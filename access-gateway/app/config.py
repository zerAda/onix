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

    # --- Anti-spoof X-OIDC-Claims : preuve de transit par le proxy de confiance ---
    # La passerelle ne fait JAMAIS confiance à X-OIDC-Claims verbatim : un client
    # atteignant la gateway directement pourrait sinon forger {"oid":...,"groups":[...]}
    # et usurper une identité (bypass RBAC total). Le reverse-proxy/IdP de confiance
    # injecte donc un secret partagé dans l'en-tête X-OIDC-Proxy-Secret ; la gateway
    # le compare en TEMPS CONSTANT (hmac.compare_digest) à cette valeur.
    # Secret configuré + header absent/incorrect => IdentityError (refus).
    proxy_shared_secret: str
    # FAIL-CLOSED : si proxy_shared_secret est VIDE, la gateway REFUSE par défaut de
    # faire confiance à X-OIDC-Claims (aucune preuve proxy possible). Cet override
    # (GATEWAY_ALLOW_UNAUTHENTICATED_HEADER=true) lève le refus — RÉSERVÉ AU DEV/TEST
    # local (jamais en prod : il rouvre la faille d'usurpation).
    allow_unauth_header: bool

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

    # ── Tier SÉMANTIQUE du cache (embedding + seuil) — cf. docs/CACHE.md §13 ──
    # OPT-IN strict : désactivé par défaut. Un cache sémantique sur un corpus
    # FACTUEL est un RISQUE DE PRÉCISION (servir la réponse de la question A
    # pour une question B « proche »). On ne l'active qu'en connaissance de
    # cause, avec un seuil élevé ET le garde anti-divergence numérique/entités.
    semantic_cache_enabled: bool
    # URL de l'API d'embeddings Ollama (endpoint legacy /api/embeddings, schéma
    # { "model", "prompt" } → { "embedding": [...] }). 100 % local/souverain.
    semantic_embed_url: str
    # Modèle d'embeddings (déjà pull : nomic-embed-text). Doit être DÉTERMINISTE
    # (sinon deux embeddings de la même question divergent et cassent le seuil).
    semantic_embed_model: str
    # Seuil de similarité COSINUS minimal pour un hit sémantique. Élevé par
    # défaut (0.95) : on préfère un miss (recalcul) à un faux positif (mauvaise
    # réponse servie). En-dessous du seuil → miss. Au-dessus MAIS divergence
    # numérique/entité détectée → REJET (cf. cache._has_factual_divergence).
    semantic_threshold: float
    # Bornage de l'index sémantique PAR PÉRIMÈTRE (réutilise la borne LRU du
    # cache exact si non surchargé). Empêche une croissance mémoire non bornée.
    semantic_max_entries: int

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

    # ── Microsoft Fabric / OneLake / Power BI (cf. app/fabric_client.py) ──
    # Module d'accès Fabric : énumération workspaces/items, RBAC de contrôle
    # (roleAssignments), lecture OneLake (données ADLS Gen2) et datasets Power BI.
    # Réutilise par DÉFAUT les identifiants Entra de Graph (même SPN) : on ne
    # duplique pas un secret. Des overrides dédiés existent si le SPN Fabric
    # diffère du SPN Graph. Défauts INERTES : si Fabric non configuré (pas de
    # tenant/client/secret), `fabric_configured` est False → aucun appel (comme
    # Graph non configuré).
    fabric_tenant_id: str
    fabric_client_id: str
    fabric_client_secret: str
    fabric_authority: str  # ex. https://login.microsoftonline.com
    # Hôtes des trois surfaces (constants d'exploitation, pas d'entrée utilisateur).
    fabric_api_host: str  # ex. https://api.fabric.microsoft.com
    onelake_host: str  # ex. https://onelake.dfs.fabric.microsoft.com
    powerbi_host: str  # ex. https://api.powerbi.com
    # Identifiants par défaut OPTIONNELS (workspace/item ciblés par une intégration
    # ultérieure ; vides = non câblé, l'appelant fournit les ids explicitement).
    fabric_workspace_id: str
    fabric_item_id: str

    # ── Périmètre GOLD — LECTURE SEULE, tables gold uniquement (cf. fabric_client) ──
    # Fabric chez onix est volontairement restreint : on ne lit QUE la couche
    # « gold » (données nettoyées/agrégées, exposables) d'UN lakehouse précis dans
    # UN workspace précis. Tout chemin OneLake hors de
    # ``{gold_lakehouse}.Lakehouse/{gold_tables_prefix}/...`` ou hors du
    # ``gold_workspace`` est REFUSÉ (fail-closed, cf. fabric_client.is_gold_path).
    # Défauts INERTES : si le gold n'est pas configuré (workspace + lakehouse),
    # `gold_configured` est False → AUCUN accès OneLake n'est accordé.
    # Le workspace/lakehouse acceptent l'id (GUID) ET le nom (l'API OneLake gère
    # les deux) : on renseigne celui dont on dispose (les deux idéalement).
    fabric_gold_workspace_id: str
    fabric_gold_workspace_name: str
    fabric_gold_lakehouse_id: str
    fabric_gold_lakehouse_name: str
    # Type d'item du lakehouse gold (toujours "Lakehouse" en pratique ; surchargeable
    # pour rester explicite — un Warehouse a un autre type/chemin).
    fabric_gold_lakehouse_type: str
    # Préfixe de chemin des tables gold sous le lakehouse. Défaut "Tables" (racine
    # managée des tables Delta) ; surchargeable en "Tables/gold" ou un schéma
    # ("Tables/gold" pour le schéma `gold`). On REFUSE tout chemin hors de ce
    # préfixe (ex. "Files/..." = données brutes non exposables).
    fabric_gold_tables_prefix: str

    # ── Fournisseur de jeton via Azure CLI (`az`) — zéro secret en repo ──
    # Si True, le client Fabric acquiert ses jetons via ``az account get-access-token``
    # (l'identité vient de `az login`, pas d'un client_secret). Utile en e2e LIVE
    # sur un poste az-connecté. Défaut False (la passerelle en service utilise le
    # SPN client-credentials). Cf. fabric_client.acquire_token_via_azcli.
    fabric_use_azcli: bool

    @property
    def graph_configured(self) -> bool:
        return bool(self.graph_tenant_id and self.graph_client_id and self.graph_client_secret)

    @property
    def fabric_configured(self) -> bool:
        """Fabric est exploitable si un SPN (tenant/client/secret) est disponible
        — soit dédié Fabric, soit hérité de Graph (mêmes identifiants Entra) — OU
        si l'auth `az` est activée (l'identité vient alors de `az login`)."""
        if self.fabric_use_azcli:
            return True
        return bool(
            self.fabric_tenant_id and self.fabric_client_id and self.fabric_client_secret
        )

    @property
    def fabric_gold_configured(self) -> bool:
        """Le périmètre gold est exploitable si on connaît AU MOINS un identifiant
        du workspace gold ET un identifiant du lakehouse gold (id OU nom). Sans
        cela, aucun accès OneLake n'est accordé (défaut INERTE, fail-closed)."""
        ws = bool(self.fabric_gold_workspace_id or self.fabric_gold_workspace_name)
        lh = bool(self.fabric_gold_lakehouse_id or self.fabric_gold_lakehouse_name)
        return ws and lh


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    claims = os.environ.get("GATEWAY_OIDC_GROUP_CLAIMS", "groups,roles")
    oidc_group_claims = tuple(c.strip() for c in claims.split(",") if c.strip())
    # Identifiants Entra de Graph (réutilisés par défaut pour Fabric : même SPN).
    graph_tenant_id = os.environ.get("GATEWAY_GRAPH_TENANT_ID", "").strip()
    graph_client_id = os.environ.get("GATEWAY_GRAPH_CLIENT_ID", "").strip()
    graph_client_secret = os.environ.get("GATEWAY_GRAPH_CLIENT_SECRET", "").strip()
    graph_authority = os.environ.get(
        "GATEWAY_GRAPH_AUTHORITY", "https://login.microsoftonline.com"
    ).rstrip("/")
    return Settings(
        onyx_base_url=os.environ.get("GATEWAY_ONYX_BASE_URL", "http://api_server:8080").rstrip("/"),
        onyx_api_key=os.environ.get("GATEWAY_ONYX_API_KEY", "").strip(),
        group_source=os.environ.get("GATEWAY_GROUP_SOURCE", "auto").strip().lower(),
        # Anti-spoof X-OIDC-Claims : secret partagé prouvant le transit par le proxy
        # de confiance (cf. identity.resolve_principal). Vide => fail-closed (refus)
        # sauf override dev explicite GATEWAY_ALLOW_UNAUTHENTICATED_HEADER=true.
        proxy_shared_secret=os.environ.get("GATEWAY_PROXY_SHARED_SECRET", "").strip(),
        allow_unauth_header=_bool("GATEWAY_ALLOW_UNAUTHENTICATED_HEADER", False),
        graph_tenant_id=graph_tenant_id,
        graph_client_id=graph_client_id,
        graph_client_secret=graph_client_secret,
        graph_host=os.environ.get("GATEWAY_GRAPH_HOST", "https://graph.microsoft.com").rstrip("/"),
        graph_authority=graph_authority,
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
        # Tier sémantique du cache (cf. app/cache.py SemanticIndex + docs/CACHE.md §13).
        # OPT-IN : false par défaut (risque de précision documenté honnêtement).
        semantic_cache_enabled=_bool("GATEWAY_SEMANTIC_CACHE_ENABLED", False),
        semantic_embed_url=os.environ.get(
            "GATEWAY_SEMANTIC_EMBED_URL", "http://ollama:11434/api/embeddings"
        ).strip(),
        semantic_embed_model=os.environ.get(
            "GATEWAY_SEMANTIC_EMBED_MODEL", "nomic-embed-text"
        ).strip()
        or "nomic-embed-text",
        # Seuil COSINUS élevé (0.95) : faux positif >> miss en coût métier.
        semantic_threshold=float(os.environ.get("GATEWAY_SEMANTIC_THRESHOLD", "0.95")),
        # Par défaut, on aligne la borne de l'index sémantique sur la LRU exacte.
        semantic_max_entries=int(
            os.environ.get(
                "GATEWAY_SEMANTIC_MAX_ENTRIES",
                os.environ.get("GATEWAY_CACHE_MAX_ENTRIES", "512"),
            )
        ),
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
        # Microsoft Fabric / OneLake / Power BI (cf. app/fabric_client.py).
        # Identifiants : override dédié SINON repli sur le SPN Graph (même Entra).
        fabric_tenant_id=os.environ.get("GATEWAY_FABRIC_TENANT_ID", "").strip() or graph_tenant_id,
        fabric_client_id=os.environ.get("GATEWAY_FABRIC_CLIENT_ID", "").strip() or graph_client_id,
        fabric_client_secret=(
            os.environ.get("GATEWAY_FABRIC_CLIENT_SECRET", "").strip() or graph_client_secret
        ),
        fabric_authority=os.environ.get("GATEWAY_FABRIC_AUTHORITY", "").strip().rstrip("/")
        or graph_authority,
        fabric_api_host=os.environ.get(
            "GATEWAY_FABRIC_API_HOST", "https://api.fabric.microsoft.com"
        ).rstrip("/"),
        onelake_host=os.environ.get(
            "GATEWAY_ONELAKE_HOST", "https://onelake.dfs.fabric.microsoft.com"
        ).rstrip("/"),
        powerbi_host=os.environ.get(
            "GATEWAY_POWERBI_HOST", "https://api.powerbi.com"
        ).rstrip("/"),
        fabric_workspace_id=os.environ.get("GATEWAY_FABRIC_WORKSPACE_ID", "").strip(),
        fabric_item_id=os.environ.get("GATEWAY_FABRIC_ITEM_ID", "").strip(),
        # Périmètre GOLD (lecture seule, tables gold uniquement). Défauts INERTES :
        # tout vide → `fabric_gold_configured` False → aucun accès OneLake.
        fabric_gold_workspace_id=os.environ.get(
            "GATEWAY_FABRIC_GOLD_WORKSPACE_ID", ""
        ).strip(),
        fabric_gold_workspace_name=os.environ.get(
            "GATEWAY_FABRIC_GOLD_WORKSPACE_NAME", ""
        ).strip(),
        fabric_gold_lakehouse_id=os.environ.get(
            "GATEWAY_FABRIC_GOLD_LAKEHOUSE_ID", ""
        ).strip(),
        fabric_gold_lakehouse_name=os.environ.get(
            "GATEWAY_FABRIC_GOLD_LAKEHOUSE_NAME", ""
        ).strip(),
        fabric_gold_lakehouse_type=os.environ.get(
            "GATEWAY_FABRIC_GOLD_LAKEHOUSE_TYPE", "Lakehouse"
        ).strip()
        or "Lakehouse",
        fabric_gold_tables_prefix=os.environ.get(
            "GATEWAY_FABRIC_GOLD_TABLES_PREFIX", "Tables"
        ).strip()
        or "Tables",
        # Auth via Azure CLI (`az`) : zéro secret, identité de `az login`.
        fabric_use_azcli=_bool("GATEWAY_FABRIC_USE_AZCLI", False),
    )


def reset_settings_cache() -> None:
    """Pour les tests : force la relecture des variables d'environnement."""
    get_settings.cache_clear()
