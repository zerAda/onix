# Codebase Concerns

**Analysis Date:** 2026-06-18

## Tech Debt

### Onyx FOSS Upstream Limitations (Architectural)

**Area:** Permission sync and document-level RBAC (search phase)

- **Issue:** The upstream Onyx FOSS build does not perform permission-sync from source systems (SharePoint, Confluence, etc.) at the retrieval phase. This means the LLM sees all documents indexed in a Document Set, regardless of per-document ACLs on the source system.
- **Files:** `access-gateway/app/doc_acl.py`, `access-gateway/app/graph_acl.py`
- **Impact:** Potential unintended information leakage during LLM generation if two users in the same Document Set have different permissions on the same file. The LLM may inadvertently reference content from a file one user should not see.
- **Mitigation in place:** Post-filter on the response path (`doc_acl.py`) strips citations to non-authorized documents and replaces with a refusal if zero citations remain. ACL is now auto-synchronized from SharePoint via Microsoft Graph (`graph_acl.py`, `make sync-doc-acl`).
- **Fix approach:** This is an **Onyx Enterprise Edition / Cloud** function (permission-sync with certificate). For FOSS, mitigations are: design Document Sets around homogeneous access patterns (e.g., one per commercial team), or deploy separate Onyx instances per access tier. Full per-document trimming at retrieval requires upgrading to EE or Cloud.

### Onyx FOSS: Encryption of Secrets at Rest (Default OFF)

**Area:** Connector credentials and API keys storage

- **Issue:** The upstream Onyx FOSS `backend/onyx/utils/encryption.py` does not encrypt secrets — the `_encrypt_string()` function returns plaintext bytes. Setting `ENCRYPTION_KEY_SECRET` logs a no-op message. Connector credentials, LLM API keys, and OAuth tokens are stored unencrypted in Postgres.
- **Files:** `backend/onyx/utils/encryption.py:16-30`, `backend/onyx/db/models.py:163`
- **Impact:** **CRITICAL for FOSS deployments.** Postgres must be protected as a security perimeter. If an attacker gains access to the database (backup leakage, stolen VM image, SQL injection in upstream Onyx), all credentials are readable in plaintext.
- **Mitigation in place:** In onix, `ENCRYPTION_KEY_SECRET` must always be set (Azure Key Vault or similar) and the environment is hardened (database access controls, TLS on connections, backups encrypted).
- **Fix approach:** Upgrade to **Onyx Enterprise Edition** if Postgres encryption at rest is mandatory. For self-hosted FOSS, rely on infrastructure-layer encryption (encrypted EBS/Azure Disk, Postgres transparent encryption if available, strict DB access controls) and treat the Postgres instance as a security boundary.

### Secrets File Defaults in Compose (dev/base)

**Area:** Development Docker Compose default credentials

- **Issue:** `docker-compose.yml` and dev overrides ship hardcoded default passwords for Postgres (`password`), MinIO (`minioadmin`), and Redis. These are also documented in `deployment/docker_compose/docker-compose.yml:380,526,86`.
- **Files:** `docker-compose.yml`, `docker-compose.dev.yml`, `deployment/docker_compose/docker-compose.yml`
- **Impact:** If the base compose file is deployed without overriding these defaults (especially via CI/automation), credentials are known to attackers. Risk is elevated if `docker-compose.yml` is committed with production values.
- **Mitigation in place:** Production `docker-compose.prod.yml` uses `${VAR:?error}` fail-fast syntax and secrets are externalized via `gen-secrets.sh`. The project's Makefile enforces `make secrets` before `make up`.
- **Fix approach:** Continue to enforce secret generation tooling and document in RUNBOOK.md that base `docker-compose.yml` is for **development only**. Add a startup guard that rejects the default credentials if `POSTGRES_PASSWORD=password` is detected in prod mode.

### Chart Defaults: Data-Tier SPOFs (Kubernetes Helm)

**Area:** Official Helm chart shipping single-instance defaults

- **Issue:** The upstream Onyx Helm chart (`deployment/helm/charts/onyx`, v0.6.0) ships with single-instance defaults: Postgres `instances: 1`, OpenSearch `singleNode: true`, Redis standalone, MinIO standalone. This means the chart is HA-*capable* but ships HA-*off*.
- **Files:** `deployment/helm/charts/onyx/values.yaml:30,57,64,1029-1030,1041`, `deploy/k8s/onix-ha/values.yaml` (onix correction)
- **Impact:** **SPOF risk in default deployments.** A single data-tier node failure → full outage. Postgres loss = all metadata gone; OpenSearch loss = no search/retrieval; Redis loss = dropped locks/tasks; MinIO loss = files unreachable.
- **Mitigation in place:** onix provides `deploy/k8s/onix-ha/` chart with HA defaults (`replicas >= 2`, HPA, `PodDisruptionBudgets`). Chart is validated server-side and matches upstream structure.
- **Fix approach:** For production Kubernetes deployments, use `deploy/k8s/onix-ha/` instead of the upstream chart, or manually override all data-tier components to multi-instance. Document this as a non-negotiable requirement in `docs/DEPLOY_AZURE.md` and `docs/HA_SCALING.md`.

## Known Bugs

### Onyx Upstream: DB Migration Race on Multi-Replica api Boot

**Area:** Alembic migrations in clustered Onyx deployments

- **Symptoms:** When `api.replicaCount > 1` in Kubernetes, all api pods race to run `alembic upgrade head` (379 migrations) simultaneously during startup. Concurrent `CREATE TABLE`/`ALTER TABLE` from N pods can cause lock contention, partial applies, or deadlocks. Some migrations may fail silently if the table already exists.
- **Files:** `backend/alembic/env.py`, `deployment/helm/charts/onyx/templates/api-deployment.yaml:73` (inline migration)
- **Trigger:** Scaling api replicas to > 1 in Kubernetes without a pre-install Job or advisory-lock guard. The multi-tenant path explicitly warns about this (`backend/onyx/db/engine/tenant_utils.py`).
- **Workaround:** Run migrations in a separate pre-install Job (Helm hook `pre-install`), not inline in the api container. Use `pg_advisory_lock()` or a dedicated migration lock table. Currently not implemented in the upstream chart.
- **Impact:** Data inconsistency, failed app startup, silent schema corruption. Blocks production multi-replica deployments.

### Onyx Upstream: Celery Beat Singleton Without Leader Election

**Area:** Periodic scheduler duplication risk

- **Symptoms:** Celery beat is a singleton (`DynamicTenantScheduler(PersistentScheduler)`) with no RedBeat or leader-election mechanism. The Deployment defaults to `RollingUpdate` strategy without `Recreate`, so during a rolling upgrade, two beat pods may briefly run simultaneously, dispatching periodic tasks twice (e.g., permission sync, index cleanup).
- **Files:** `deployment/helm/charts/onyx/templates/celery-beat.yaml:12`, `backend/onyx/background/celery/apps/beat.py:27`
- **Trigger:** Any `helm upgrade` or pod restart during standard maintenance. If a human manually sets `replicaCount: 2` in values, beat continuously double-schedules.
- **Workaround:** Add `strategy: Recreate` to the beat Deployment. Beat-tick locks per tenant (`CHECK_*_BEAT_LOCK`, ~120s duration) mitigate downstream duplication but are fragile.
- **Impact:** Duplicate job dispatch (permission sync runs twice), increased load, potential race conditions in lock-based work coordination. Low severity (mitigated by locks) but a correctness gap.

### Onyx Upstream: api-server Missing Health Probes

**Area:** Kubernetes readiness and liveness

- **Symptoms:** The api-server Deployment ships with empty probes (`startupProbe/readinessProbe/livenessProbe: {}` in values). Traffic routes to pods before Alembic finishes and uvicorn is ready. A wedged api pod (deadlock, OOM) stays in the load-balancer rotation indefinitely.
- **Files:** `deployment/helm/charts/onyx/values.yaml:50-52`
- **Trigger:** Any api pod deployment or a hung api process during operation.
- **Workaround:** Operators must supply custom probes to `values.yaml` (example in comments but not active). onix-ha chart provides sensible defaults.
- **Impact:** Traffic to unready pods, delayed error detection, cascading failures. Should be a mandatory requirement, not optional.

## Security Considerations

### Prompt Injection via Custom Tools → SSRF + Credential Replay

**Area:** LLM Tool Integration / External API calls

- **Risk:** Onyx Custom Tools (OpenAPI actions) in the upstream FOSS bypasses centralized SSRF checks. The tool implementation calls `requests.request()` directly with user-input-driven URL paths/query params and carries an `Authorization` header. A malicious document can steer the LLM to invoke a tool with a redirect to an internal host, leaking credentials.
- **Files:** `backend/onyx/tools/tool_implementations/custom/custom_tool.py:193-198`, `openapi_parsing.py:52-66`
- **Current mitigation:** Tool base hosts are admin/curator-configured (not LLM-controlled), limiting reachable targets. Upstream Onyx does not guard the path/query params.
- **Recommendations:** Route Custom Tool calls through `ssrf_safe_get()` before executing. Validate the full URL (not just base host) against SSRF policy. This is an upstream Onyx gap, not introduced by onix.

### Avatar Upload: Content-Type Confusion (Stored XSS)

**Area:** File upload handling

- **Risk:** Avatar upload endpoint accepts `file.content_type` directly from the client (no validation) and stores it. On download, the stored content-type is replayed to the browser. An attacker can upload `image/svg+xml` or `text/html` with JavaScript, achieving stored XSS when the avatar URL is fetched.
- **Files:** `backend/onyx/server/features/persona/api.py:285-298`, `chat_backend.py:907,922`
- **Current mitigation:** Requires authentication + `Vary: Cookie` cookie header.
- **Recommendations:** Validate MIME type against an allowlist (e.g., `image/jpeg`, `image/png`, `image/webp`). Use a library like `puremagic` (already a dependency) to sniff the file's actual magic bytes. Reject mismatches.

### Insufficient SSRF Protection Below `VALIDATE_ALL`

**Area:** Web connector for crawling

- **Risk:** Web connector SSRF guard (`web_connector_ssrf_enforced`) is only active when `SSRFProtectionLevel.VALIDATE_ALL`. At `VALIDATE_LLM`, `ALLOW_PRIVATE_NETWORK`, or `DISABLED`, the guard returns immediately and an admin-configured web crawler can reach `169.254.169.254` (AWS IMDS), localhost, or RFC1918 addresses.
- **Files:** `backend/onyx/server/security/models.py:74-78`, `connectors/web/connector.py:110-121`
- **Current mitigation:** Default is `VALIDATE_ALL`, and the code itself documents this as a known-intentional trade-off (internal crawler mode). Operator can configure lower levels if they trust the admin.
- **Recommendations:** Document the SSRF levels clearly in deployment guides. Default must remain `VALIDATE_ALL`. If an operator intentionally disables SSRF for internal crawling, require explicit acknowledgment and log a warning.

### Secrets in Helm `values-localdev.yaml` (Dev Only)

**Area:** Configuration management

- **Risk:** Development Helm values file `deployment/helm/charts/onyx/values-localdev.yaml:102,109` contains hardcoded encryption keys and sandbox private keys. If this file is accidentally deployed to production or shared insecurely, credentials are exposed.
- **Files:** `deployment/helm/charts/onyx/values-localdev.yaml`
- **Current mitigation:** File is clearly named `-localdev`, development-only. Production `values.yaml` defaults empty. `install.sh` auto-generates `USER_AUTH_SECRET`.
- **Recommendations:** Add a pre-deploy check that rejects hardcoded secrets in production values. Consider using sealed-secrets or external secret management (Azure Key Vault, HashiCorp Vault) for all environments.

## Performance Bottlenecks

### Postgres Connection Pool Exhaustion Under Multi-Replica api Scale

**Area:** Database connection management

- **Problem:** Each api replica spawns a connection pool (sync `pool_size=40, max_overflow=10` + async equivalent) plus workers add more. Math: 10 api replicas ≈ 1,000+ concurrent connections. Postgres default `max_connections` (100–500 in CNPG) is exhausted quickly.
- **Files:** `backend/onyx/configs/app_configs.py:459-470`, `backend/onyx/db/engine/sql_engine.py`, `async_sql_engine.py`
- **Cause:** No PgBouncer or connection pooler is deployed by default in the chart. TCP keepalives exist but do not address the ceiling.
- **Improvement path:** Integrate **PgBouncer** or **pgcat** as a sidecar or standalone service in the Helm chart. CNPG offers a `Pooler` CRD; use it. Size pooler to `pool_size = (max_connections - reserved) / replicas`. Document minimum Postgres `max_connections` for target replica count. This is under-documented in the upstream chart.

### Embedding/Model-Server Throughput Ceiling

**Area:** Vector embedding generation

- **Problem:** Model-server inference runs a single replica (no HPA) with unbounded concurrency. Under high load (indexing + query embedding simultaneously), the model instance OOMs or deadlocks ("Already borrowed" race in `SentenceTransformer`, retried 3×). Local embed batch size is 8 (inefficient for throughput).
- **Files:** `backend/model_server/encoders.py:89-99`, `backend/onyx/configs/model_configs.py:42,44`, `deployment/helm/charts/onyx/values.yaml:173` (no HPA)
- **Cause:** Single model-server instance, no autoscaler, no request limiting (indexing has `--limit-concurrency 10`, inference does not).
- **Improvement path:** Add HPA to inference model-server (CPU/memory triggers, `minReplicas: 2`, `maxReplicas: 6`). Add `--limit-concurrency 20` to inference. Increase local embed batch to 16–32. Profile under realistic load. Documented in `docs/PERFORMANCE.md`.

### Celery Docfetching Serialization Bottleneck

**Area:** Connector data extraction

- **Problem:** Docfetching concurrency = 1 per worker (`app_configs.py:625`). Connector extraction is single-threaded, serialized. A large connector (e.g., 100K+ documents) runs single-threaded, limiting throughput. Process stage (concurrency 6) can outrun fetch.
- **Files:** `backend/onyx/configs/app_configs.py:625`
- **Cause:** Connector APIs often rate-limit or enforce sequential fetch. Single-threaded fetch is intentional to avoid overload.
- **Improvement path:** This is a design constraint, not a bug. Mitigation is horizontal: scale docfetching worker pods to parallelize multiple connectors or partition large connectors (e.g., by date range). Document the parallelization strategy in `docs/PERFORMANCE.md`.

### Redis Non-Persistence & Eviction Risk

**Area:** Celery broker and lock storage

- **Problem:** Redis is configured standalone with `appendonly: no` and `save: ""` (no persistence) and `maxmemory-policy: allkeys-lru`. A Redis restart evicts all state, including in-flight Celery tasks, locks, and caches. This can cause deadlocks or task loss.
- **Files:** `deployment/helm/charts/onyx/values.yaml:1029-1030`
- **Cause:** Default CNPG chart is optimized for ephemeral cache, not durable broker state.
- **Improvement path:** For production, enable Redis persistence (`appendonly: yes`, `save "60 1000"`). Switch Celery to use a proper message broker (RabbitMQ recommended, used in onix-ha). Implement Redis replication/Sentinel for HA. Documented in `docs/HA_SCALING.md` as a mandatory change.

## Fragile Areas

### access-gateway: ACL Synchronization Latency and Edge Cases

**Area:** Document access control (onix-specific)

- **Files:** `access-gateway/app/doc_acl.py`, `access-gateway/app/graph_acl.py`
- **Why fragile:** The per-document ACL is synchronized from SharePoint via Microsoft Graph on a configurable interval (`GATEWAY_DOC_ACL_REFRESH_SECONDS`, default 3600s). During the sync window, SharePoint and the gateway's ACL cache can diverge. If a user's access is revoked on SharePoint but the sync hasn't run, the gateway may still show citations from that file.
- **Safe modification:** Keep the sync interval low (< 300s) for sensitivity. Implement a webhook listener (SharePoint push notifications) to trigger immediate re-sync on ACL change. Add monitoring/alerting for sync failures. Test with large ACL lists (100K+ permissions) to ensure performance.
- **Test coverage:** Integration tests with mock Graph API exist (`access-gateway/tests/`), but live SharePoint/tenant testing is limited. Consider a staging test against a real (non-prod) SharePoint tenant.

### actions: OCR and LLM-Based Extraction Quality

**Area:** Document audit and information extraction (onix-specific)

- **Files:** `actions/app/ocr_audit.py`, document generation pipeline
- **Why fragile:** OCR quality depends on image resolution, text orientation, and font. LLM-based field extraction (via Ollama) is model-dependent; smaller models (< 3B) may hallucinate or miss fields. The extraction comparison against a reference document is exact-string matching (brittle).
- **Safe modification:** Add confidence scoring for OCR and extraction. Implement fuzzy string matching for comparison (e.g., Levenshtein distance). Add A/B testing / manual review queue for low-confidence extractions. Profile with real customer documents (not just synthetic).
- **Test coverage:** 34 tests in `actions/tests/` cover happy paths; edge cases (rotated text, handwriting, multi-language) not fully covered. Add regression tests for known customer document types.

### Streaming guardrails: Hard abort before malicious chunk

**Area:** LLM response safety (onix-specific)

- **Files:** `access-gateway/app/streaming.py`, post-filtre guard logic
- **Why fragile:** The guardrail post-filter streams the response incrementally and aborts if a malicious chunk is detected (e.g., prompt leak, injection attempt). Abort must happen **before** the chunk is sent to the client. If the LLM generates a multi-part injection (split across chunks), the guard might miss it.
- **Safe modification:** The post-filtre red-team testing is strong (21/21 on real `qwen2.5:7b`), but test against larger/newer models (Llama 3.1 70B, Mistral Large). Add continuous monitoring and alerting for guardrail failures. Document the design assumption: guardrails are **heuristic**, not cryptographic proof.
- **Test coverage:** `tests/rag/test_guardrails.py` + E2E E2E_GUARDRAILS.md. Coverage is solid but live-model testing is biased toward small Ollama models. Recommend rotating model validation.

## Scaling Limits

### Onyx Vector Index Ceiling (OpenSearch Single-Shard Default)

**Area:** Search index scalability

- **Current capacity:** Default OpenSearch deployment ships 1 shard, 1 replica (`document_index/opensearch/schema.py`). Practical document ceiling: ~10–50M chunks (rough; depends on RAM, shard size tuning).
- **Limit:** Single shard becomes a bottleneck at high query throughput. No cross-shard parallelism. Node loss = full outage (single replica on single node).
- **Scaling path:** For >50M document corpus, increase shard count to 3–5 (requires index rebuild or reindexing). Use AWS OpenSearch managed or self-hosted OpenSearch cluster with >= 3 nodes and replica >= 1. Documented in `docs/PERFORMANCE.md` and `docs/HA_SCALING.md`.

### Postgres WAL Disk Space Under High Indexing Throughput

**Area:** Database write-ahead log management

- **Current capacity:** Indexing throughput = ~1000 chunks/second (per docprocessing worker at concurrency 6). At sustained high throughput, WAL generation can exceed disk capacity if CNPG isn't configured with automatic WAL archival.
- **Limit:** Disk fills → writes stall → queries timeout → operational crisis.
- **Scaling path:** Enable CNPG WAL archival to S3 (`archiveWALTemplate`). Monitor WAL disk usage. Set `max_wal_size` based on expected throughput. Tested in onix-ha chart with `ScheduledBackup`.

### Redis Max Memory & Task Queue Depth

**Area:** In-memory broker and cache

- **Current capacity:** Redis `maxmemory-policy: allkeys-lru` at default 256MB. Under sustained indexing, Celery task backlog can exceed memory.
- **Limit:** Eviction drops tasks / locks → silent failures.
- **Scaling path:** Increase Redis `maxmemory` to 2–4GB (scale with worker count). Switch to RabbitMQ for the broker (stateless, scales independently). Enable Redis Streams for task durability (Celery 5.3+, opt-in). Monitored in `docs/HA_SCALING.md`.

## Dependencies at Risk

### cryptography 46.0.7: OpenSSL Bundled Vulnerability (CVSS 7.5)

**Area:** Supply chain / cryptography

- **Risk:** `cryptography==46.0.7` bundles an OpenSSL version with a known vulnerability (GHSA-537c-gmf6-5ccf, Availability impact, CVSS 7.5). Vulnerability is a potential DoS in TLS handshakes.
- **Impact:** Under attack (malicious TLS handshakes), API availability could degrade.
- **Migration plan:** Upgrade `cryptography` to `48.0.1` or later. Coordinate with upstream Onyx dependency pins. Test for backward compatibility. No changes needed in onix code.

### starlette 0.49.3: Path Poisoning & StaticFiles SSRF (MODERATE)

**Area:** Web framework

- **Risk:** Two advisories: BadHost path-poisoning (GHSA-86qp-…) and StaticFiles SSRF on Windows (GHSA-wqp7-…). Fixed in starlette 1.0.1 / 1.1.0.
- **Impact:** BadHost mitigation exists (Onyx uses `WEB_DOMAIN` hard-coded, not Host header). StaticFiles SSRF not exploitable (Onyx does not serve user-controlled paths as static files).
- **Migration plan:** Upgrade starlette requires a FastAPI major bump (`starlette>=1.0` is a breaking change). Defer unless a blocker emerges. Monitor GitHub advisories. **LOW priority.**

### pypdf 6.10.2: Parser DoS (CVSS ~4.x)

**Area:** PDF handling

- **Risk:** Multiple DoS vulnerabilities in PDF parsing. Onyx parses untrusted PDFs during indexing.
- **Impact:** A malicious PDF could cause indexing to hang or crash.
- **Migration plan:** Upgrade `pypdf` to `6.12.0`. Test with a corpus of real PDFs (corpus in test fixtures or customer backups). No code changes needed.

### NLTK 3.9.4: Path Traversal (CVSS 7.5)

**Area:** Natural language processing

- **Risk:** Vulnerability in `nltk.data.load()` allows path traversal. Risk is elevated if NLTK is used to load user-supplied data paths.
- **Impact:** Code execution or information disclosure.
- **Migration plan:** Note: the current pin is `nltk==3.9.4`, which is **already the fixed version** (OSV database lists this as the fixed boundary). No upgrade needed. Verify in live code that NLTK paths are not user-controlled.

## Missing Critical Features

### Audit-Trail ("Who Saw What") — Onyx FOSS Missing Entirely

**Area:** Compliance / RGPD accountability

- **Problem:** Onyx FOSS has **no audit-trail** recording which user accessed which document, when. This feature is completely absent even in Onyx Enterprise Edition. onix compensates with HMAC-chained audit logs in the gateway and actions, but core Onyx chat/search access is not logged at the Onyx level.
- **Blocks:** RGPD art.5(2) accountability (data controller must prove what happened). Compliance with CNIL / GDPR audits for regulated deployments.
- **Approach:** onix adds HMAC-chained audit logs to `access-gateway/` and `actions/`. For full audit compliance, enable Postgres query logging (`log_statement = 'all'`) and centralize logs to a tamper-proof sink (Loki + retention policy). This is a **documented limitation** (`docs/SECURITY.md`, `docs/RGPD.md`).

### Right-to-Erasure (Art.17) — Onyx FOSS Implementation Broken

**Area:** RGPD / data subject rights

- **Problem:** Onyx FOSS user deletion is broken: foreign-key `NOT NULL` constraints are not handled, leaving PII (email, name) orphaned in the database. Art.17 erasure must delete or anonymize all PII traces, including chat history and documents.
- **Files:** Onyx upstream (not onix-specific, mitigated by onix-actions `/erasure` endpoint)
- **Impact:** RGPD non-compliance, potential data protection violations.
- **Approach:** onix-actions provides `/erasure` endpoint that explicitly deletes chat history and user metadata. Connector-indexed documents are not deleted (by design; erasure of a document shared across users would affect others). Document this limitation in `docs/RGPD.md`. For full GDPR compliance, implement a document purge workflow (custom connector).

### Multi-Tenancy at FOSS Level

**Area:** Scaling to multiple independent customers

- **Problem:** Onyx FOSS has **no multi-tenancy**. Sharing a single Onyx instance across multiple customers requires trust that the query filtering (Document Sets) is ironclad. There is no schema isolation.
- **Blocks:** Multi-customer SaaS on FOSS build.
- **Approach:** Deploy **separate Onyx instances per customer** (one Postgres, one OpenSearch, one Ollama, etc.). onix-ha chart makes this repeatable. Scaling to 100s of customers requires orchestration (templated Helm values, Argo, Terraform). This is a **design decision**, not a bug. Documented in `docs/HA_SCALING.md`.

## Test Coverage Gaps

### Gateway ACL Filtering: No Live SharePoint Integration Tests

**Area:** access-gateway

- **What's not tested:** The `graph_acl.py` module syncs ACLs from a **real** Microsoft Graph tenant. Current tests mock the Graph client. No live test against a staging/dev SharePoint tenant to verify:
  - Handling of large ACL lists (100K+ items)
  - Refresh intervals and staleness windows
  - Webhook-triggered sync (if implemented)
  - Permission inheritance edge cases
- **Files:** `access-gateway/tests/`, `access-gateway/app/graph_acl.py`
- **Risk:** Subtle bugs in permission sync could silently leak or over-restrict. A real tenant's ACL structure might differ from test mocks.
- **Priority:** **HIGH** — this is a security-critical path. Recommend staging test with a real non-prod tenant (or a dedicated test tenant in your Azure subscription).

### onix-actions: E2E Against Real External Services

**Area:** actions microservice

- **What's not tested:** 
  - Notifications: SMTP integration only tested in unit tests (no real SMTP server in CI). Slack/Teams/Mattermost webhook tested against mocks.
  - OCR: Tested with synthetic PDFs and images; no real-world scanned documents (handwriting, color, skew).
  - Task persistence: SQLite-backed task list in docker-compose is not tested against Postgres (used in Kubernetes).
- **Files:** `actions/tests/`
- **Risk:** Notification failures could go undetected until prod. OCR accuracy on customer documents unknown.
- **Priority:** **MEDIUM** — actions are operational but feature-gated. Recommend a customer pilot with real documents and real notification endpoints before general rollout.

### Streaming Guardrails: Coverage Against Larger Models

**Area:** Post-filter / LLM safety

- **What's not tested:** The guardrail red-team testing (21/21 passing) was conducted against `qwen2.5:7b`. Larger models (Mistral Large, Llama 3.1 70B) may exhibit different attack surface.
- **Files:** `tests/rag/`, `access-gateway/app/guardrails.py`
- **Risk:** A jailbreak that passes the small model might succeed on a larger model.
- **Priority:** **MEDIUM** — recommend rotation of red-team tests to include at least one large OSS model quarterly. Document the model-dependency assumption.

---

*Concerns audit: 2026-06-18*
