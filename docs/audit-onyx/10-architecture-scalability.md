# Onyx Audit — Dimension 10: Architecture & Scalability/HA

- **Target:** Onyx (open-source RAG, ex-Danswer) — `/tmp/onyx_v411`
- **Version:** git tag **v4.1.1**, commit `33613e1a8fb4bc036b569af5ecc3c05cc3a53ce7` (grafted/shallow)
- **Size (this checkout):** 2,597 `.py` files, 1,861 `.ts/.tsx` files (`find | wc -l`). (Mission brief cites ~542K Py LOC / 1783 TS at full history.)
- **Helm chart version:** `0.5.19`, appVersion `latest` (`deployment/helm/charts/onyx/Chart.yaml:8`)
- **Auditor stance:** every claim below is anchored to `path:line` (relative to `/tmp/onyx_v411`) or a command I ran. Items I could not fully verify are marked **[unverified]**.

---

## 1. Scope

This document covers **architecture, horizontal scalability, and high-availability (HA)** only. In scope: component topology & data flow; statelessness; the official Helm chart's multi-replica correctness (migration race, beat singleton, data-tier HA); the Celery concurrency/locking model; and scale ceilings (model server, vector engine, Postgres connections). Security, RAG quality, connector breadth, and cost are other dimensions.

**Distribution tiers** (relevant throughout):
- **FOSS** (MIT, `LICENSE`): the whole `backend/onyx` + Helm chart audited here.
- **EE** (`backend/ee/`, separate license): adds permission-sync / external-group-sync Celery beat tasks, usage reporting, license checks. Confirmed by EE-gated beat entries (`backend/onyx/background/celery/tasks/beat_schedule.py:217` `check-for-doc-permissions-sync`, EE-only).
- **Cloud** (multi-tenant SaaS): gated by `MULTI_TENANT` (`backend/shared_configs/configs.py:167`) and `cloud_*` beat tasks; schema-per-tenant.

---

## 2. Component map + data flow

### 2.1 Runtime components

| Component | Role | Entry / evidence | Stateful? |
|---|---|---|---|
| **api-server** | FastAPI monolith (REST + auth + chat + admin). Also runs DB migrations on boot. | `backend/onyx/main.py:458` `get_application()`, `:317` `lifespan`; deploy `templates/api-deployment.yaml:75` | Stateless (state in PG/Redis) |
| **web-server** | Next.js frontend | `templates/webserver-deployment.yaml:13` | Stateless |
| **model-server (inference)** | FastAPI ML service: bi-encoder embeddings | `backend/model_server/main.py`; endpoint `backend/model_server/encoders.py:176` `/encoder/bi-encoder-embed` | Stateless req-wise; **models cached in-proc** (`encoders.py` `_GLOBAL_MODELS_DICT`) |
| **model-server (indexing)** | Same image, `INDEXING_ONLY=true`, separate pool | `templates/indexing-model-deployment.yaml`; flag `backend/shared_configs/configs.py:64` | Stateless |
| **Celery workers (8 pools)** | Background work (indexing, sync, pruning, deletion, user-files, monitoring, scheduled) | `backend/onyx/background/celery/apps/*.py`; 8 worker Deployments (`ls templates/celery-worker-*.yaml | grep -v hpa…` → 8) | Stateless; coordination in Redis+PG |
| **Celery beat** | Single periodic scheduler | `backend/onyx/background/celery/apps/beat.py:27` `DynamicTenantScheduler(PersistentScheduler)` | **Singleton (SPOF)** |
| **Postgres** | Relational system-of-record (no vectors) | `templates/postgres-cluster.yaml` (CloudNativePG `Cluster`) | **Stateful core** |
| **Vector/search engine** | OpenSearch (default) **or** Vespa (legacy) | §2.3 | **Stateful core** |
| **Redis** | Celery broker + result backend + distributed locks + fences + KV cache | `backend/onyx/configs/app_configs.py:527` `REDIS_HOST`; standalone in chart (`values.yaml:1003`) | **Stateful (but ephemeral by config — see §3.4)** |
| **Object store** | Raw files, indexing batches, checkpoints | `backend/onyx/file_store/file_store.py:649` `get_default_file_store()` → S3/MinIO else Postgres-large-objects | **Stateful** |
| **MCP server** | Model Context Protocol endpoint | `templates/mcp-server-deployment.yaml` | Stateless |
| **Slack/Discord bots, sandbox/code-interpreter** | Optional add-ons | `templates/slackbot.yaml`, `discordbot.yaml`, `sandbox-proxy/` | Mixed |

### 2.2 Indexing data flow (the scale-critical path)

```
Connector source
   │  (docfetching worker, concurrency=1)
   ▼
[FETCH] run_docfetching: batch (INDEX_BATCH_SIZE=16) ──► OBJECT STORE (batch_storage.store_batch)
   │                                                       └─ checkpoint every 100 batches (resumable)
   │  enqueue DOCPROCESSING_TASK (Redis)
   ▼
[PROCESS] docprocessing worker (concurrency=6): get_batch ──► chunk ──► embed ──► write
   │                                   │                         │
   │                                   │                  HTTP → indexing model-server (embeddings)
   │                                   ▼                         ▼
   │                          VECTOR INDEX (OpenSearch/Vespa)   POSTGRES (content hash, doc metadata)
```
Evidence: fetch entry `backend/onyx/background/indexing/run_docfetching.py:305`; batch store `:762`; checkpoint cadence `:807`; handoff enqueue `:788`; process pickup `backend/onyx/background/celery/tasks/docprocessing/tasks.py:1712`; pipeline `backend/onyx/indexing/indexing_pipeline.py:1220` `index_doc_batch`; embed call `backend/onyx/natural_language_processing/search_nlp_models.py:1130`; vector write `indexing_pipeline.py:1398`; PG hash persisted only after vector write `:1445`. Batch sizes: `MAX_CHUNKS_PER_DOC_BATCH=1000` (`backend/onyx/configs/app_configs.py:994`), local embed batch 8 / API embed batch 512 (`backend/onyx/configs/model_configs.py:42,44`). Embedding retry via `tenacity` 8 tries (indexing) with exp backoff (`search_nlp_models.py` retry block).

**Two-stage fetch→process via the object store is a genuinely good design**: it decouples slow connector I/O from CPU-bound embedding, persists intermediate batches (so a process-stage crash doesn't re-fetch), and supports checkpoint resume on a 1M-doc connector. This is production-grade, not POC.

### 2.3 Vector/search engine — **dual backend, mid-migration**

- Selection: `backend/onyx/document_index/factory.py:111` `get_default_document_index()`. Gates: `ONYX_DISABLE_VESPA` default **true** (`app_configs.py:394`); `ENABLE_OPENSEARCH_INDEXING_FOR_ONYX` default true (`:378`); `ENABLE_OPENSEARCH_RETRIEVAL_FOR_ONYX` (`:387`). **Default backend = OpenSearch.**
- OpenSearch is the **only** engine in the modern Helm chart (subchart dep `opensearch 3.6.0`, `Chart.yaml:34`). **Vespa is NOT in the chart** — `templates/legacy-vespa-check.yaml` actively *fails* `helm upgrade` from chart <0.5.0 to force users off the bundled `da-vespa` StatefulSet (read `legacy-vespa-check.yaml:1-40`). Vespa is now "bring-your-own external/Vespa Cloud."
- Migration machinery: `backend/onyx/background/celery/tasks/opensearch_migration/tasks.py` (Vespa Visit API → OpenSearch, continuation-token tracked in `OpenSearchTenantMigrationRecord`, `backend/onyx/db/opensearch_migration.py`). Retrieval flips per-tenant via DB flag, not env — a careful incremental cutover.
- **Multi-tenant model = single shared index with `tenant_id` filter** (no index-per-tenant), with tenant-prefixed chunk IDs to avoid collisions (`backend/onyx/document_index/opensearch/schema.py:71-100`). Avoids index explosion; relies on filter correctness for isolation.

### 2.4 Dependency graph & coupling

```
                 web ──► api-server ──┬──► Postgres (CNPG)
                                      ├──► Redis (broker+locks+cache)
   model-server(inf) ◄───────────────┤
                                      ├──► Vector index (OpenSearch/Vespa)
                                      └──► Object store (S3/MinIO/PG-LO)
   beat ──► Redis ──► 8x Celery workers ──┬──► Postgres
                                          ├──► Object store
                                          ├──► model-server(idx) ──► (embeddings)
                                          └──► Vector index
```
**Coupling observations:** Redis is the single most central dependency — broker, result backend, distributed-lock store, fence registry, and a KV cache, all in one process. Postgres is the system-of-record AND the migration-coordination substrate AND (optionally) the object store AND (optionally) the cache backend. These are the two hard cores.

---

## 3. HA / scalability analysis

### 3.1 Statelessness & horizontal scale — **app tier: good**

- **api-server / web / model-server / workers are stateless** (state externalized to PG/Redis/index/object-store). Each has a `replicaCount` and HPA/KEDA option. Confirmed: `api.replicaCount` (`values.yaml:397`), `webserver.replicaCount` (`:309`), HPA templates `api-hpa.yaml`, `webserver-hpa.yaml`, and per-worker `*-hpa.yaml` / `*-scaledobject.yaml`.
- Model-server caches models in-process but is request-stateless → safe to scale; the cost is **cold start** (lazy HF download + RoPE pre-warm on first request, `encoders.py:38-78`), 30–120s **[unverified exact timing — estimate]**.

### 3.2 The official Helm chart — maturity is HIGH for app tier, but defaults are single-instance

**Strong signals (premium-leaning):**
- 8 dedicated Celery worker pools each with its own Deployment, HPA, KEDA ScaledObject, metrics service, and ServiceMonitor (`ls templates/celery-worker-*`). This is a sophisticated, intentional separation (docfetching vs docprocessing vs light vs heavy vs monitoring vs scheduled vs user-file vs primary).
- KEDA support with scale-to-zero (`idleReplicaCount`), CPU+memory triggers, and pluggable `customTriggers` (`templates/celery-worker-docprocessing-scaledobject.yaml`).
- Data-tier shipped as proper operators/subcharts: **CloudNativePG** for Postgres, **OpenSearch** Helm chart, **redis-operator** (OT-Container-Kit), **MinIO**, ingress-nginx, cert-manager hooks. (`Chart.yaml:30-56`.)
- Prometheus ServiceMonitors + Grafana dashboards (`templates/grafana-dashboards.yaml`, `api-servicemonitor.yaml`), and a real DB-pool metrics exporter (`backend/onyx/server/metrics/postgres_connection_pool.py`).
- Worker liveness/readiness via a heartbeat-file probe (`backend/onyx/background/celery/celery_k8s_probe.py` — liveness fails if file not modified in 60s), wired in `templates/celery-worker-docprocessing.yaml:104-121`.
- `pre-delete-cleanup.yaml` hook and `legacy-vespa-check.yaml` fail-fast guard show operational care.

**POC-leaning / gaps in the SAME chart:**

1. **Every data-tier component defaults to a single instance (SPOF by default).**
   - Postgres: `postgresql.cluster.instances: 1` (`values.yaml:30`; template default also 1, `postgres-cluster.yaml:15`). No replica/failover out of the box (CNPG *can* do HA if `instances>1`, but the shipped default is 1).
   - OpenSearch: `singleNode: true`, `replicas: 1` (`values.yaml:57,64`). No shard replication by default.
   - Redis: `redisStandalone` (single), and crucially `appendonly no` + `save ""` (`values.yaml:1029-1030`) → **no persistence**; `maxmemory-policy allkeys-lru` will *evict* keys. Redis here holds Celery state and locks; eviction/restart loses in-flight task/lock state.
   - MinIO: `mode: standalone`, `replicas: 1` (`values.yaml:1041`). Single-node object store.
   - **Verdict:** the chart is HA-*capable* but ships HA-*off*. An operator must consciously raise every data-tier to multi-instance. For "enterprise-grade out of the box," this is a real gap.

2. **DB migration race across api replicas — no lock, no Job.** `alembic upgrade head` runs **inline in the api-server container command** (`templates/api-deployment.yaml:73`), not as a pre-install Job or init container with leader election. There is **no `pg_advisory_lock`** in `backend/alembic/env.py` (grep found none; `env.py:204` `do_run_migrations` just `SET search_path` + `run_migrations`). With `api.replicaCount > 1`, **all** api pods race to migrate on boot across **379** migration files (`ls backend/alembic/versions/*.py | wc -l`). Alembic's `alembic_version` row provides *some* serialization, but concurrent `CREATE/ALTER` from N pods is a known foot-gun (lock waits, partial-apply on crash). The multi-tenant path (`alembic -n schema_private`, 7 tenant migrations) iterates schemas sequentially and explicitly warns about whole-DB locking via UNION ALL (`backend/onyx/db/engine/tenant_utils.py`). **This is the single biggest multi-replica correctness risk in the chart.**

3. **Celery beat: singleton with NO leader election AND no `strategy: Recreate`.** Beat is `DynamicTenantScheduler(PersistentScheduler)` (`apps/beat.py:27`) — Celery beat assumes exactly one instance and there is **no RedBeat/lock/leader-election** (Celery agent confirmed; grep for RedBeat negative). The Deployment (`templates/celery-beat.yaml:12`) sets `replicas` but **no `strategy: Recreate`** — I confirmed `Recreate` exists in model-server/bot deployments but NOT beat (`grep -rln "Recreate\|strategy:" templates/` → sandbox-proxy, discordbot, indexing-model, inference-model only). During a rolling `helm upgrade`, default RollingUpdate can momentarily run **two beat pods → duplicate periodic dispatch** of every `check-for-*` task. Beat-tick locks per tenant (`CHECK_*_BEAT_LOCK`) mitigate downstream duplication, but this is a fragile arrangement. If `replicaCount` is ever set >1, it silently double-schedules continuously.

4. **api-server has no default health probes.** `startupProbe/readinessProbe/livenessProbe: {}` (`values.yaml:50-52`, only commented examples). Out of the box, K8s routes traffic to api pods before `alembic upgrade head` + uvicorn are ready, and won't restart a wedged pod. Operators must supply probes. (The chart *does* provide them for workers.)

5. **Model servers: no autoscaler, single replica, empty readiness probe.** `indexCapability/inferenceCapability replicaCount: 1` (`values.yaml:119,173`); no `*model*hpa.yaml`/`scaledobject` exists (confirmed by file list). Inference deployment has **no `--limit-concurrency`** (indexing has `--limit-concurrency 10`, `templates/indexing-model-deployment.yaml:59`), so concurrent inference embedding requests are unbounded → OOM risk on the embedding model. Readiness probes are empty → traffic before model load.

### 3.3 Concurrency model & locking — **mature, hybrid Redis+DB**

- 8 worker pools, all **threads** pool (not prefork), prefetch mostly 1. Concurrency defaults: primary 4, light 24 (prefetch 8), heavy 4, docfetching **1**, docprocessing 6, monitoring 1, scheduled 4, user-file 2 (`backend/onyx/configs/app_configs.py:618-661`; configs in `background/celery/configs/*.py`). All overridable by env.
- **Distributed locks (Redis):** primary-worker singleton lock `da_lock:primary_worker` (120s, hub-reacquired ~every 15s) (`apps/primary.py:165-182`, constant `configs/constants.py:139`); per-connector index-creation lock (`task_creation_utils.py:42`); long-op locks for indexing (3915s), pruning (3600s), permissions-sync (3600s) (`configs/constants.py:156-172`).
- **Migration to DB-based coordination (newer, better):** indexing now uses `IndexingCoordination.try_create_index_attempt()` with `SELECT … FOR UPDATE NOWAIT` on `IndexAttempt` (`backend/onyx/db/indexing_coordination.py:54-74`), and cancellation via a DB boolean instead of a Redis signal. This replaces fragile Redis fencing for the indexing path — a positive maturity trend.
- **Backpressure:** real for user-file queues (`USER_FILE_PROCESSING_MAX_QUEUE_DEPTH=500`, `configs/constants.py:183`) with task expiry (60s). General indexing throughput backpressure is weaker — the cloud path uses a frank **hack**: `CLOUD_BEAT_MULTIPLIER_DEFAULT=8.0` to "slow down task dispatch … until we have a better implementation (backpressure, etc)" (`beat_schedule.py:30-33`). Self-honest, but signals the throttling story is unfinished.

### 3.4 Single Points of Failure (default deployment)

| SPOF (default) | Why | HA path available? |
|---|---|---|
| **Postgres (1 instance)** | System-of-record; loss = full outage | Yes — CNPG `instances: N` (not default) |
| **OpenSearch (single-node)** | Search/index; loss = no retrieval/indexing | Yes — multi-node + replicas (not default) |
| **Redis (single, non-persistent)** | Broker+locks+cache; restart drops state | Partial — redis-operator can do replication; `REDIS_REPLICA_HOST` exists for reads (`app_configs.py:532`) but broker is single |
| **MinIO (standalone)** | Object store for batches/checkpoints/files | Yes — distributed MinIO / external S3 (not default) |
| **Celery beat (singleton, no LE)** | Periodic scheduler; loss pauses all scheduled work | No leader election; relies on K8s restart |
| **api-server probes empty** | Bad pods stay in rotation | Operator must add probes |

App tier (api/web/model/workers) is **not** a SPOF (multi-replica capable). The **entire data tier and beat are SPOFs in the shipped defaults.**

---

## 4. Bottlenecks & scale ceilings

1. **Postgres connection ceiling — the sharpest ceiling.** Sync+async pools each `pool_size=40, max_overflow=10` per api process (`app_configs.py:459-470`; engines `db/engine/sql_engine.py`, `async_sql_engine.py`); workers size pool ≈ concurrency + overflow (`apps/primary.py:120-122`). The DB-team agent's math: ~10 api replicas ≈ **1,000+ connections** (sync+async+readonly), before workers. **No PgBouncer is configured by default** (CNPG `Pooler` CRD ships but no pooler resource in values; TCP keepalives present, `app_configs.py:509-518`). Postgres `max_connections` (CNPG default ~100–500) will be exhausted well before the app tier's nominal scale. **Enterprise deployments MUST add a pooler** — this is under-documented in the chart.
2. **docfetching concurrency = 1.** Connector extraction is serialized per worker (`app_configs.py:625`); scaling fetch throughput means scaling *pods*, and a single huge connector still runs single-threaded fetch. Process stage (concurrency 6) can outrun fetch.
3. **Embedding / model-server.** Single replica, no HPA, inference unbounded concurrency, GPU/CPU shared model instance, known `SentenceTransformer` "Already borrowed" race retried 3× (`encoders.py:89-99`). Under heavy concurrent indexing+query embedding this is the compute bottleneck and an OOM risk. Local embed batch of 8 is small/inefficient.
4. **OpenSearch sizing.** Self-hosted default: 1 shard / 1 replica (`document_index/opensearch/schema.py` defaults; AWS-managed multi-tenant path uses 324 shards/2 replicas per the vector agent). Single-shard single-node will not hold a large-tenant corpus or survive a node loss.
5. **Redis as everything.** Broker + result backend + locks + fences + cache on one non-persistent node. At high task volume this is both a throughput and a durability bottleneck; eviction (`allkeys-lru`) can drop locks/results.
6. **Beat reload + per-tenant schedule generation** every 60s pulls the tenant list from DB (`apps/beat.py:31,161`); at very high tenant counts this tick does growing work (cloud mitigates with the 8× multiplier hack).

---

## 5. Production-readiness signals vs POC-smells

**Premium / production signals (verified):**
- Clean separation of 8 specialized worker pools with independent autoscaling + observability.
- Two-stage object-store-buffered, checkpointed, resumable indexing pipeline (`run_docfetching.py` + `indexing_pipeline.py`).
- Operator-based data tier (CNPG, OpenSearch, redis-operator, MinIO) — not raw StatefulSets.
- DB-pool Prometheus metrics with per-endpoint attribution; ServiceMonitors + Grafana dashboards.
- Hybrid locking maturing from Redis fences → DB `FOR UPDATE NOWAIT` coordination.
- Careful upgrade guards (`legacy-vespa-check`, pre-delete hook), schema-name injection validation (`sql_engine.py:50`), `statement_cache_size=0` for asyncpg under poolers.
- Multi-tenant schema-per-tenant with a deliberate Vespa→OpenSearch online migration framework.

**POC-smells / enterprise gaps (verified):**
- **Every data-tier default = single instance, no HA, Redis non-persistent.** "Enterprise out-of-the-box" fails here.
- **Migration race:** inline `alembic upgrade head` in every api replica, no advisory lock, no Job (`api-deployment.yaml:73`).
- **Beat singleton with no leader election and no `Recreate` strategy** → double-scheduling window on rolling upgrade.
- **No default health probes on api-server**; **no autoscaler & unbounded concurrency on model servers.**
- **No default connection pooler** despite ~1,000+ potential PG connections at modest replica counts.
- Backpressure for indexing is an admitted **temporary hack** (8× beat multiplier).
- docfetching single-threaded per worker.

---

## 6. Score & verdict

**Architecture & Scalability/HA score: 3.5 / 5.**

**Verdict:** The application tier and the indexing/worker architecture are genuinely production-grade and sophisticated (8 autoscalable stateless worker pools, checkpointed pipeline, DB-coordinated locking, real observability), but the **official Helm chart ships every data-tier component and Celery beat as single-instance SPOFs**, with a **multi-replica DB-migration race**, a **beat double-scheduling window**, **no default pooler/probes/model-autoscaling** — so it is "enterprise-*capable* with expert tuning," not "enterprise-*grade* out of the box." Not a POC; a strong platform that needs deliberate HA hardening before premium production use.

---

## 7. Unverified / limits

- The checkout is a **grafted/shallow clone** (`git log -1` shows `(grafted)`); I could not run full `git log`/blame history or cross-check the brief's 542K-LOC figure (local `find` counts differ — likely full-history vs this tree).
- I did **not** boot the stack; all runtime behavior (cold-start timing 30–120s, actual concurrent-migration failure, connection exhaustion) is inferred from code/config, marked **[unverified]** where timing-specific.
- No `WebSearch`/`Context7`/GitHub-issue corroboration was performed for this dimension; findings are source-of-truth from the v4.1.1 tree only.
- Alembic `alembic_version` row-lock serialization is asserted from Alembic's known behavior, not from a reproduced concurrent run — **[unverified at runtime]**.
- CNPG/OpenSearch/redis-operator subcharts can clearly do HA; I verified the **shipped defaults** are single-instance but did not exhaustively read every subchart's HA knobs.
- KEDA queue-depth (Redis-length) scaling exists only if `customTriggers` is configured; default triggers are CPU/memory — a poor signal for bursty Celery indexing **[design note, verified in template]**.
