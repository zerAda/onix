# External Integrations

**Analysis Date:** 2026-06-19

## APIs & External Services

**Microsoft Graph (SharePoint RBAC & Entra ID Groups):**
- Service: Microsoft Graph API (transitive group membership, SharePoint site/drive permissions)
  - What it's used for: Resolve user groups → Document Set authorization, per-document ACL
  - SDK/Client: `httpx` via `access-gateway/app/graph_client.py`
  - Auth: Service Principal (client credentials flow, app-only)
    - `GATEWAY_GRAPH_CLIENT_ID` - Service principal ID
    - `GATEWAY_GRAPH_CLIENT_SECRET` - Service principal secret
    - `GATEWAY_GRAPH_TENANT_ID` - Entra tenant ID
  - Host: `GATEWAY_GRAPH_HOST` (default: `https://graph.microsoft.com` — Gov/China sovereign variants supported)
  - Authority: `GATEWAY_GRAPH_AUTHORITY` (default: `https://login.microsoftonline.com`)
  - Scopes: Not delegated (client credentials = application permissions only)
  - Implementation: `access-gateway/app/graph_client.py` — token acquisition, pagination (via JSON `continuationToken`), error handling
  - ACL logic: `access-gateway/app/graph_acl.py` — fail-closed RBAC decision on SharePoint sites/documents
  - Setup: `scripts/setup-sharepoint-app.sh` — idempotent app creation, Graph API permissions, consent

**Microsoft Fabric (NEW):**
- Service: Microsoft Fabric API + OneLake (data lake) + Power BI datasets (added v73+)
  - What it's used for: Enumerate Fabric workspaces, check RBAC roles (Viewer/Contributor/Member/Admin), list OneLake items, verify principal access to gold-tier data
  - SDK/Client: `httpx` via `access-gateway/app/fabric_client.py`
  - Auth: Service Principal (client credentials, 3 separate audiences/tokens):
    - Fabric Control: `https://api.fabric.microsoft.com/.default`
    - OneLake Data (ADLS Gen2): `https://storage.azure.com/.default`
    - Power BI: `https://analysis.windows.net/powerbi/api/.default`
  - Implementation: `access-gateway/app/fabric_client.py` — modular token provider (injectable for testing), read-only by design
  - ACL logic: `access-gateway/app/fabric_acl.py` — Fabric workspace role-based authorization + OneLake principal access (preview), fail-closed
  - Scope Constraint: **GOLD tier only** (confined lakehouse read path; OneLake `Tables/gold` subtree via `is_gold_path` function)
  - Pagination: Respects each API's form:
    - Fabric REST: `continuationToken` / `continuationUri` in JSON body
    - OData (Power BI legacy): `@odata.nextLink`
    - ADLS Gen2 DFS: `x-ms-continuation` response header → `continuation=` query param
  - Setup: `scripts/setup-fabric-app.sh` — app creation, Graph permissions (Sites.Read.All, Files.Read.All, GroupMember.Read.All), consent, tenant settings manual (SPN enabled for Fabric APIs)
  - Constraints: SPN must have Workspace role (Viewer+) to enumerate; OneLake securityPolicy/principalAccess in PREVIEW (may be unavailable on some tenants → fail-closed)

**SharePoint Document-Level ACL (Graph Integration):**
- Service: Microsoft Graph API for per-document permissions
  - Implementation: `access-gateway/app/graph_acl.py` (same SPN as Graph above)
  - Config: ACL mapping JSON (`access-gateway/config/doc_acl_mapping.json`)
    - Format: `{ "doc_id": {"site_id": "...", "drive_id": "...", "item_id": "..."} }`
  - Sync: `make sync-doc-acl` (Python script `scripts/sync-doc-acl.py`)
    - Reads mapping + Graph creds, writes static ACL to `access-gateway/config/doc_acl.json`
    - Periodically launched (cron/CI) to propagate access changes
  - Decision: Fail-closed (any error = deny)

**Entra ID (OIDC / SSO):**
- Service: Azure AD / Entra ID identity provider
  - SDK/Client: PyJWT 2.13.0 (RS256/ES256/HS256 signature verification)
  - Auth flow: OIDC relay via reverse-proxy/SSO layer (pre-verified claims in `X-OIDC-Claims` header)
  - Fallback: Basic auth (dev/test, `AUTH_TYPE=basic`)
  - Email verification: `REQUIRE_EMAIL_VERIFICATION` (default: false, enable in prod)
  - Valid domains: `VALID_EMAIL_DOMAINS` (optional filtering for sign-up)
  - Session timeout: `SESSION_EXPIRE_TIME_SECONDS` (default: 86400s)

**Ollama (LLM Inference):**
- Service: Local LLM runtime (containerized, internal network only)
  - SDK/Client: `httpx` (OpenAI-compatible endpoint)
  - URL: `http://ollama:11434` (Docker internal network, NOT localhost)
  - Usage: onix-actions field extraction LLM, RAG eval judge (offline tests)
  - Models: Pre-pulled to `ollama_data` volume via `make models` / `scripts/pull-models.sh`
  - Env vars: `ONIX_OLLAMA_URL`, `ONIX_LLM_MODEL` (default `llama3.2:3b`)
  - No external API key; fully local/sovereign

## Data Storage

**Databases:**
- PostgreSQL 15.2-alpine
  - Connection: `postgres://user:pass@relational_db:5432/onyx` (Docker) or `postgres://...@<FQDN>.postgres.database.azure.com?sslmode=require` (Azure managed)
  - Driver: psycopg 3.3.4 (async-capable, binary included)
  - Purpose: Onyx application data (users, connectors, documents, chat history, embeddings metadata), onix-actions state (audit log, task queue, usage tracking)
  - Env vars: `POSTGRES_HOST`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DB_READONLY_USER`, `DB_READONLY_PASSWORD`
  - Migrations: Alembic (run at api_server startup: `alembic upgrade head`)
  - HA (prod): CloudNative-PG operator (K8s) or Azure Database for PostgreSQL Flexible Server (zone-redundant, TLS)

**Cache & Session Storage:**
- Redis 7.4-alpine
  - Connection: `redis://:password@cache:6379` (Docker internal) or `rediss://:key@<FQDN>.redis.cache.windows.net:6380` (Azure TLS)
  - Driver: redis 8.0.0 (actions) / redis 5.2.1 (access-gateway)
  - Databases: 
    - Base 0 (Onyx internal state, sessions, Celery task queue)
    - Base 1 (access-gateway RBAC cache, semantic question cache)
  - Purpose:
    - Onyx: Session cache, token cache, embedding cache
    - access-gateway: Semantic cache (LRU by default if `GATEWAY_CACHE_REDIS_URL` empty; upgradeable to shared Redis for multi-replica HA)
      - Cache key: HMAC(question, authorized_doc_sets, locale) — deterministic safety against user context leakage
      - TTL: `GATEWAY_CACHE_TTL_SECONDS` (default 3600s)
      - Max entries: `GATEWAY_CACHE_MAX_ENTRIES` (default 512)
  - Env vars: `REDIS_HOST`, `REDIS_PASSWORD`, `REDIS_PORT` (default 6379), `REDIS_SSL` (6380 on Azure)
  - Persistence: tmpfs (ephemeral, no disk backup in compose mode). HA prod: Redis Operator (K8s) + cluster mode, or Azure Cache for Redis Premium with persistence

**File Storage:**
- MinIO (Release 2025-07-23, S3-compatible)
  - Endpoint: `http://minio:9000` (Docker internal) or `https://minio.<namespace>.svc.cluster.local` (K8s)
  - Driver: boto3 1.43.30 (path-style addressing)
  - Purpose: Onyx document file-system, onix-actions output (.docx audit files)
  - Env vars: `S3_ENDPOINT_URL`, `S3_AWS_ACCESS_KEY_ID`, `S3_AWS_SECRET_ACCESS_KEY`, `S3_FILE_STORE_BUCKET_NAME` (default "onyx-file-store-bucket")
  - Console: `:9001` (internal only, no port published to host)
  - HA (prod): 4-node distributed MinIO (erasure-coded) in K8s, or Azure Blob Storage (SMB/NFS)
  - Persistence: `minio_data` Docker volume (~50GB recommended for typical deployments)

**Search & Vector Index:**
- OpenSearch 3.6.0 (fork of Elasticsearch 7.x)
  - Connection: `https://admin:password@opensearch:9200` (internal, self-signed TLS)
  - Driver: Onyx native OpenSearch client (Python)
  - Purpose: Lexical + vector (embedding) search for RAG retrieval
  - Env vars: `OPENSEARCH_HOST`, `OPENSEARCH_ADMIN_PASSWORD`, `OPENSEARCH_PORT` (default 9200)
  - Heap tuning: `OPENSEARCH_JAVA_OPTS` (default "-Xms1g -Xmx1g"), tunable via `OPENSEARCH_HEAP`
  - Persistence: `opensearch-data` volume (20+ GB recommended)
  - HA (prod): 3-node cluster (K8s), Premium SSD v2 storage, zone-redundant

**Model Embeddings Cache:**
- Local volume: `model_cache_huggingface:/app/.cache/huggingface/`
  - Mounted in inference_model_server container
  - Purpose: Pre-cached embeddings/reranking models (BAAI, jina-ai, etc.)
  - Populated: Via `make models` (pre-pull before startup)
  - Offline-friendly: Models cached locally; no internet required after initial download

## Authentication & Identity

**Auth Provider:**
- **Entra ID (Azure AD)** - Primary SSO (production)
  - Implementation: OIDC flow (relay via reverse-proxy/IdP, claims pre-verified in `X-OIDC-Claims` header)
  - Fallback: Basic auth (dev/test, `AUTH_TYPE=basic`)
  - Email verification: `REQUIRE_EMAIL_VERIFICATION` (default: false, enable in prod)
  - Valid domains: `VALID_EMAIL_DOMAINS` (optional)
  - Session timeout: `SESSION_EXPIRE_TIME_SECONDS` (default: 86400s, 1 day)

**Group Resolution (access-gateway):**
- Strategy 1 (Claims-based, priority 1): Parse `X-OIDC-Claims` JSON header
  - Claims to inspect: `GATEWAY_OIDC_GROUP_CLAIMS` (default: `groups,roles`)
  - Order = priority (first found wins)
- Strategy 2 (Graph fallback, priority 2 in auto-mode): Query Graph `transitiveMemberOf` endpoint
  - Source: `GATEWAY_GROUP_SOURCE` (default: `auto` = claims-first, Graph fallback on overage/absence)
  - Caching: `GATEWAY_GROUP_CACHE_TTL` (seconds, default 300s)
  - Deny-by-default: `GATEWAY_DENY_IF_NO_MATCH=true` (recommended in prod)

**Service Principals:**
- **onix-sharepoint-app** (setup via `scripts/setup-sharepoint-app.sh`)
  - Graph API Permissions (application roles, not delegated):
    - `Sites.Read.All` - Read all site metadata
    - `Files.Read.All` - Read file contents (for indexing)
    - `GroupMember.Read.All` - Enumerate group members (transitive)
  - Used by: `access-gateway/app/graph_client.py` (RBAC decisions), Onyx SharePoint connector

- **onix-fabric-app** (setup via `scripts/setup-fabric-app.sh`, NEW)
  - Graph API Permissions: Same as above (Sites, Files, GroupMember)
  - Fabric/Power BI Setup (manual, via admin portal):
    - Tenant setting: "Service principals can use Fabric APIs" (must be enabled)
    - Tenant setting: "Service principals can use Power BI APIs" (if Power BI used)
    - Workspace role: Add SPN to workspace with role ≥ Viewer (for control plane) or Member (for data plane)
  - Used by: `access-gateway/app/fabric_client.py` (Fabric/OneLake RBAC), `access-gateway/app/fabric_acl.py` (fail-closed ACL decisions)

## Monitoring & Observability

**Error Tracking:**
- Not configured (local/on-premise; no cloud error tracking service)
- Application exceptions logged to stdout (JSON format via Python logging)

**Logs:**
- Docker json-file driver: `max-size: 50m, max-file: 6` (rotate after 50MB, keep 6 files)
- Log volumes:
  - `api_server_logs:/var/log/onyx` (Onyx API)
  - `background_logs:/var/log/onyx` (Onyx background workers)
  - `inference_model_server_logs:/var/log/onyx` (embeddings/reranking)
- **access-gateway audit logs**: Pseudonymized identity (HMAC-salted) via `GATEWAY_AUDIT_SALT` (RGPD: no UPN in plaintext)

**Monitoring Stack (Optional, Separate Compose):**
- Command: `make monitor-up` / `make monitor-down`
- Components (`monitoring/docker-compose.monitoring.yml`):
  - **Prometheus v3.1.0** - Time-series metrics database
    - Retention: 15 days
    - Scrape targets:
      - `actions:8100/metrics` (FinOps, cache, usage tracking)
      - `access-gateway:8200/metrics` (request latency, guardrails, cache hits)
      - `postgres`, `redis`, `opensearch` via exporters (node_exporter, postgres_exporter, redis_exporter)
    - Alert rules: `monitoring/prometheus/rules/onix-alerts.yml`, `onix-slo.yml`
  - **Grafana** - Dashboards UI (localhost:3001, admin auth required)
    - Datasources: Prometheus, Loki
    - Pre-provisioned dashboards for Onyx, onix-actions, infrastructure
    - Auth: GRAFANA_ADMIN_PASSWORD (must be strong, enforced at `make monitor-up`)
  - **Alertmanager** - Alert routing/deduplication
  - **Loki** - Log aggregation (optional)
  - **Promtail** - Log shipper (Docker socket mounted read-only)
  - **Blackbox Exporter** - Endpoint availability probes (/health checks)

## CI/CD & Deployment

**Hosting:**
- Local Docker Compose (single-node, dev/test)
- Local Docker Compose + Caddy TLS (prod-local, on-prem single-node with OIDC)
  - Systemd unit: `deploy/local-prod/onix.service`
  - TLS auto-provisioned by Caddy (requires ONYX_DOMAIN set)
  - Runbook: `docs/PROD_LOCAL.md`
- Kubernetes (HA) via Helm chart `deploy/k8s/onix-ha`
  - Multi-zone deployment, auto-scaling, PodDisruptionBudgets
  - Data-tier: CloudNative-PG, OpenSearch, Redis Operator, MinIO (all optional, can use external services)
- Azure IaC: Bicep templates (`deploy/azure/bicep/`) for repeatable infrastructure setup

**CI Pipeline:**
- GitHub Actions (`.github/workflows/ci.yml`, `cd.yml`, `ragas-nightly.yml`)
- **Quality Gates (blocking):**
  - `pytest` - Unit/integration tests (offline suites):
    - `actions/tests` (audit, OCR, generation, finops)
    - `access-gateway/tests` (RBAC, caching, audit)
    - `tests/rag` (contract mode, no LLM)
  - `pip-audit --strict` - Zero CVE policy (pinned dependency audit)
  - `bandit` - SAST security scanner (medium+ severity)
  - `gitleaks` - Secrets detection (zero secrets allowed)
  - `docker compose config` - Compose syntax validation (all overlays)
  - `helm lint` + `helm template` - Kubernetes chart validation
  - `trivy` - Container image vulnerability scan (CRITICAL/HIGH only, ignore-unfixed)
- **Quality Gates (nightly, optional):**
  - `ragas-nightly.yml` - RAG quality gate (RAGAS eval with live Ollama LLM judge)
    - Fairness, context_precision, answer_relevancy metrics
    - Gate: absolute threshold + anti-regression baseline comparison
    - Baseline: `tests/rag/ragas_eval/baseline_scores.json` (committed; refreshed after reviews)

**Deployment Targets:**
- Docker containers (compose or K8s)
- Kubernetes (onix-ha chart, multi-zone HA, auto-scaling)
- Local single-node hardened (prod-local via docker-compose.prod-local.yml + systemd)

## Environment Configuration

**Required Environment Variables:**

- **Core Secrets (all generated by `scripts/gen-secrets.sh`):**
  - `SECRET` - Onyx session signing key (64+ chars, base64)
  - `USER_AUTH_SECRET` - Onyx user JWT signing key (64+ chars)
  - `ENCRYPTION_KEY_SECRET` - At-rest encryption for stored connector secrets (Onyx EE only, no-op in FOSS)
  - `POSTGRES_PASSWORD` - DB admin password
  - `OPENSEARCH_ADMIN_PASSWORD` - Search engine admin password
  - `REDIS_PASSWORD` - Cache password
  - `S3_AWS_ACCESS_KEY_ID`, `S3_AWS_SECRET_ACCESS_KEY` - MinIO/S3 credentials
  - `ONIX_ACTIONS_API_KEY` - Actions service HMAC key (32+ bytes)
  - `GATEWAY_CACHE_HMAC_SECRET` - Semantic cache safety key (32+ bytes, REQUIRED if cache enabled)
  - `GATEWAY_AUDIT_SALT` - Audit log HMAC salt

- **Network & TLS (Prod):**
  - `ONYX_DOMAIN` - Public domain name (for Caddy TLS)
  - `WEB_DOMAIN` - Callback URL (must start with `https://`)
  - `BIND_IP` - Listening IP (default `127.0.0.1`; set to `0.0.0.0` for remote, triggers security checks)

- **Auth (Prod):**
  - `AUTH_TYPE=oidc` - Enable OIDC (required if exposed to network)
  - `OAUTH_CLIENT_ID` - Entra app ID
  - `OAUTH_CLIENT_SECRET` - Entra app secret
  - `OPENID_CONFIG_URL` - OIDC discovery endpoint
  - `VALID_EMAIL_DOMAINS` - Domain filtering (optional)
  - `REQUIRE_EMAIL_VERIFICATION=true` - Enforce email verification

- **access-gateway (RBAC/ACL):**
  - `GATEWAY_ONYX_BASE_URL=http://api_server:8080` - Upstream Onyx API
  - `GATEWAY_GRAPH_TENANT_ID` - Entra tenant
  - `GATEWAY_GRAPH_CLIENT_ID` - Service principal ID (SharePoint/Fabric SPN)
  - `GATEWAY_GRAPH_CLIENT_SECRET` - Service principal secret
  - `GATEWAY_GROUP_SOURCE=auto` - Group resolution strategy
  - `GATEWAY_MAPPING_PATH=/config/group_map.json` - Document Set mapping
  - `GATEWAY_CACHE_ENABLED=true` - Semantic cache
  - `GATEWAY_CACHE_HMAC_SECRET` - Cache safety (gen-secrets.sh, ≥32 bytes)
  - `GATEWAY_CACHE_REDIS_URL` - Redis URL for multi-replica (empty = LRU memory)
  - `GATEWAY_DOC_ACL_PATH=/config/doc_acl.json` - Per-document ACL file
  - `GATEWAY_AUDIT_SALT` - Audit log salt

- **Fabric/OneLake (if used):**
  - Reuses: `GATEWAY_GRAPH_CLIENT_ID`, `GATEWAY_GRAPH_CLIENT_SECRET` (same SPN)
  - Implies: Fabric workspace role check + OneLake principal access (PREVIEW mode)

- **onix-actions:**
  - `ONIX_ACTIONS_API_KEY` - Service API key (required; missing = 503 Service Unavailable)
  - `ONIX_OLLAMA_URL=http://ollama:11434` - Ollama endpoint
  - `ONIX_LLM_MODEL=llama3.2:3b` - Field extraction model
  - Feature flags: `ONIX_AUDIT_ENABLED`, `ONIX_GENERATE_ENABLED`, `ONIX_OCR_ENABLED`, `ONIX_LLM_ENABLED`, etc.
  - FinOps (optional): `ONIX_RATE_CARD`, `ONIX_BUDGET_EUR`, `ONIX_BUDGET_WARN_PCT`
  - Notifications (optional): `ONIX_NOTIFY_WEBHOOK` (Slack/Teams/Mattermost compatible)

- **Ollama:**
  - `OLLAMA_KEEP_ALIVE=5m` - Model eviction timeout
  - `OLLAMA_NUM_PARALLEL=1` - Concurrency limit
  - `OLLAMA_CONTEXT_LENGTH=8192` - Context window (tuned by `make tune`)
  - `OLLAMA_FLASH_ATTENTION=1` - Performance optimization
  - `OLLAMA_KV_CACHE_TYPE=q8_0` - Quantized cache

**Secrets Location:**
- Development: `.env` file (git-ignored, never committed)
- Production (compose): `.env` file (mounted volume, chmod 600)
- Production (K8s): `onix-secrets` Kubernetes Secret (Workload Identity + Azure Key Vault CSI driver)
- CI/CD: GitHub Actions secrets (ONIX_E2E_* for tests)

## Webhooks & Callbacks

**Incoming:**
- None configured (onix is read-only from Entra/Graph/Fabric perspective)
  - SharePoint connector: Pull-based scheduled sync (no incoming webhook)
  - Fabric connector: Pull-based queries (no change-feed)

**Outgoing:**
- onix-actions: Generic webhook for notifications
  - `ONIX_NOTIFY_WEBHOOK` - Slack/Mattermost/Teams-compatible URL (optional)
  - Triggers: Budget threshold alerts, OCR completion, task completion
  - Auth: None (webhook URL itself is the shared secret)

---

*Integration audit: 2026-06-19*
