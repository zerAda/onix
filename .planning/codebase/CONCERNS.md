# Codebase Concerns

**Analysis Date:** 2026-06-19

## Overview

This is a refresh audit following 73 commits merged to `main` that resolved numerous prior concerns (CVE pypdf→6.13.3, monitoring hardening, backup/restore prod-awareness, RGPD S3 erasure, secrets-at-rest wiring, gitleaks pre-commit hook, bandit on scripts). **All previous P0 blockers have been resolved** (cf. `docs/audit-reality/_VERDICT.md` §4). The codebase is currently **production-ready for mono-poste and single-machine prod deployments**; HA/Azure pathway is quasi-ready with documented workarounds.

This document reflects **only open concerns that remain** as of the 2026-06-18/19 audit cycle, categorized by impact and mitigation urgency.

---

## Tech Debt

### Fabric ACL Module — New Surface, Untested in Live Tenant

**Status:** Recently integrated into `access-gateway/`; production code path open.

- **Issue:** The `access-gateway/app/fabric_acl.py` and `fabric_client.py` modules provide RBAC for Microsoft Fabric (parallel to SharePoint Graph ACL). This is a **new surface** not yet hardened in live tenant conditions.
  - **No live tenant testing** (only mock/offline tests: `test_fabric_acl.py`, `test_fabric_client.py`)
  - **Pagination complexity:** Three different REST formats (Fabric `continuationToken`, OData `@odata.nextLink`, ADLS `x-ms-continuation`) — subtle differences, hard to maintain consistency.
  - **OneLake securityPolicy marked PREVIEW** by Microsoft — may return 404 or 403 on untested tenants.
  - **Gold-scope filtering** (`is_gold_path`) is new gatekeeping mechanism (fail-closed by design, but untested against real Power BI / OneLake hierarchies).
  - **No rate limiting** on Fabric/Graph API calls — could overwhelm tenant if user queries in a tight loop.

- **Files:** 
  - `access-gateway/app/fabric_acl.py:1-438` (ACL decision logic)
  - `access-gateway/app/fabric_client.py:1-500+` (API client)
  - `access-gateway/app/config.py:210-220` (Fabric gold config)
  - `access-gateway/tests/test_fabric_acl.py`, `test_fabric_client.py` (mocks only)

- **Impact:** A tenant with real Fabric workspace / OneLake could expose read permissions not matched by the offline test suite. Fail-closed posture (refuse access on error) mitigates, but **availability loss is real** if API changes or OneLake preview behavior diverges. **Customers will perceive this as broken search** when they have legitimate Fabric access.

- **Improvement path:**
  1. **Live tenant integration test** (sandbox Fabric workspace) before production use.
  2. Document exact API versions and preview states for each audience.
  3. Add rate-limiting: `GATEWAY_FABRIC_RATE_LIMIT_PER_MINUTE=100` with sliding window counter.
  4. Add observability: counter `fabric_acl_decisions_total{grant,deny,error,workspace_id}`.

---

### Ralph Autonomous Loop — Self-Modifying System Without Circuit-Breaker

**Status:** Operational; iterates scopes via `ralph/loop.sh` — monitoring inadequate.

- **Issue:** The Ralph autonomous iteration system (6 scope agents, parallel boucles, criteria A1–A7) is powerful but introduces a **new class of system risk: self-modification without human observation**.
  - Ralph pulls backlog from `docs/audit-reality/<scope>.md` and iterates **automatically**.
  - Commits are pushed to origin/main **after gate passage** (no human review step).
  - If a scope agent encounters an **ambiguous failure state** (gate error that's not binary), it may retry idempotently or stop; **no circuit-breaker** to prevent cascading re-runs or state corruption.
  - State files (`ralph/state/<scope>.md`) guard against divergence (read-only on entry), but a **corrupted state file could halt a scope indefinitely** with no alerting.

- **Files:** 
  - `ralph/loop.sh` (orchestration)
  - `ralph/ORCHESTRATION.md` (criteria)
  - `ralph/scopes/*.md` (scope prompts)
  - `ralph/state/*.md` (per-scope journals)

- **Impact:** 
  - **Low frequency but high blast radius:** A gate failure (e.g., `bandit` discovering new medium-severity issue) could stall the Ralph loop, causing manual intervention.
  - **Audit trail fragility:** If gates (`.pre-commit-config.yaml`, `.gitleaks.toml`, `Makefile` rules) change unexpectedly, the loop could orphan commits in a half-fixed state.
  - **Configuration drift:** If an agent's scope prompt diverges from its state file (due to doc update), the agent could re-fix issues that were already resolved.

- **Improvement path:**
  1. **Circuit-breaker:** If a Ralph iteration fails gates **3 times consecutively** on the same scope, emit alert + pause loop (require manual `ralph/retry.sh <scope>`).
  2. **Gate snapshots:** Serialize `make test` output to `ralph/gate-snapshot-<scope>-<timestamp>.log` before each run; fail loop loudly if gates regress.
  3. **Observability:** Ralph emits Prometheus metrics on each iteration start/success/failure for alerting.
  4. **Human loop:** Add a daily digest email: "Ralph iterated scopes X,Y,Z; commits A,B,C pushed; gate changes: D,E".

---

## Known Bugs

### (None at P0 currently open)

All documented P0-level bugs from prior audit-reality cycles have been resolved. See `docs/audit-reality/_VERDICT.md` §4 for resolved P0 backlog:
- ✅ Actions secrets WS2 wiring (Helm helper)
- ✅ Actions object-store S3 config
- ✅ RGPD S3 erasure endpoint
- ✅ Monitoring `/metrics` endpoint discovery

---

## Security Considerations

### Fabric ACL — Tenant Isolation Untested (Multi-Workspace Risk)

**Risk:** Fabric workspaces in a multi-tenant customer scenario.

- **Issue:** The `fabric_acl.py` module assumes a **single gold workspace** is configured (`GATEWAY_FABRIC_GOLD_WORKSPACE_ID`, `GATEWAY_FABRIC_GOLD_ITEM_ID`). If a customer has **multiple workspaces** (dev/staging/prod separated in Fabric):
  - A user querying from one workspace could potentially retrieve **cached results from another workspace** if cache key does not include workspace isolation.
  - The cache HMAC (`cache.py:263-309`) includes **document_set** but **not workspace_id** — by design, onix expects Document Set ↔ workspace to be 1:1. But this is **untested in multi-workspace scenarios**.
  - If workspace ACLs diverge, a user with access to WS#1 could get a cache hit from WS#2 (stale ACL state).

- **Files:** 
  - `access-gateway/app/config.py:210-220` (Fabric gold config)
  - `access-gateway/app/cache.py:263-309` (cache key composition)
  - `access-gateway/app/fabric_acl.py:66-73` (gold scope check)
  - `access-gateway/app/main.py:483-502` (cache hit + ACL re-check)

- **Current mitigation:** Fail-closed default (`GATEWAY_DOC_ACL_DEFAULT_POLICY=deny`); if workspace not explicitly allowed, access is denied. But if a site is configured wrong, the cache could serve **cross-workspace data silently**.

- **Recommendations:**
  1. Add **explicit document-to-workspace mapping validation** in `fabric_acl.py` (similar to `graph_acl.py:79-88`).
  2. Consider adding `workspace_id` to cache key **if multi-workspace support is ever needed**.
  3. **Test scenario:** Configure 2 Fabric workspaces, deny access to WS#2, verify cache does not serve WS#2 results.

---

### Streaming Failure Mode — False Positives in Guardrail Rules

**Status:** Known behavior, documented; but untested at scale.

- **Issue:** In `access-gateway/app/streaming.py`, **fail-CLOSED** mode on streaming (`stream=True`) aborts the stream on guardrail trigger. This differs from non-streaming fail-OPEN mode (`doc_acl.py:424-438`). If the guardrail in `streaming.py:234-256` fires spuriously (false positive detection of prompt injection):
  - A **legitimate request hangs** (user sees "connection closed" after 3 seconds).
  - No error message is returned (streaming contract violation).

- **Files:** 
  - `access-gateway/app/streaming.py:234-256,270-276` (fail-closed abort)
  - `access-gateway/app/guardrail.py:287-310` (detection rules)
  - `docs/E2E_GUARDRAILS.md` (21/21 red-team passed on `qwen2.5:7b`)

- **Current mitigation:** 
  - Guardrail rules are **conservative** (21/21 red-team passed), but red-team was **manual/live only**, not in CI gate.
  - False positive rate unknown in production.

- **Improvement path:**
  1. Add Prometheus counter `stream_rejected_by_rule{rule}` to detect spurious fires (alert if >1% of streams).
  2. Add runbook: "If user reports stream hangs, check `onix.gateway.streaming` logs for the `rule` that fired."
  3. **Red-team scaling:** Expand red-team dataset from 21 to 50+ cases; add `make rag-eval-red-team-live` target.

---

## Performance Bottlenecks

### Semantic Cache Tier — Precision Risk if Enabled (OPT-IN but Inadequate Guards)

**Status:** OPT-IN by design (`GATEWAY_SEMANTIC_CACHE_ENABLED=false` by default), but guardrails insufficient if enabled.

- **Issue:** The semantic cache tier (`cache.py:445-565`) uses cosine similarity on embeddings to serve cached answers for "similar" questions. This is **deterministic** (no LLM involvement) but has **precision risk**:
  - Threshold `GATEWAY_SEMANTIC_THRESHOLD=0.95` is high, but a corpus of **few documents** can trigger false positives.
  - If Ollama embedding model diverges (model reloaded, bit-incompatibility), **cached embeddings become stale** without automatic invalidation.
  - **Guard anti-divergence** (`cache.py:352-419`) uses heuristics (entity MAJUSCULE, numeric markers `n:`, `m:`, `q:`, `e:`) — **not foolproof**.

- **Files:** 
  - `access-gateway/app/cache.py:445-565` (semantic index)
  - `access-gateway/app/cache.py:604-642` (Ollama embeddings client)
  - `access-gateway/app/cache.py:861-928` (semantic lookup)
  - `access-gateway/tests/test_cache_semantic.py:*` (36 unit tests, all offline)

- **Impact:** If enabled on a **factual corpus**, the risk is **incorrect answer served from cache**. Audit says this is documented as OPT-IN with "knowledge of cause" — **true**, but if a customer enables it without deep understanding, they could get **hallucinated answers passed off as cached results**.

- **Improvement path:**
  1. Add **hard TTL:** `GATEWAY_SEMANTIC_CACHE_MAX_AGE_HOURS=24` (default 24h). After 24h, semantic cache entries are **evicted** (force re-embedding).
  2. Add **divergence metric:** `semantic_cache_divergence_rejected{reason}` counter to track guard rejections.
  3. **Scale red-team:** Add test `test_cache_semantic.py::test_semantic_divergence_with_model_drift` (mock Ollama embedding drift by 0.01 cosine distance).

---

### Ollama Context Window Regression — No Health Check

**Status:** Known limitation; not actively monitored.

- **Issue:** Ollama is configured with a fixed `num_ctx` (Onyx default 4096, often larger via Modelfile). If the model is **accidentally pulled with smaller context** (different quantization tier):
  - Longer documents are **truncated at retrieval time** (no error).
  - User perceives **hallucination** (model was not given relevant context).
  - No runtime verification of `num_ctx`.

- **Files:** 
  - `docker-compose.yml:449-460` (Ollama config)
  - `deploy/k8s/onix-ha/values.yaml:152-161` (Helm Ollama values)
  - `scripts/pull-models.sh` (model pull logic)
  - `docker-compose.monitoring.yml:196+` (Ollama exporter)

- **Current mitigation:** `pull-models.sh` is pre-flight step, documented but **not enforced** at runtime.

- **Improvement path:**
  1. Add health check: `/health` endpoint queries Ollama's `/api/show <model>` to verify `config.num_ctx >= EXPECTED_CONTEXT_WINDOW` (fail if under).
  2. Prometheus gauge `ollama_model_context_window{model}` exported; alert if `< 4096`.

---

## Fragile Areas

### Docker Compose Prod Mode — Backup/Restore Coupling (Operational Fragility)

**Status:** P1 honnêteté concern resolved; operational fragility remains.

- **Issue:** The `make backup` / `make restore` targets (`scripts/backup.sh`, `scripts/restore.sh`) work for **mono-poste** correctly, but in **prod mode** (`COMPOSE_PROD`):
  - `backup.sh:13` uses `PROJ="onix"` and `docker compose ... stop` **without** empoiling the prod surcouche.
  - If prod services (Caddy, oauth2-proxy, gateway) are running via `COMPOSE_PROD`, the `stop` command may **not stop them** (depends on how docker compose resolves project name).
  - **Symptom:** Backup leaves Caddy/oauth2-proxy still running → **inconsistent snapshot** (data layer stopped, auth layer live).
  - On restore, Onyx boots with auth layer still running, leading to **race conditions** (auth checking before API ready).

- **Files:** 
  - `scripts/backup.sh:10-25`
  - `scripts/restore.sh:1-20`
  - `Makefile:92-96` (targets)
  - `deploy/prod/docker-compose.prod.yml` (surcouche)

- **Current mitigation:** Documented in `docs/audit-reality/deploy-ops.md:58` as ⚠️ (minor ecart); audit recommends operator manually stack the override file.

- **Improvement path:**
  1. Modify `backup.sh` / `restore.sh` to accept `--prod` flag: `./backup.sh --prod` empoils the surcouche automatically.
  2. Add **verification step** after stop: confirm Caddy not listening on `${BIND_IP}:443` (netstat/ss check).

---

### Access Gateway — Cache Invalidation Race (Static Mode)

**Status:** Documented as working; untested in failure scenarios.

- **Issue:** Cache invalidation is **TTL-based only** (`GATEWAY_CACHE_TTL_SECONDS=3600` by default). If a document's ACL is **changed via SharePoint** and then accessed again within the TTL window:
  1. Cache hit with **old (more permissive) ACL** remains possible.
  2. Post-filter (`doc_acl.py:495-502`) **does re-check the ACL on every hit**, but it re-checks against the **memoized ACL state** (`CompositeDocACL` built at gateway init).
  3. If Graph ACL is in **static mode** (`GATEWAY_DOC_ACL_GRAPH_ENABLED=false`, the default), the memoized state is **never updated** — cache hit serves **stale permissions indefinitely**.
  4. Until an operator runs `make sync-doc-acl` **and waits for TTL to expire**, the stale cache persists.

- **Files:** 
  - `access-gateway/app/cache.py:789-825` (store)
  - `access-gateway/app/main.py:483-502` (cache hit + filter)
  - `access-gateway/app/doc_acl.py:197-206` (CompositeDocACL)
  - `access-gateway/app/main.py:129-150` (acl refresh loop)

- **Current mitigation:** 
  - Dynamic mode (`GATEWAY_DOC_ACL_GRAPH_ENABLED=true`) refreshes ACL every `GATEWAY_DOC_ACL_REFRESH_SECONDS=900` s.
  - Static mode expects operator to run `make sync-doc-acl` out-of-band when SharePoint ACLs change.

- **Improvement path:**
  1. Add **grace period invalidation**: when ACL is detected as stale (newer than last load), invalidate cache entries **older than 5 minutes**.
  2. Prometheus counter `acl_stale_detected_total` to alert on mismatches.
  3. Document explicitly: "If you add/remove SharePoint permissions, run `make sync-doc-acl` and wait 1 hour for old cache to expire (or restart gateway to clear cache immediately)."

---

### Actions — Object Store Abstraction (Early Stage)

**Status:** Recently wired for HA (S3/MinIO); needs hardening.

- **Issue:** The `actions/app/objstore.py` abstracts between **local filesystem** (mono-poste) and **S3/MinIO** (HA). While the abstraction works, it's **young**:
  - **No retry with exponential backoff** (boto3 defaults: 3 retries, max 60s not applied here).
  - **Fixed timeouts, no jitter** → thundering herd risk if S3 is briefly slow.
  - `.docx` generation **writes directly to S3** (async via Celery), but **no transaction boundary** — if write fails mid-stream, an incomplete `.docx` file persists.
  - **No multipart upload** for large files (single PUT request can fail mid-transfer).

- **Files:** 
  - `actions/app/objstore.py:1-150+` (abstraction)
  - `actions/app/docgen.py:70-140` (S3 write)
  - `actions/app/celery_app.py` (task queue)
  - `actions/tests/test_stateless_backends.py` (offline mocks)

- **Impact:** **Degraded availability** if S3 is slow; **corrupted `.docx` files** if network fails mid-upload.

- **Improvement path:**
  1. Add **exponential backoff**: boto3-like retry (3 attempts, max 60s total, jitter 10–100ms).
  2. Implement **multipart upload** for `.docx` generation (5MB chunks, retries per chunk).
  3. Add health check: `GET /health` tries a test write to S3 (not on every request, but on `make verify`).

---

## Scaling Limits

### Redis — Single Point of Failure (Mono-Poste Only)

**Status:** Known acceptable limit for mono-poste; HA configuration available.

- **Issue:** In default mono-poste (`docker-compose.yml`), Redis is a **single container** (`redis:7.4`). If it crashes:
  - Cache **misses entirely** (graceful degradation; code catches exceptions).
  - Celery **broker unavailable** (ongoing `/audit/file` uploads fail).
  - **Lock mechanism unavailable** (if used, e.g., for doc-acl refresh).

- **Files:** 
  - `docker-compose.yml:437-448` (Redis)
  - `access-gateway/app/cache.py:168-222` (Redis backend exception handling)
  - `actions/app/celery_app.py` (broker)
  - `access-gateway/app/main.py:134-150` (acl refresh loop with lock)

- **Current mitigation:**
  - Documented as limitation of mono-poste.
  - HA (Helm `deploy/k8s/onix-ha`) uses **Azure Cache for Redis** (managed, zone-redundant).
  - `docker-compose.yml:437` has `restart: always` (single-container recovery).

- **Improvement path:**
  1. For mono-poste: add **Redis Sentinel** (3-node setup, higher complexity) or accept fragility as tradeoff.
  2. For HA: already addressed (Azure managed Redis).

---

### Postgres Migrations — Pre-Install Job Dependency (HA Only)

**Status:** Documented design; untested in live HA cluster.

- **Issue:** Helm chart runs Alembic migrations via a **pre-install Job** (`deploy/k8s/onix-ha/templates/migration.yaml`). If the Job fails:
  - Release install is **marked as failed**.
  - Schema is **in intermediate state** (partially migrated).
  - Rolling back requires manual `helm rollback` + schema repair.

- **Files:** 
  - `deploy/k8s/onix-ha/templates/migration.yaml`
  - `deploy/k8s/onix-ha/templates/migration-role*.yaml` (RBAC)
  - `actions/alembic/versions/` (migration scripts)

- **Current mitigation:** Alembic migrations are **idempotent** (don't re-apply if already run), so re-running is safe.

- **Improvement path:**
  1. Add **pre-flight check** in `migration.yaml`: verify schema version matches expected version before upgrading.
  2. Implement **dry-run mode**: `helm template … | grep alembic-dryrun`.

---

## Dependencies at Risk

### pypdf — Recently Pinned (CVE Resolved)

**Status:** ✅ **RESOLVED** (2026-06-19 audit)

- **Prior issue:** `actions/requirements.txt` had `pypdf<6.11` due to CVE-2024-XXXXX.
- **Current state:** Pinned to `pypdf==6.13.3` (latest secure as of 2026-06-18).
- **Monitoring:** `pip-audit --strict` gate in CI (`ci.yml:155-160`) blocks new CVEs.

---

### Onyx v4.1.1 — FOSS Fork Risk (Architectural)

**Status:** Documented limitation; monitoring needed.

- **Issue:** onix is built on **Onyx v4.1.1 FOSS** (MIT licensed). Onyx development is **community-driven**, and codebase is **not vendored** in this repo (`docs/audit-onyx/*` audits external `v411` source).
  - If Onyx upstream introduces a **breaking change** (new required config, schema change), onix may need to adapt.
  - The audit-reality files note several Onyx FOSS limitations (no audit-trail, secrets in plain text by default, no RBAC per-document at search time). These are **compensated by onix layers**, but tightly coupled.

- **Files:** 
  - `docker-compose.yml:134-190` (Onyx image pinned `danswer/danswer:4.1.1`)
  - `docs/audit-onyx/00-VERDICT.md`
  - `ARCHITECTURE.md:23-26`

- **Current mitigation:**
  - Image is **precisely pinned** (no `latest` tag).
  - Gates (`bandit`, `pip-audit`, `trivy`) catch supply-chain issues.
  - onix layers are **loosely coupled** (proxy pattern; Onyx is black box).

- **Improvement path:**
  1. **Monitor Onyx releases:** Subscribe to GitHub releases or check quarterly.
  2. Add quarterly "Onyx compatibility test" to runbook (test against v4.2.x / v5.x if released).

---

## Test Coverage Gaps

### Fabric ACL — Only Offline Mocks

**Status:** Known by design; inadequate for production.

- **What's not tested:**
  - Real Microsoft Fabric workspace with actual RBAC assignments.
  - OneLake securityPolicy PREVIEW behavior (known to return 404 on some tenants).
  - Power BI dataset permission inheritance.
  - Pagination edge cases (>999 items, continuation token expiry).
  - Rate-limit behavior under load.

- **Files:** 
  - `access-gateway/tests/test_fabric_acl.py` (52 unit tests, all mocks)
  - `access-gateway/tests/test_fabric_client.py` (38 unit tests, all mocks)
  - `access-gateway/tests/e2e/run_access_e2e.py` (e2e harness, not Fabric-specific)

- **Risk:** A tenant configuration could fail silently with a **"permission denied" error** that looks like normal deny-by-default, but is actually a **bug in pagination or error handling**.

- **Improvement path:**
  1. Create a **sandbox Fabric workspace** for integration testing.
  2. Add `tests/fabric_e2e_live.py::test_fabric_acl_real_tenant` (gated behind `FABRIC_INTEGRATION_TEST=1` env var).
  3. Run this E2E test on schedule (weekly) in CI, only if tenant credentials available.

---

### Semantic Cache — Divergence Guard Untested Against Real Models

**Status:** Guard rules documented; not red-teamed at scale.

- **What's not tested:**
  - How often the divergence guard fires on real user queries (Prometheus would track this, but counter not yet in gate).
  - False negatives: does a legitimate entity-heavy question still get cache hits when it should?
  - Embedding model drift (what happens if Ollama reloads with slightly different quantization).

- **Files:** 
  - `access-gateway/app/cache.py:352-419` (guard logic)
  - `access-gateway/tests/test_cache_semantic.py:*` (36 unit tests, all offline)

- **Improvement path:**
  1. Add `semantic_cache_divergence_rejected{reason}` counter to expose in `/metrics`.
  2. If ever deployed with semantic cache enabled, **alert if rejection rate > 5%**.

---

### Red-Team — Live Only, Not in CI Gate

**Status:** Documented limitation (audit notes this honestly).

- **What's tested in CI:** Guardrail rules against **hardcoded 21-item red-team dataset** (stored in code).
- **What's not tested:** User-generated adversarial queries, model-specific vulnerabilities (different LLM → different bypasses), international attacks (non-English injection).

- **Files:** 
  - `tests/rag/guardrail_postfilter.py` (rules)
  - `docs/E2E_GUARDRAILS.md` (21 red-team cases)
  - `tests/rag/run_live.py` (manual runner)

- **Improvement path:**
  1. Expand red-team dataset from 21 to **50+ cases** (different injection techniques, languages).
  2. Add `make rag-eval-red-team-live` target (manual, requires Ollama running).
  3. Gate: if PR changes guardrail rules, require operator approval + red-team rerun.

---

## Missing Critical Features

### Fabric ACL — No Rate Limiting (Feature Gap)

**Status:** Not implemented; could cause tenant-level issues under load.

- **Problem:** If a user repeatedly queries with high frequency, the gateway could **overwhelm the Fabric API** (each query ⇒ N Graph/Fabric calls for ACL check). No rate limiting is in place.

- **Files:** 
  - `access-gateway/app/fabric_acl.py`
  - `access-gateway/app/fabric_client.py` (no rate-limit decorators)

- **Improvement path:**
  1. Add `GATEWAY_FABRIC_RATE_LIMIT_PER_MINUTE=100` config.
  2. Use a **sliding window counter** (Redis or in-memory) keyed by `tenant_id`.
  3. Return `429 Too Many Requests` if limit exceeded.

---

### Monitoring — Dashboard for Fabric ACL Decisions (Feature Gap)

**Status:** Missing observability.

- **Problem:** If Fabric ACL starts denying requests unexpectedly, there's **no dashboard** to visualize deny rate or error causes.

- **Files:** 
  - `monitoring/grafana/dashboards/` (needs new dashboard for gateway Fabric metrics)

- **Improvement path:**
  1. Add Prometheus counter `fabric_acl_decisions_total{grant,deny,error,workspace_id}`.
  2. Add Grafana dashboard: Fabric ACL decision pie chart, error rate time series, error reasons breakdown.

---

## RGPD / Governance Concerns

### Registre Traitements — Base Légale Incomplete (Compliance Risk)

**Status:** Template provided; compliance depends on customer decision.

- **Issue:** `docs/REGISTRE_TRAITEMENTS.md` is a **gabarit** (template) that customers must complete. The **base légale** (legal basis under RGPD art. 6) is marked `TODO (décision client)` — **if left blank, onix is NOT RGPD-compliant in the customer's context**.

- **Files:** 
  - `docs/REGISTRE_TRAITEMENTS.md:23,52` (TODO markers)
  - `docs/DPIA_TEMPLATE.md:50,53` (DPIA base légale, also TODO)

- **Current mitigation:**
  - Documented honnêtement as templates (not represented as filled-out compliance).
  - Scope document references customer's DPO responsibility.

- **Improvement path:**
  1. Add pre-flight check: `make verify` includes a validator that checks `REGISTRE_TRAITEMENTS.md` contains **no `TODO (décision client)` markers** (fail if template incomplete).
  2. Runbook section: "Before go-live, ensure DPO has signed off on REGISTRE & DPIA."

---

### Audit Trail — Gateway Side NOT Chaîné (Asymmetry)

**Status:** Documented gap (audit notes this clearly).

- **Issue:** The **actions** service has full HMAC-chaîned audit trail (`actions/app/audit_log.py:88-195`), but the **gateway** side only has **pseudonymized journal** (`access-gateway/app/audit.py:34-57`). The gateway audit is **not chaîned** (no cryptographic link between records).
  - This is **asymmetry:** the gateway has high-volume access decisions (chat history), actions have lower-volume operations (file upload, erase).
  - For a full audit trail, **both layers should be chaîned**.

- **Files:** 
  - `access-gateway/app/audit.py:34-57` (pseudonymized only, no chaîning)
  - `actions/app/audit_log.py:88-195` (chaîned + verified)
  - `docs/PARITE_ENTREPRISE.md:40` (claims « audit HMAC chaîné », but gateway side is not)

- **Current mitigation:**
  - Gateway audit is fail-safe (no errors bubble up).
  - Pseudonymization prevents identity leakage.

- **Improvement path:**
  1. Extend `gateway/app/audit.py` to support optional chaîning (use `previous_hash` field in JSON log).
  2. Add verification endpoint `GET /admin/audit/verify` (parallel to actions' endpoint).

---

## Summary of Priorities

| Priority | Category | Item | Owner | Target |
|----------|----------|------|-------|--------|
| **P0** | — | (none currently open) | — | — |
| **P1** | Security | Fabric ACL: live tenant integration test + rate-limiting | Backend | v1.1 |
| **P1** | Architecture | Ralph loop: circuit-breaker + gate snapshots | DevOps | v1.1 |
| **P1** | Security | Fabric ACL: tenant isolation validation (cache key) | Backend | v1.1 |
| **P1** | Ops | Backup/restore: prod mode consistency check | DevOps | v1.1 |
| **P2** | Performance | Semantic cache: TTL-based eviction + divergence counter | Cache | v1.2 |
| **P2** | Performance | Ollama: context window health check + Prometheus gauge | LLM | v1.2 |
| **P2** | Ops | Actions object store: exponential backoff + multipart | Backend | v1.2 |
| **P2** | Testing | Fabric ACL: expand unit tests + e2e harness | QA | v1.2 |
| **P2** | Governance | Registre/DPIA: pre-flight validator for completed templates | Compliance | v1.2 |
| **P2** | Observability | Fabric ACL dashboard + Prometheus counters | Ops | v1.2 |

---

*Concerns audit: 2026-06-19*
