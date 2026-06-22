"""identity — résout l'identité et les GROUPES Entra de l'appelant.

Deux sources, sélectionnées par GATEWAY_GROUP_SOURCE :

* "claims" : lit les groupes dans les claims OIDC. La passerelle s'attend à ce
  que le reverse-proxy/IdP en amont injecte les claims vérifiés dans l'en-tête
  `X-OIDC-Claims` (JSON). Les claims de groupe (par défaut `groups`, puis `roles`)
  contiennent des GUID de groupe (ou des noms de rôles d'app).
  ⚠ Si Entra dépasse la limite de taille du token (≈200 groupes JWT), il N'inclut
  PAS la liste mais un claim d'**overage** (`_claim_names` / `hasgroups`) imposant
  un repli sur Microsoft Graph.

* "graph" : interroge Microsoft Graph `transitiveMemberOf` (app-only) à partir de
  l'`oid` (objectId) ou de l'UPN de l'utilisateur.

* "auto" : claims si une liste exploitable est présente ; sinon (absente OU
  overage) bascule automatiquement sur Graph. C'est le mode recommandé.

La comparaison/identité repose sur `oid` (objectId Entra, stable) sinon `sub`/UPN.
"""
from __future__ import annotations

import hmac as _hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from .config import Settings
from .graph_client import GraphError, fetch_transitive_group_ids

logger = logging.getLogger("onix.gateway.identity")


@dataclass(frozen=True)
class Principal:
    """Identité résolue de l'appelant."""

    user_id: str  # oid (objectId) de préférence, sinon UPN/sub
    upn: Optional[str]
    group_ids: list[str]
    source: str  # "claims" | "graph" — d'où viennent les groupes


class IdentityError(RuntimeError):
    """Impossible d'identifier l'appelant (claims manquants/incohérents)."""


def parse_oidc_claims(raw_header: Optional[str]) -> dict:
    """Parse l'en-tête X-OIDC-Claims (JSON). Vide/invalide => {} (pas d'exception
    : l'absence d'identité est gérée plus haut comme un refus, pas un crash)."""
    if not raw_header:
        return {}
    try:
        data = json.loads(raw_header)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        logger.warning("X-OIDC-Claims illisible (JSON invalide) — ignoré.")
        return {}


def _user_id_from_claims(claims: dict) -> tuple[str, Optional[str]]:
    upn = claims.get("upn") or claims.get("preferred_username") or claims.get("email")
    user_id = claims.get("oid") or claims.get("sub") or upn
    if not user_id:
        raise IdentityError("Aucun identifiant utilisateur dans les claims (oid/sub/upn).")
    return str(user_id), (str(upn) if upn else None)


def _has_overage(claims: dict) -> bool:
    """Détecte l'overage de groupes (Entra a tronqué la liste)."""
    # JWT : claim "hasgroups": true, OU "_claim_names"/"_claim_sources" pointant
    # vers l'API Graph pour 'groups'.
    if claims.get("hasgroups") is True:
        return True
    claim_names = claims.get("_claim_names")
    if isinstance(claim_names, dict) and "groups" in claim_names:
        return True
    return False


def _groups_from_claims(claims: dict, claim_keys: tuple[str, ...]) -> Optional[list[str]]:
    """Extrait une liste de groupes exploitable des claims, ou None si absente.

    None signifie « pas de liste utilisable » (donc, en mode auto, repli Graph).
    Une liste VIDE explicite est, elle, retournée telle quelle ([])."""
    for key in claim_keys:
        if key in claims:
            val = claims[key]
            if isinstance(val, list):
                return [str(g).strip() for g in val if str(g).strip()]
            if isinstance(val, str) and val.strip():
                # Certains IdP émettent une chaîne séparée par des espaces.
                return [g for g in val.split() if g]
    return None


class _TTLCache:
    """Cache mémoire minimal {user_id: (expiry, group_ids)}. Process-local."""

    def __init__(self, ttl: int) -> None:
        self.ttl = ttl
        self._store: dict[str, tuple[float, list[str]]] = {}

    def get(self, key: str) -> Optional[list[str]]:
        if self.ttl <= 0:
            return None
        entry = self._store.get(key)
        if not entry:
            return None
        expiry, value = entry
        if expiry < time.monotonic():
            self._store.pop(key, None)
            return None
        return list(value)

    def set(self, key: str, value: list[str]) -> None:
        if self.ttl <= 0:
            return
        self._store[key] = (time.monotonic() + self.ttl, list(value))

    def clear(self) -> None:
        self._store.clear()


def _require_proxy_proof(settings: Settings, proxy_secret_header: Optional[str]) -> None:
    """ANTI-SPOOF (fail-closed) : prouve que la requête a transité par le proxy de
    confiance AVANT de faire confiance au moindre claim de X-OIDC-Claims.

    Sans cette garde, un client atteignant la gateway en direct pourrait forger
    `{"oid":"...","groups":["<guid-admin>"]}` → usurpation d'identité + bypass RBAC
    total. Le proxy de confiance ajoute `X-OIDC-Proxy-Secret: <secret>` ; on le
    compare en TEMPS CONSTANT (hmac.compare_digest, anti-timing) au secret partagé.

    * Secret configuré : header absent OU incorrect → IdentityError (refus).
    * Secret NON configuré : refus aussi (aucune preuve possible), SAUF override
      dev explicite GATEWAY_ALLOW_UNAUTHENTICATED_HEADER=true.
    """
    expected = (settings.proxy_shared_secret or "").strip()
    if expected:
        presented = (proxy_secret_header or "").strip()
        if not presented or not _hmac.compare_digest(presented, expected):
            raise IdentityError(
                "Preuve proxy invalide/absente : X-OIDC-Claims rejeté (anti-spoof). "
                "La requête n'a pas transité par le reverse-proxy de confiance."
            )
    elif not settings.allow_unauth_header:
        raise IdentityError(
            "GATEWAY_PROXY_SHARED_SECRET non configuré : refus de faire confiance à "
            "X-OIDC-Claims (fail-closed, anti-spoof). Définir le secret partagé proxy, "
            "ou GATEWAY_ALLOW_UNAUTHENTICATED_HEADER=true en dev/test UNIQUEMENT."
        )


async def resolve_principal(
    settings: Settings,
    *,
    oidc_claims_header: Optional[str],
    proxy_secret_header: Optional[str] = None,
    cache: Optional[_TTLCache] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Principal:
    """Résout l'identité + groupes selon GATEWAY_GROUP_SOURCE.

    Lève IdentityError si l'identité est inconnue OU si la preuve de transit par le
    proxy de confiance (X-OIDC-Proxy-Secret) est absente/invalide (anti-spoof),
    GraphError si l'appel Graph échoue alors qu'il est requis.
    """
    # Anti-spoof EN PREMIER : on rejette avant même de parser/croire les claims.
    _require_proxy_proof(settings, proxy_secret_header)
    claims = parse_oidc_claims(oidc_claims_header)
    if not claims:
        raise IdentityError("Identité absente : en-tête X-OIDC-Claims requis (SSO OIDC).")
    user_id, upn = _user_id_from_claims(claims)
    source_cfg = settings.group_source

    # Cache (clé = user_id).
    if cache is not None:
        cached = cache.get(user_id)
        if cached is not None:
            return Principal(user_id=user_id, upn=upn, group_ids=cached, source="cache")

    group_ids: list[str]
    used_source: str

    if source_cfg == "claims":
        from_claims = _groups_from_claims(claims, settings.oidc_group_claims)
        if from_claims is None:
            if _has_overage(claims):
                raise IdentityError(
                    "Overage de groupes OIDC : la liste dépasse la limite du token. "
                    "Configurez GATEWAY_GROUP_SOURCE=auto (repli Graph)."
                )
            from_claims = []
        group_ids, used_source = from_claims, "claims"

    elif source_cfg == "graph":
        group_ids = await fetch_transitive_group_ids(user_id, settings, client=http_client)
        used_source = "graph"

    else:  # "auto"
        from_claims = _groups_from_claims(claims, settings.oidc_group_claims)
        if from_claims is not None and not _has_overage(claims):
            group_ids, used_source = from_claims, "claims"
        else:
            # Liste absente OU overage -> Graph (si configuré).
            if not settings.graph_configured:
                raise GraphError(
                    "Repli Graph requis (claims de groupe absents/overage) mais Graph "
                    "non configuré. Renseignez GATEWAY_GRAPH_* ou émettez le claim 'groups'."
                )
            group_ids = await fetch_transitive_group_ids(user_id, settings, client=http_client)
            used_source = "graph"

    if cache is not None:
        cache.set(user_id, group_ids)
    return Principal(user_id=user_id, upn=upn, group_ids=group_ids, source=used_source)
