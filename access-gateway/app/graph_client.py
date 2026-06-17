"""graph_client — client Microsoft Graph minimal (app-only) pour l'appartenance
transitive aux groupes.

Endpoint : GET /v1.0/users/{id}/transitiveMemberOf/microsoft.graph.group
           ?$select=id,displayName&$top=999
En-tête   : ConsistencyLevel: eventual (requis avec l'OData cast / advanced query).
Pagination: suit @odata.nextLink jusqu'à épuisement.

Permission Graph (moindre privilège, APPLICATION) : **GroupMember.Read.All**
(suffisant pour lister l'appartenance d'un autre utilisateur ; User.Read.All
fonctionne aussi mais est plus large). On ne demande JAMAIS Directory.Read.All si
ce n'est pas nécessaire. Réf. docs/RBAC.md.

Le jeton est obtenu par *client credentials* (scope `<graph_host>/.default`). La
fonction d'acquisition est injectable pour les tests (aucun appel réseau réel).
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

import httpx

from .config import Settings

logger = logging.getLogger("onix.gateway.graph")

# Type d'un fournisseur de jeton (injectable). Renvoie un access_token brut.
TokenProvider = Callable[[], Awaitable[str]]


class GraphError(RuntimeError):
    """Erreur d'appel Graph (jeton ou requête)."""


async def acquire_app_token(settings: Settings, client: httpx.AsyncClient) -> str:
    """Acquiert un jeton app-only (client credentials). Lève GraphError si KO."""
    if not settings.graph_configured:
        raise GraphError("Microsoft Graph non configuré (tenant/client/secret manquants).")
    token_url = f"{settings.graph_authority}/{settings.graph_tenant_id}/oauth2/v2.0/token"
    data = {
        "client_id": settings.graph_client_id,
        "client_secret": settings.graph_client_secret,
        "scope": f"{settings.graph_host}/.default",
        "grant_type": "client_credentials",
    }
    resp = await client.post(token_url, data=data)
    if resp.status_code != 200:
        # Ne jamais logguer le corps (peut contenir des détails sensibles).
        raise GraphError(f"Échec d'acquisition du jeton Graph (HTTP {resp.status_code}).")
    token = resp.json().get("access_token")
    if not token:
        raise GraphError("Réponse de jeton Graph sans access_token.")
    return token


async def fetch_transitive_group_ids(
    user_id: str,
    settings: Settings,
    *,
    client: Optional[httpx.AsyncClient] = None,
    token_provider: Optional[TokenProvider] = None,
) -> list[str]:
    """Renvoie la liste des identifiants de groupes (GUID `id`) dont `user_id` est
    membre directement OU transitivement.

    On `$select=id,displayName` ; on retourne les `id` (GUID) — clé canonique du
    mapping — en complétant par `displayName` quand `id` manque (objets à info
    limitée). Suit la pagination `@odata.nextLink`.
    """
    if not user_id:
        raise GraphError("user_id requis pour interroger Graph.")

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(15.0))
    try:
        provider: TokenProvider = token_provider or (lambda: acquire_app_token(settings, client))
        token = await provider()
        headers = {
            "Authorization": f"Bearer {token}",
            "ConsistencyLevel": "eventual",
            "Accept": "application/json",
        }
        url: Optional[str] = (
            f"{settings.graph_host}/v1.0/users/{user_id}"
            "/transitiveMemberOf/microsoft.graph.group"
            "?$select=id,displayName&$top=999"
        )
        group_ids: list[str] = []
        pages = 0
        while url:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                raise GraphError(
                    f"Échec transitiveMemberOf (HTTP {resp.status_code}) pour l'utilisateur."
                )
            body = resp.json()
            for item in body.get("value", []):
                gid = item.get("id") or item.get("displayName")
                if gid:
                    group_ids.append(str(gid))
            url = body.get("@odata.nextLink")
            pages += 1
            if pages > 1000:  # garde-fou anti-boucle
                logger.warning("Pagination Graph anormalement longue : interruption.")
                break
        # Dédup en conservant l'ordre.
        return list(dict.fromkeys(group_ids))
    finally:
        if owns_client:
            await client.aclose()
