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
    # Post-filtre garde-fous (couche 3) appliqué sur la réponse de l'assistant.
    # True par défaut : c'est un contrôle de sécurité DÉPLOYÉ (fail-safe). On ne
    # l'expose désactivable que pour le diagnostic ; en prod on le laisse actif.
    guardrail_enabled: bool
    # Timeout (s) du relais HTTP vers l'amont Onyx. Un amont qui génère via LLM
    # peut être lent (CPU) ; configurable pour ne pas couper une génération longue.
    upstream_timeout: float
    # Endpoint Prometheus GET /metrics (observabilité qualité + ops). Activé par
    # défaut (réseau interne — pas d'auth requis). Désactiver uniquement pour les
    # environnements sans stack de monitoring (GET /metrics → 404, aucun compteur).
    metrics_enabled: bool

    # --- Cache applicatif RBAC-safe (réponses Onyx, post-filtre garde-fous) ---
    # Cf. docs/CACHE.md. Le cache est une couche DÉTERMINISTE au-dessus du
    # KV-cache token-level interne d'Ollama : il évite tout aller-retour LLM
    # quand une question identique dans le MÊME périmètre Document Set
    # autorisé est posée à nouveau. La clé HMAC inclut le périmètre trié →
    # un utilisateur d'un autre périmètre ne peut PAS récupérer la réponse.
    cache_enabled: bool
    # URL Redis (ex. redis://cache:6379/0). Vide → bascule sur LRU mémoire.
    cache_redis_url: str
    # TTL d'une entrée (secondes). Au-delà, l'entrée est ré-évaluée par Onyx.
    cache_ttl_seconds: int
    # Bornage de la LRU en mémoire (entrées max).
    cache_max_entries: int
    # Secret HMAC (REQUIS quand cache_enabled=true). N'est JAMAIS autogénéré ;
    # un sel éphémère casserait la stabilité des clés entre redémarrages, donc
    # le hit-rate. Fail-loud à l'init si manquant (cf. cache.build_cache).
    cache_hmac_secret: str
    # Locale incluse dans la clé (différencie versions FR/EN d'une même question).
    cache_locale: str

    # ── Filtre ACL par DOCUMENT appliqué sur la RÉPONSE (FOSS, voir doc_acl.py) ──
    # Active le retrait des citations vers les documents non autorisés
    # individuellement pour l'appelant. C'est un filtre de SORTIE — il
    # n'empêche pas que le LLM ait potentiellement vu le contenu pendant la
    # génération (limite assumée, cf. docs/RBAC.md § « Per-Document Filter »).
    doc_acl_enabled: bool
    # Chemin du fichier ACL JSON (objet doc_id → {groups, users}). Fichier
    # absent ⇒ ACL vide (donc deny-by-default total si default_policy=deny).
    doc_acl_path: str
    # Politique par défaut pour un doc_id NON listé dans l'ACL : "deny" (par
    # défaut, cohérent avec la posture deny-by-default de la passerelle) ou
    # "allow" (réservé aux POCs / corpus historique sans ACL fine).
    doc_acl_default_policy: str
    # Si True ET que toutes les citations sont retirées par le filtre, on
    # SUBSTITUE le texte de l'assistant par un refus sûr
    # (`REFUSAL_NO_ACCESSIBLE_SOURCE`). Désactiver uniquement pour le diag.
    doc_acl_strip_uncited: bool

    # ── Streaming SSE (cf. app/streaming.py + docs/STREAMING.md) ──
    # Active le relais token-par-token devant Onyx (latence perçue ÷10 sur CPU)
    # tout en conservant les garde-fous (garde DUR incrémental + override final)
    # et le filtre ACL par-document sur le paquet citations. True par défaut.
    stream_enabled: bool
    # Délai d'inactivité (secondes) toléré entre deux paquets amont avant de
    # considérer le flux comme bloqué (à câbler côté `httpx.stream`/read timeout
    # par l'orchestrateur). Un LLM CPU peut être lent : valeur généreuse.
    stream_idle_timeout: float

    # ── ACL par DOCUMENT auto-dérivée de SharePoint via Graph (cf. graph_acl.py) ──
    # Active la source d'ACL VIVANTE : la passerelle lit les permissions par item
    # SharePoint (Microsoft Graph) et OR-merge cette ACL avec le `doc_acl.json`
    # statique (CompositeDocACL). Reste un filtre de SORTIE (cf. docs/RBAC.md §4.4).
    # Désactivé par défaut (opt-in : nécessite Graph configuré + un mapping).
    doc_acl_graph_enabled: bool
    # Chemin du mapping JSON { doc_id: {site_id, drive_id, item_id} } reliant un
    # doc Onyx à son item SharePoint (cf. docs/connectors/SHAREPOINT.md). Requis
    # si doc_acl_graph_enabled=true.
    doc_acl_mapping_path: str
    # TTL (s) de l'ACL Graph en mémoire : au-delà, elle est re-synchronisée
    # (rafraîchi périodique). 0 ⇒ figée après le 1er build (pas de re-sync auto).
    doc_acl_refresh_seconds: int

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
        guardrail_enabled=_bool("GATEWAY_GUARDRAIL_ENABLED", True),
        upstream_timeout=float(os.environ.get("GATEWAY_UPSTREAM_TIMEOUT", "30")),
        metrics_enabled=_bool("GATEWAY_METRICS_ENABLED", True),
        # Cache applicatif RBAC-safe (cf. app/cache.py + docs/CACHE.md).
        cache_enabled=_bool("GATEWAY_CACHE_ENABLED", True),
        cache_redis_url=os.environ.get("GATEWAY_CACHE_REDIS_URL", "").strip(),
        cache_ttl_seconds=int(os.environ.get("GATEWAY_CACHE_TTL_SECONDS", "3600")),
        cache_max_entries=int(os.environ.get("GATEWAY_CACHE_MAX_ENTRIES", "512")),
        cache_hmac_secret=os.environ.get("GATEWAY_CACHE_HMAC_SECRET", "").strip(),
        cache_locale=os.environ.get("GATEWAY_CACHE_LOCALE", "fr").strip().lower() or "fr",
        # Filtre ACL par document (cf. app/doc_acl.py + docs/RBAC.md).
        doc_acl_enabled=_bool("GATEWAY_DOC_ACL_ENABLED", True),
        doc_acl_path=os.environ.get("GATEWAY_DOC_ACL_PATH", "config/doc_acl.json").strip(),
        doc_acl_default_policy=(
            os.environ.get("GATEWAY_DOC_ACL_DEFAULT_POLICY", "deny").strip().lower() or "deny"
        ),
        doc_acl_strip_uncited=_bool("GATEWAY_DOC_ACL_STRIP_UNCITED", True),
        # Streaming SSE (cf. app/streaming.py + docs/STREAMING.md).
        stream_enabled=_bool("GATEWAY_STREAM_ENABLED", True),
        stream_idle_timeout=float(os.environ.get("GATEWAY_STREAM_IDLE_TIMEOUT", "60")),
        # ACL par document auto-dérivée de SharePoint via Graph (cf. app/graph_acl.py).
        doc_acl_graph_enabled=_bool("GATEWAY_DOC_ACL_GRAPH_ENABLED", False),
        doc_acl_mapping_path=os.environ.get(
            "GATEWAY_DOC_ACL_MAPPING_PATH", "config/doc_acl_mapping.json"
        ).strip(),
        doc_acl_refresh_seconds=int(os.environ.get("GATEWAY_DOC_ACL_REFRESH_SECONDS", "900")),
    )


def reset_settings_cache() -> None:
    """Pour les tests : force la relecture des variables d'environnement."""
    get_settings.cache_clear()
