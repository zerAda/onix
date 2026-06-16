"""config — réglages de la passerelle (12-factor : tout par variable d'env).

Aucun secret en dur. Le client_secret Graph et la clé API du proxy sont lus
exclusivement dans l'environnement (injectés via `.env` gitignoré / coffre).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # --- Onyx amont (cible du proxy) ---
    onyx_base_url: str
    onyx_api_key: str  # clé d'API Onyx (peut être vide si auth par cookie de session)

    # --- Source des groupes Entra ---
    # "claims"  : lit les groupes dans les claims OIDC (header X-OIDC-Claims, JSON)
    # "graph"   : interroge Microsoft Graph transitiveMemberOf (app-only)
    # "auto"    : claims si présents, sinon bascule sur Graph (gère l'overage OIDC)
    group_source: str

    # --- Microsoft Graph (mode graph/auto) ---
    graph_tenant_id: str
    graph_client_id: str
    graph_client_secret: str
    graph_host: str  # ex. https://graph.microsoft.com (souverain : Gov/China possibles)
    graph_authority: str  # ex. https://login.microsoftonline.com

    # --- Mapping groupe -> Document Set ---
    mapping_path: str  # chemin du JSON de mapping (objet OU forme structurée)

    # --- Politique ---
    # Si True : un utilisateur sans aucun groupe mappé est REFUSÉ (deny-by-default).
    deny_if_no_match: bool
    # Claims OIDC : noms de claims contenant les identifiants de groupe (ordre = priorité).
    oidc_group_claims: tuple[str, ...]
    # Cache TTL (secondes) des groupes résolus par utilisateur (0 = pas de cache).
    group_cache_ttl: int

    @property
    def graph_configured(self) -> bool:
        return bool(self.graph_tenant_id and self.graph_client_id and self.graph_client_secret)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    claims = os.environ.get("GATEWAY_OIDC_GROUP_CLAIMS", "groups,roles")
    oidc_group_claims = tuple(c.strip() for c in claims.split(",") if c.strip())
    return Settings(
        onyx_base_url=os.environ.get("GATEWAY_ONYX_BASE_URL", "http://api_server:8080").rstrip("/"),
        onyx_api_key=os.environ.get("GATEWAY_ONYX_API_KEY", "").strip(),
        group_source=os.environ.get("GATEWAY_GROUP_SOURCE", "auto").strip().lower(),
        graph_tenant_id=os.environ.get("GATEWAY_GRAPH_TENANT_ID", "").strip(),
        graph_client_id=os.environ.get("GATEWAY_GRAPH_CLIENT_ID", "").strip(),
        graph_client_secret=os.environ.get("GATEWAY_GRAPH_CLIENT_SECRET", "").strip(),
        graph_host=os.environ.get("GATEWAY_GRAPH_HOST", "https://graph.microsoft.com").rstrip("/"),
        graph_authority=os.environ.get(
            "GATEWAY_GRAPH_AUTHORITY", "https://login.microsoftonline.com"
        ).rstrip("/"),
        mapping_path=os.environ.get("GATEWAY_MAPPING_PATH", "/config/group_map.json"),
        deny_if_no_match=_bool("GATEWAY_DENY_IF_NO_MATCH", True),
        oidc_group_claims=oidc_group_claims,
        group_cache_ttl=int(os.environ.get("GATEWAY_GROUP_CACHE_TTL", "300")),
    )


def reset_settings_cache() -> None:
    """Pour les tests : force la relecture des variables d'environnement."""
    get_settings.cache_clear()
