<!-- refreshed: 2026-06-18 -->
# Architecture

**Analysis Date:** 2026-06-18

## System Overview

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                    User Layer (Browser / HTTP Client)                   │
│                         HTTPS (TLS + OIDC)                             │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │  Ingress (nginx/Caddy)       │
                    │  + OIDC oauth2-proxy         │
                    │  (Entra ID identity)         │
                    │  Posts X-OIDC-Claims header  │
                    └──────────┬──────────┬────────┘
         /api/chat/* routes →  │          │  ← remaining routes
                       ┌───────▼──────┐   │
   ONIX LAYER ────────►│ access-       │   │  RBAC (group→Document Set),
   (value-add)         │ gateway       │   │  ACL per-document, cache,
                       │ (FastAPI)     │   │  guardrails, /metrics,
                       │ `app/main.py` │   │  streaming SSE
                       └───────┬───────┘   │
                               ▼           ▼
                       ┌──────────────────────────────┐
   ONYX LAYER ────────►│ api_server (FastAPI)         │
   (FOSS, MIT)         │ background (Celery workers)  │
                       │ `onyx-backend:4.1.1`         │
                       └─────┬────────┬────────┬───────┘
                             │        │        │
              ┌──────────────┼────────┼────────┼───────────────────┐
              │              │        │        │                   │
              ▼              ▼        ▼        ▼                   ▼
        ┌──────────┐  ┌──────────┐ ┌──────┐ ┌──────────┐  ┌──────────────┐
        │Postgres  │  │OpenSearch│ │Redis │ │  MinIO   │  │   Ollama     │
        │(metadata)│  │(vectors+ │ │(cache│ │(files)   │  │ (LLM local)  │
        │  15.2    │  │ BM25)    │ │broker│ │  S3      │  │  0.30.8      │
        │  v15.2   │  │  3.6.0   │ │ 7.4  │ │RELEASE.  │  │ CPU or GPU   │
        │          │  │          │ │ -a   │ │2025-07-23│  │              │
        └──────────┘  └──────────┘ └──────┘ └──────────┘  └──────────────┘
              ▲ DATA TIER (INFRA LAYER: managed Azure or in-cluster/compose)

        ┌────────────────────────────────────────────────────────────┐
        │ onix-actions Microservice (FastAPI)                        │
        │ - audit engine (OCR extraction, field normalization)       │
        │ - docgen (.docx generation)                               │
        │ - tasks & notifications                                   │
        │ - usage tracking & cost reporting                         │
        │ - admin controls (kill-switch, feature flags)             │
        │ - audit HMAC chaining (tamper-evident logs)               │
        │ - PII redaction, DLP egress, retention/deletion           │
        └────────────────────────────────────────────────────────────┘
              (Invoked via Onyx Custom Actions by assistant)
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **access-gateway** | RBAC enforcement (groups→Document Sets), per-document ACL, cache RBAC-safe, guardrails post-filter, streaming proxy | `access-gateway/app/main.py` |
| **identity** | Parse OIDC claims header, resolve group membership via Entra ID Graph or OIDC claims | `access-gateway/app/identity.py` |
| **cache** | HMAC-keyed response caching (exact & semantic), isolation by authorized perimeter | `access-gateway/app/cache.py` |
| **guardrail** | Deterministic post-filter (citations, hallucination, injection, exfil detection) | `access-gateway/app/guardrail.py` |
| **doc_acl** | Document-level access filtering (static JSON + Graph SharePoint ACLs) | `access-gateway/app/doc_acl.py`, `app/graph_acl.py` |
| **audit** | Access decisions + guardrail decisions with HMAC pseudonymization | `access-gateway/app/audit.py` |
| **onyx-actions** | Audit engine (OCR→normalized fields), docgen, tasks, notifications, cost tracking, admin kill-switch, audit log chaining | `actions/app/` (multiple modules) |
| **audit_engine** | Extract canonical fields from OCR, normalize amounts/dates/names, compare against reference | `actions/app/audit_engine.py` |
| **audit_log** | HMAC-chained audit trail (tamper-evident), verification | `actions/app/audit_log.py` |
| **db** | Stateless database layer (SQLite default, PostgreSQL opt-in for HA) | `actions/app/db.py` |
| **ollama** | Local LLM inference (CPU/GPU), no cloud calls, no telemetry | `docker-compose.yml` service |

## Pattern Overview

**Overall:** Multi-layered proxy with security-first composition.

**Key Characteristics:**
- **Fail-closed**: Missing identity, unavailable graph, or ACL errors all deny access
- **Deterministic security**: Post-filter guardrails are outside LLM (not manipulable by injection)
- **Stateless tiers**: API/background/gateway/actions all horizontally scalable (state→DB/Redis)
- **Sovereign**: All LLM inference local (Ollama, no cloud API calls), telemetry off
- **Auditable**: Single compose file, pinned images, HMAC-chained audit trail, configuration-driven

## Layers

**Ingress Layer:**
- Purpose: TLS termination, OIDC authentication (Entra ID), identity injection
- Location: `nginx` + `oauth2-proxy` (via docker-compose or Caddy in prod)
- Contains: Reverse proxy config, SSO integration
- Depends on: Entra ID OIDC provider
- Used by: All downstream services (via X-OIDC-Claims header)

**ONIX Gateway Layer (Custom):**
- Purpose: RBAC enforcement, document ACL, cache, guardrails, observability
- Location: `access-gateway/` (FastAPI microservice)
- Contains: Identity resolution, group mapping, cache logic, post-filters, metrics
- Depends on: Onyx upstream, Redis (optional cache), Microsoft Graph (optional ACL), Ollama (semantic embeddings optional)
- Used by: All `/api/chat/*` requests (proxied from ingress)
- Stateless: Can replicate horizontally (state in Redis/DB)

**Onyx FOSS Layer:**
- Purpose: RAG orchestration (chat, search, indexing, connectors)
- Location: `api_server`, `background` containers (onyxdotapp/onyx-backend:4.1.1)
- Contains: FastAPI endpoints, Celery workers, Alembic migrations
- Depends on: Postgres, OpenSearch, Redis, model-server, MinIO
- Used by: Gateway proxy, UI frontend
- Stateless: No local state (all in data tier)

**Model Inference Layer:**
- Purpose: Embeddings + reranking (deterministic, fast)
- Location: `inference_model_server` container (onyxdotapp/onyx-model-server:4.1.1)
- Contains: Sentence transformers, rerank models
- Depends on: HuggingFace cache
- Used by: Background indexing, retrieval pipeline

**LLM Layer:**
- Purpose: Text generation (local, sovereign)
- Location: `ollama` container (ollama/ollama:0.30.8)
- Contains: Model weights (pulled via `scripts/pull-models.sh`), inference engine
- Depends on: Host CPU or GPU, models on disk
- Used by: Onyx generation endpoint, gateway semantic cache (optional)

**Onix Actions Microservice:**
- Purpose: Audit trail, docgen, tasks, cost tracking, admin controls
- Location: `actions/` (FastAPI, invoked as Onyx Custom Action)
- Contains: Audit engine, audit log, admin state, cost tracker, task queue
- Depends on: SQLite or Postgres (stateful), Celery (optional queue), MinIO (docgen)
- Used by: Onyx assistant (via Custom Action OpenAPI hook)

**Data Tier (Managed / In-Cluster):**
- **Postgres**: Onyx metadata, user accounts, configurations; onix admin state, audit log, usage tracking
- **OpenSearch**: Vector index (embeddings) + lexical search (BM25)
- **Redis**: Celery broker, cache backend, session store
- **MinIO/S3**: Document files, generated .docx artifacts

## Data Flow

### Primary Chat Request Path

1. **User submits question** → Browser (HTTPS)
2. **Ingress → OIDC validation** → OAuth2-proxy verifies session, injects `X-OIDC-Claims` header (`access-gateway/app/identity.py:resolve_principal`)
3. **Access Gateway identity resolution** → Parse OIDC claims or fetch groups from Graph (`access-gateway/app/identity.py:resolve_principal`)
4. **RBAC filter** → Map user groups to authorized Document Sets (`access-gateway/app/main.py:_principal_and_sets`, `app/mapping.py`)
5. **Cache check (exact)** → HMAC key = normalized question + sorted authorized Document Sets + locale (`access-gateway/app/cache.py:make_cache_key`)
   - **Hit**: Serve cached response, skip LLM, apply ACL on cached answer
   - **Miss**: Proceed to upstream
6. **Proxy to Onyx** → POST `/v1/chat/send-message` with enforced `document_set_ids` (`access-gateway/app/onyx_proxy.py:enforce_document_sets`)
7. **Onyx retrieval** → OpenSearch search (top-k) + reranking via model-server
8. **Ollama generation** → Text generation at `http://ollama:11434` (internal)
9. **Response stream** → SSE streaming back to gateway (`access-gateway/app/streaming.py:proxy_stream`)
10. **Post-filter guardrails** → Check for citation, hallucination, injection, exfil (`access-gateway/app/guardrail.py:post_filter`)
    - **Fail**: Return safe refusal
    - **Pass**: Cache response, apply document ACL filter
11. **Document ACL filter** → Remove citations not accessible by user (`access-gateway/app/doc_acl.py:filter_citations`)
12. **Audit log** → Record access decision (pseudonymized) (`access-gateway/app/audit.py:log_access_decision`)
13. **Stream to user** → Filtered response via SSE

**State Management:**
- **Per-request state**: Principal, authorized sets, cache key computed fresh each request
- **Shared state**: Group mapping (TTL cache in memory or Redis), response cache (Redis or in-memory LRU)
- **Persistent state**: Onyx metadata (Postgres), index (OpenSearch), documents (MinIO)

### Document Ingestion Flow

1. **Connector** → SharePoint Graph API or other source
2. **Background worker** → Chunk documents (512 tokens default), extract metadata
3. **Embeddings** → Send to model-server, get vectors
4. **Index** → Store in OpenSearch (vector + BM25 lexical)
5. **Files** → Store in MinIO (S3-compatible)
6. **ACL sync (opt-in)** → If Graph ACL enabled, read SharePoint permissions, write to `doc_acl.json` (via `scripts/sync-doc-acl.py`)
   - Note: In FOSS mode, ACL is NOT enforced at indexing (perm-sync = EE feature). Gateway applies ACL as output filter.

### Actions / Custom Hook Flow

1. **Assistant calls Onyx Custom Action** → Invokes `onix-actions` microservice
2. **Caller authentication** → Validate HMAC or JWT signature (`actions/app/caller_identity.py:resolve_caller`)
3. **Admin control** → Check kill-switch, feature flags (`actions/app/admin_state.py:is_allowed`)
4. **Action execution** → Audit, docgen, task, notification, etc.
5. **Audit log** → Record outcome with HMAC chain (`actions/app/audit_log.py:append_audit`)
6. **Response** → Return result to assistant (or error 403 if blocked)

## Key Abstractions

**Principal:**
- Purpose: Authenticated user identity (user_id, UPN, group memberships, source)
- Examples: `access-gateway/app/identity.py:Principal`
- Pattern: Immutable DTO resolved once per request; groups cached by TTL

**GroupMap:**
- Purpose: Mapping group IDs (Entra) → authorized Onyx Document Set IDs
- Examples: `access-gateway/app/mapping.py:GroupMap` (loaded from JSON)
- Pattern: Static after app init, reloaded async via background task on file change

**Cache:**
- Purpose: Avoid redundant LLM calls for identical questions in same perimeter
- Examples: `access-gateway/app/cache.py:Cache` (builder pattern)
- Pattern: HMAC-key deterministic lookup, with semantic similarity tier (opt-in), fail-soft on Redis error

**DocACL:**
- Purpose: Document-level access control (who can see which citations)
- Examples: `access-gateway/app/doc_acl.py:CompositeDocACL` (combines static JSON + Graph)
- Pattern: Filter citations post-retrieval, never mutualizes cache entry across ACLs

**Guardian (Guardrail Post-Filter):**
- Purpose: Detect unsafe outputs (hallucinations, prompt leaks, exfil, injection)
- Examples: `access-gateway/app/guardrail.py:post_filter`, `has_citation`, `relays_exfil_link`
- Pattern: Stateless, deterministic heuristics (not LLM-based → injection-proof)

**AuditLog (HMAC-Chained):**
- Purpose: Tamper-evident audit trail of access decisions
- Examples: `actions/app/audit_log.py` (append-only, HMAC chained)
- Pattern: Each entry signed with HMAC(prev_hash + entry), verification via replay

## Entry Points

**Gateway HTTP:**
- Location: `access-gateway/app/main.py:app` (FastAPI application)
- Triggers: HTTP request to `/health`, `/metrics`, `/v1/authorized-document-sets`, `/v1/chat/send-message`
- Responsibilities: Route request, resolve identity, enforce RBAC, proxy to Onyx, apply guardrails, stream response

**Onyx API:**
- Location: `docker-compose.yml` service `api_server` (onyxdotapp/onyx-backend:4.1.1)
- Triggers: HTTP request to `/chat/`, `/search/`, `/admin/`, etc.
- Responsibilities: Query OpenSearch, generate via Ollama, manage sessions

**Onyx Background:**
- Location: `docker-compose.yml` service `background` (onyxdotapp/onyx-backend:4.1.1)
- Triggers: Celery tasks (indexing, scheduled jobs, webhook processing)
- Responsibilities: Ingest documents, embed, index, sync permissions (EE)

**Ollama Inference:**
- Location: `docker-compose.yml` service `ollama` (ollama/ollama:0.30.8)
- Triggers: HTTP request to `http://ollama:11434/api/generate` or `/api/embed`
- Responsibilities: Generate text or embeddings

**Onix Actions:**
- Location: `actions/app/main.py:app` (FastAPI application)
- Triggers: Onyx Custom Action (OpenAPI hook), external health checks
- Responsibilities: Audit extraction, docgen, cost tracking, admin controls, audit log

**Makefile Entry Points:**
- `make tune` → `scripts/detect-hardware.sh --apply` (auto-tune `.env` for hardware)
- `make up` → `docker compose up -d` + `scripts/pull-models.sh` (start stack + pre-pull models)
- `make verify` → `scripts/verify.sh` (end-to-end health check)
- `make rag-eval` → `cd tests/rag && python -m ragas_eval.runner` (quality gate)
- `make sync-doc-acl` → `python scripts/sync-doc-acl.py` (sync SharePoint ACL to `doc_acl.json`)

## Architectural Constraints

- **Threading:** Event loop (FastAPI/asyncio on Python 3.9+). Onyx background uses Celery (thread/process pool configurable). Actions uses asyncio for HTTP, SQLite/Postgres access serialized by application lock.
- **Global state:** Access-gateway reloads group mapping async (file watch + in-memory cache). Onyx background maintains Celery connection pool. Actions maintains SQLite/Postgres connection context manager (stateless per-request).
- **Circular imports:** None enforced by package structure (`access-gateway/app/`, `actions/app/` are flat, no cyclic dependencies).
- **Num_ctx (LLM context window):** Hardcoded in Ollama Modelfile via compose/Helm. Default Onyx 4096 is retained; no truncation by design. **Do not reduce without audit.**
- **Cache key stability:** HMAC key is deterministic across restarts → no ephemeral salt injected. Hit rate depends on stable secret + stable question normalization.
- **Fail-closed:** Identity error → 401, graph unavailable → 502, ACL error → 403 (never silent bypass).
- **Document Set scope isolation:** Cache key includes sorted authorized sets → user cannot cross-pollinate responses between roles.

## Anti-Patterns

### Mutable Shared State in Workers

**What happens:** Onyx background workers (Celery) or actions replicas modify state in memory, assuming single-process control.

**Why it's wrong:** In HA (Kubernetes with N replicas), each process has its own memory. Two replicas writing to independent SQLites creates divergent state. Multi-replica deployments require stateless services + shared DB/Redis.

**Do this instead:** 
- State lives in `Postgres` or `Redis`, never in process memory.
- Use `actions/app/db.py` abstraction (SQLite locally, Postgres when `ONIX_DB_BACKEND=postgres`).
- Audit log chaining uses application lock (`_lock` in `db.py`) for atomicity within a process; inter-process safety guaranteed by Postgres transaction isolation.

### Trusting Document Set Selection from Client

**What happens:** Code accepts `document_set_ids` from the HTTP request body without re-checking against user's authorized sets.

**Why it's wrong:** User can escalate by specifying a document set they don't belong to; gateway should not relay unapproved sets to Onyx.

**Do this instead:** 
- `access-gateway/app/onyx_proxy.py:enforce_document_sets()` OVERWRITES the request body with computed authorized sets.
- User chooses from their subset; any attempt to inject is silently replaced.

### Cache Hit Served to Different ACL User

**What happens:** User A caches a response. User B asks the same question but with a different document ACL scope and gets User A's cached response with docs B shouldn't see.

**Why it's wrong:** ACL bypass via cache collision. Violates confidentiality.

**Do this instead:** 
- Cache key includes sorted authorized Document Set IDs (see `access-gateway/app/cache.py:_perimeter_partition()`).
- Different ACL scope → different key → different cache entry.
- Additionally, after cache hit, re-apply document ACL filter to remove unauthorized citations (`access-gateway/app/doc_acl.py:filter_citations()` applies to both hits and misses).

### Guardrail Bypass via Prompt Injection in Context

**What happens:** Attacker embeds `ignore_safety=true` in a document; during retrieval, it's placed in context; LLM respects the injected instruction, bypassing guardrails.

**Why it's wrong:** LLM-based safety (if it existed) would be injectable. Guardrails are inside the LLM (manipulable).

**Do this instead:** 
- Post-filter guardrails (`access-gateway/app/guardrail.py`) are OUTSIDE the LLM, deterministic heuristics (citation check, exfil link detection, etc.).
- They run on the LLM **output**, after generation, where injection in input cannot retroactively affect the check.

### Audit Log Without Tamper Evidence

**What happens:** Log entries are sequential but not cryptographically bound. Admin deletes middle entries; no way to detect chain break.

**Why it's wrong:** Audit trail loses integrity. Compliance breach (RGPD, SOC2 audit, regulatory).

**Do this instead:** 
- Each audit entry includes HMAC(previous_hash + entry_content) (`actions/app/audit_log.py:compute_entry_hash()`).
- Verification via replay: recompute all HMACs, compare to stored chain. Any deletion or mutation breaks the chain (detected at `actions/app/audit_log.py:verify_chain()`).

### Exposing Metrics with PII

**What happens:** Prometheus metrics endpoint logs request paths with user IDs or document names as label values (e.g., `path="/audit/document-12345/user-alice"`).

**Why it's wrong:** Cardinality explosion + PII leak to monitoring system. Metrics are often less secured than logs.

**Do this instead:** 
- Metric labels use **route templates** (e.g., `path="/download/{job_id}"`), not actual values.
- Aggregate counters by endpoint pattern, not per-user or per-document.
- See `access-gateway/app/metrics.py` and `actions/app/main.py` for examples.

---

*Architecture analysis: 2026-06-18*
