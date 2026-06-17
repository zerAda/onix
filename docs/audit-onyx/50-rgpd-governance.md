# Onyx v4.1.1 — RGPD / Compliance / Data Governance / Multi-Tenancy Audit

> Dimension: data-residency, sovereignty, PII, encryption, right-to-erasure,
> audit logging, access governance, multi-tenancy.
> Auditor posture: senior data-governance / privacy engineer, byte-level evidence.
> Verdict at the end (§8). Unverified items flagged (§9).

---

## 1. Scope

- **Target**: real Onyx source at `/tmp/onyx_v411`, git tag **v4.1.1**, HEAD `33613e1`
  ("fix(web): sanitize docx-preview ... to release v4.1"). `backend/onyx/__init__.py`
  reads version from env `ONYX_VERSION` (defaults "Development").
- **Editions distinguished**: **FOSS/MIT** (`backend/onyx/...`, MIT `LICENSE`),
  **EE** (`backend/ee/...`, license-gated), **Cloud** (the vendor's hosted SaaS,
  `MULTI_TENANT=true` path). Onyx uses a runtime indirection
  (`fetch_versioned_implementation` / `fetch_ee_implementation_or_noop`) to swap FOSS
  stubs for EE implementations — so "the feature exists" frequently means "exists only
  in EE".
- **Method**: Read/Grep/Glob + git over the tree; two parallel deep sub-audits
  (erasure/retention; multi-tenancy); WebFetch of `docs.onyx.app` to verify (not assume)
  vendor claims. Every load-bearing claim cites `path:line` relative to `/tmp/onyx_v411`.
- **NOT verified**: live Alembic schema (only `models.py` source-of-truth read; `ondelete`
  rules not diffed against migrations), runtime behavior, the actual bytes on the wire to
  `telemetry.onyx.app` (code path read, not packet-captured).

---

## 2. Residency & telemetry — what leaves the box

### 2.1 Self-hostability — YES, fully self-hostable
All core processing (parsing, chunking, embeddings, rerank, vector index, RAG, chat
orchestration) runs in-cluster. File store, Postgres, Redis, vector DB are all
self-hostable (`deployment/docker_compose/`, `deployment/helm/`). Deployment ships
nginx + Let's Encrypt TLS termination (`deployment/data/nginx/app.conf.template.prod`,
certbot mounts in `docker-compose.prod.yml:309,349`) → **in-transit TLS available OOTB**.

Vendor doc confirms (`docs.onyx.app/security/self_hosted/data_processing`, fetched):
*"All data processing occurs within your infrastructure"*; document parsing, chunking,
embeddings, reranking happen on-prem.

### 2.2 Mandatory external calls — telemetry is the only one ON by default

**(a) Anonymous telemetry — DEFAULT ON (FOSS + EE).**
- Code: `backend/onyx/utils/telemetry.py:30`
  `_DANSWER_TELEMETRY_ENDPOINT = "https://telemetry.onyx.app/anonymous_telemetry"`.
- Gate: `optional_telemetry()` returns early **only if** `DISABLE_TELEMETRY`
  (`telemetry.py:105`). `DISABLE_TELEMETRY = os.environ.get("DISABLE_TELEMETRY","").lower()=="true"`
  (`backend/onyx/configs/app_configs.py:1128-1129`) → **default false ⇒ telemetry ON**.
- Deployment defaults reinforce ON: `deployment/docker_compose/env.template:201`
  ships `# DISABLE_TELEMETRY=` (commented out); Helm `values.yaml:1473-1474`:
  `# Optional Telemetry, please keep it on (nothing sensitive is collected)? <3`
  with `DISABLE_TELEMETRY: ""`.
- Disable: set env `DISABLE_TELEMETRY=true` (confirmed by `docs.onyx.app/more/telemetry`).

**What the telemetry payload contains** (`telemetry.py:119-134` + call sites):
`record` (enum), `data`, `user_id` (a **UUID**, not email — e.g. `timing.py:80`
`str(user.id)`), `customer_uuid` (random UUIDv4 stored in KV, `telemetry.py:64-72`),
`is_cloud`. Record types (`telemetry.py:35-46`): VERSION, SIGN_UP, USAGE, LATENCY,
FAILURE, METRIC, INDEXING_PROGRESS/COMPLETE, PERMISSION_SYNC_*, INDEX_ATTEMPT_STATUS.
Representative `data`: `{"version": __version__}` (`main.py:391-393`),
`{"function": <name>, "latency": <str>}` (`timing.py:77-81`),
`{"index_attempt_id", "status", "cc_pair_id"}` (`db/index_attempt.py:272-279`),
SIGN_UP `{"action":"create"}` + user UUID (`auth/users.py:1180-1184`).
**No document content, chat messages, queries, file names, or user emails are sent**
on the anonymous channel — confirmed by reading every `optional_telemetry(` call site;
`grep optional_telemetry backend/onyx/chat/process_message.py` is **empty** (chat path
emits no telemetry).

**EE leak delta — `instance_domain` (PII-adjacent).** When `ENTERPRISE_EDITION_ENABLED`,
the payload adds `instance_domain` (`telemetry.py:128-129`), derived from the **email
domain of the first user** (`_get_or_generate_instance_domain`, `telemetry.py:75-96`:
`first_user.email.split("@")[-1]`). So EE self-host instances de-anonymize themselves by
company domain to the vendor unless `DISABLE_TELEMETRY=true`.

**(b) PostHog — EE/Cloud only, OFF unless keyed.** `backend/ee/onyx/utils/posthog_client.py:22-29`
instantiates PostHog **only if `POSTHOG_API_KEY`** is set; host default
`https://us.i.posthog.com` (`backend/ee/onyx/configs/app_configs.py:121`). All `mt_cloud_*`
helpers no-op unless `MULTI_TENANT` (`telemetry.py:152-220`). EE telemetry also forwards
the **client IP** to PostHog as `$ip` for GeoIP (`backend/ee/onyx/utils/telemetry.py:9-46`)
— but only on the Cloud/keyed path. For a self-hosted EE instance that does **not** set
`POSTHOG_API_KEY`, PostHog stays disabled.

**(c) Sentry — OFF unless DSN set.** `backend/onyx/configs/sentry.py` only attaches tags;
Sentry init is gated on a `SENTRY_DSN` (no default in `app_configs.py`) ⇒ off by default.

**(d) License control-plane — self-hosted EE paid only, optional.**
`backend/ee/onyx/server/license/api.py:142,165` calls `CLOUD_DATA_PLANE_URL/proxy/...`
to claim/refresh a paid license (after Stripe checkout). Signature verified locally with
a bundled RSA-4096 public key (`backend/ee/onyx/utils/license.py`,
`backend/keys/license_public_key.pem`). This is **not** hit by FOSS and not hit unless an
admin buys/claims an EE license. License *enforcement* (gating) for self-host is separate
(`license_enforcement_config.py`).

**(e) Model/embedding providers.** If configured to an external LLM/embedding/rerank API
(OpenAI, Anthropic, Cohere, etc.), **queries + chat history + document chunks are sent to
that provider** — vendor doc states this explicitly. Fully avoidable by self-hosting the
model server (default deployment ships a local `model_server`) and using a local LLM
(e.g. Ollama). This is operator choice, not a mandatory call.

**Net "what leaves the box by default": only anonymous telemetry to `telemetry.onyx.app`**
(metrics/version/latency/indexing+permission-sync progress, random UUIDs; EE adds your
email domain). Everything heavier (PostHog, Sentry, license server, external model
inference) is opt-in / config-driven. Telemetry is trivially killable with one env var.

---

## 3. PII / encryption / storage

### 3.1 Where content lives
- **Document content (chunks, title, body)**: in the **vector store** in **plaintext**.
  Vespa schema `backend/onyx/document_index/vespa/app_config/schemas/danswer_chunk.sd.jinja:37-50`
  defines `field title type string {...}`, `field content type string { indexing: summary | index }`,
  `field content_summary ...` — i.e. raw text indexed/stored, no app-layer encryption.
  OpenSearch is the **default** engine (`ONYX_DISABLE_VESPA` default **true**,
  `app_configs.py:394`); same plaintext-content model.
- **Original files** (uploads, connector blobs): object storage. Default backend is **S3/MinIO**
  (`get_default_file_store`, `file_store.py:649-679`; `FILE_STORE_BACKEND=s3` default per the
  docstring at `:657`), with GCS and Postgres-large-object alternatives. Vendor doc names
  **MinIO**.
- **Chat history (sessions, messages)**: **Postgres**, plaintext.
  `ChatMessage.message: Mapped[str] = mapped_column(Text)` (`backend/onyx/db/models.py:2840`);
  `ChatSession` at `models.py:2721`.

### 3.2 Encryption at rest — the headline FOSS gap
- **FOSS = NO application-level encryption, by design.**
  `backend/onyx/utils/encryption.py:16-30`: `_encrypt_string` returns `input_str.encode()`
  and logs *"MIT version of Onyx does not support encryption of secrets."* `_decrypt_bytes`
  symmetric. So in FOSS even **connector credentials / API keys** are stored **unencrypted**
  in Postgres (only "encrypted" at the bytes level = identity function).
- **EE = AES-CBC for secrets only.** `backend/ee/onyx/utils/encryption.py:33-86` implements
  real AES (128/192/256, CBC, random IV, PKCS7) keyed by `ENCRYPTION_KEY_SECRET`.
  `app_configs.py:153-156`: *"Encryption key secret is used to encrypt connector credentials,
  api keys, and other sensitive information ... available in Onyx EE"*. Scope is **secrets
  only** (`encrypt_string_to_bytes` call sites: `models.py:163,195,227` = credential/token
  columns; `connectors/google_utils/google_kv.py`; KV `encrypt=True`). **Document content
  and chat history are NOT app-encrypted even in EE.**
- **Self-host at-rest for content therefore = disk/volume encryption only.** Vendor doc
  confirms: self-hosted relies on *"the disk encryption of the deployment"*; AES-256 at-rest
  + TLS 1.3 is the **Cloud** offering, not self-host out-of-box.
- **In transit**: TLS available at the edge (nginx/certbot). Internal hops configurable
  (`OPENSEARCH_USE_SSL` default true `app_configs.py:348`; `REDIS_SSL` opt-in `:526`;
  Postgres `sslmode` operator-driven). Not enforced internally by default.

### 3.3 PII handling / redaction
- **No built-in PII detection/redaction of document or chat content.** No Presidio, no
  classifier. `grep` for `presidio|\bPII\b|redact` in code surfaces only **secret/credential**
  scrubbing in logs (`backend/onyx/tracing/masking.py:39-59` redacts `private_key`/`Authorization`;
  `backend/onyx/llm/utils.py:358-362` `[REDACTED]` for secrets; `process_message.py:1363+`
  `[REDACTED_API_KEY]`) and credential masking for the UI (`encryption.py:33-138 mask_string`).
- **PII redaction is a customer-supplied webhook**, not a feature. The "hook points"
  (`backend/onyx/hooks/points/document_ingestion.py`, `query_processing.py`) advertise
  *"redact PII or normalize text before indexing"* (`document_ingestion.py:99`) but Onyx only
  provides the **call-out integration point** — the redaction endpoint must be built and hosted
  by the operator. Out of the box, nothing scrubs PII from indexed content.

---

## 4. Erasure (art.17) / retention (art.5)

(Byte-verified; `models.py` has 84 `ondelete="CASCADE"` declarations.)

### 4.1 Document / connector deletion — FULLY PURGED if uniquely owned, else PARTIAL
- Orchestration: `backend/onyx/background/celery/tasks/connector_deletion/tasks.py`
  (monitor) → per-doc `document_by_cc_pair_cleanup_task`
  (`backend/onyx/background/celery/tasks/shared/tasks.py:75`).
- **Refcount gate**: `get_document_connector_count` (`shared/tasks.py:118`); `count==1` ⇒ DELETE,
  `count>1` ⇒ only strip this cc_pair's ACL (`shared/tasks.py:119-208`). **A document indexed
  by ≥2 connectors is NOT erased when one connector is deleted.**
- Vector purge — **FULLY PURGED both engines**: Vespa `VespaIndex.delete`
  (`document_index/vespa/vespa_document_index.py:771`) → `delete_vespa_chunks`
  (`vespa/deletion.py:23-36`); OpenSearch `delete_by_query` scoped tenant+doc, raises if not all
  deleted (`opensearch/opensearch_document_index.py:504`, `client.py:983-988`).
- Postgres purge — **FULLY PURGED**: `delete_documents_complete__no_commit`
  (`backend/onyx/db/document.py:975-1018`) deletes KG entities, ChunkStats, DocByCC, feedback,
  tags, and the Document row.
- File purge — **FULLY PURGED** (all 3 backends delete blob **and** `file_record`:
  `file_store.py:492-508`, `postgres_file_store.py:247-256`, `gcs_file_store.py:302-310`),
  wired via `delete_files_best_effort` (`db/document.py:1036` → `file_store/staging.py:71-91`).
- **Residual gaps**: (a) shared docs (refcount>1) retained; (b) `SearchDoc` history snapshots
  persist — `SearchDoc.document_id` is a plain `String`, **no FK** to `document.id`
  (`models.py:3012`), retaining `semantic_id/link/blurb/doc_metadata/primary_owners/
  secondary_owners` (`models.py:3014-3034`) after the source doc is gone (potential PII in chat
  history); (c) reliability: DB commits before best-effort file delete, which swallows
  exceptions (`staging.py:83-91`) ⇒ transient S3/GCS error → orphaned blob, no auto-retry for
  promoted/user files.

### 4.2 User right-to-erasure — PARTIAL, and silently degrades to soft-delete (key finding)
- Endpoints (`backend/onyx/server/manage/users.py`, **admin-only**):
  `PATCH /manage/admin/deactivate-user` = **SOFT** (`is_active=False`, `users.py:637-638`);
  `DELETE /manage/admin/delete-user` = **HARD**, but **refuses unless already deactivated**
  (`users.py:680-684`).
- **No self-service / data-subject erasure endpoint, no anonymization endpoint**; no EE GDPR
  route (`backend/ee/onyx/server/manage/` has only `standard_answer.py`).
- Hard-delete fn `delete_user_from_db` (`backend/onyx/db/users.py:510-546`):
  `db_session.delete(user)` (real delete), manually handles oauth/SAML/groups, and
  **anonymizes (not deletes)** `DocumentSet.user_id`/`Persona.user_id` → None (org-owned).
- Chat history on user delete: **CASCADE — purged** (`ChatSession.user_id` ondelete=CASCADE,
  `models.py:2727-2729`; `ChatMessage` deleted via `cascade="all, delete-orphan"`,
  `models.py:2784-2789`). Many other PII tables also CASCADE (Memory, Notification, Credential,
  PAT, …).
- **🔴 ERASURE-BLOCKING FKs (verified):** `ApiKey.user_id` (`models.py:502`),
  `UserProject.user_id` (`models.py:4793`), `UserFile.user_id` (`models.py:4821`) are
  `ForeignKey("user.id"), nullable=False`, **no `ondelete`** (⇒ NO ACTION/RESTRICT) and are
  **not handled** in `delete_user_from_db`. For any user who owns a file, project, or API key,
  `DELETE user` raises an FK violation → endpoint catches, rolls back, returns 500
  (`users.py:704-707`). Net: **hard delete fails; user remains soft-deleted only**, with
  `email`, `personal_name` (`models.py:349`), `personal_role` (`:350`), `hashed_password`
  **retained indefinitely** (the `User` model has no soft-delete column; only `is_active`).
  → This is a direct **art.17 defect** for the common case of an active user with files.

### 4.3 Retention
- **Chat retention: EE-only, OFF by default.** No retention job in FOSS beat schedule
  (`backend/onyx/background/celery/tasks/beat_schedule.py` cleanup targets checkpoints/index
  attempts/sandboxes only). EE `CHECK_TTL_MANAGEMENT_TASK`
  (`backend/ee/onyx/background/celery/tasks/beat_schedule.py:34-41`) →
  `perform_ttl_management_task` (`.../ttl_management/tasks.py:25-66`) hard-deletes sessions older
  than `maximum_chat_retention_days`. Disabled unless set: `should_perform_chat_ttl_check`
  returns False if unset (`ee/onyx/background/celery_utils.py:15-16`); default `None`
  (`backend/onyx/server/settings/models.py:67`).
- `query_history` (EE) is export/read-only — no deletes against chat tables.

### 4.4 User-initiated chat deletion — SOFT by default
- `delete_chat_session` (`backend/onyx/db/chat.py:343-369`): `hard_delete=HARD_DELETE_CHATS`;
  soft path sets `chat_session.deleted=True` (`chat.py:367`). `HARD_DELETE_CHATS` default
  **False** (`backend/onyx/configs/chat_configs.py:60-63`). Endpoint
  `DELETE /delete-chat-session/{id}` defaults to soft (`chat_backend.py:509-521`). Soft-deleted
  content persists indefinitely in FOSS (no retention cron) and stays admin-readable via
  `include_deleted` (`chat.py:75-91`).

---

## 5. Audit logging + access governance

### 5.1 Audit logging — **DOES NOT EXIST (neither FOSS nor EE)** — decisive gap
- `grep -rniE "audit_log|AuditLog|audit_event|AuditEvent|audit_trail" backend/onyx backend/ee`
  (excluding tests) returns **only** an unrelated Salesforce string (`connectors/salesforce/
  blacklist.py:270 "setupaudittrail"`). **There is no access/audit log table, no who-saw-what
  trail, no immutable mutation log anywhere in the codebase.**
- This fork's own internal design docs confirm it is *aspirational*: `docs/craft/.../skills_plan.md:1164,1565`
  defers an audit log to "V1.5" and says SOC2 audit trail *"should land in Onyx's existing audit
  log infra"* — but that infra **does not exist** (the grep above). `docs/craft/.../search-requirements.md:181`
  similarly assumes "the existing audit log path" that is not present.
- Closest artifacts (NOT audit logs): EE `query_history`
  (`backend/ee/onyx/db/query_history.py`) = analytics/feedback export of chat sessions
  (filter by time/feedback), and `ONYX_QUERY_HISTORY_TYPE` (NORMAL/ANONYMIZED/DISABLED,
  `app_configs.py:107`, enum `constants.py:323-326`). Useful for analytics, **not** an
  access-audit trail of "user X viewed document Y at time T."
- **Verdict: no audit logging in any edition.** For regulated/RGPD sectors needing
  accountability (art.5(2)) this is a hard blocker; operators must build it externally from app
  logs.

### 5.2 Access governance
- **Document-level ACL — FOSS baseline exists, default-deny at query layer.** Every doc carries
  an `access_control_list`; every search filters on it (Vespa
  `vespa_request_builders.py:180-184`; OpenSearch `opensearch/search.py:227+`). A user's ACL =
  `{prefix_user_email(email), PUBLIC_DOC_PAT}`; anonymous = `{PUBLIC_DOC_PAT}` only
  (`backend/onyx/access/access.py:114-127`). Unmatched ⇒ not returned (default-deny is real).
- **External permission sync + group sync — EE-ONLY.** Mirroring source ACLs/groups
  (Confluence, Slack, Google Drive, SharePoint, Jira, Teams, Gmail, Salesforce, GitHub) and
  post-query censoring live entirely under `backend/ee/onyx/external_permissions/`
  (e.g. `confluence/doc_sync.py`, `group_sync.py`, `post_query_censoring.py`). FOSS
  `_get_access_for_document` returns `user_groups=[]` (`access.py:42-50`) and
  `source_should_fetch_permissions_during_indexing` is a noop without EE
  (`access.py:137-146`, `fetch_ee_implementation_or_noop`). So **in FOSS, connector docs are
  indexed as public or owner-only — you cannot enforce the source system's per-user
  permissions**; that fidelity requires EE.
- **Auth on by default; cannot be disabled in v4.1.1.** `AUTH_TYPE` default `BASIC`
  (`app_configs.py:132-136`); `AUTH_TYPE='disabled'` is **explicitly rejected**
  (`backend/onyx/auth/users.py:177-179`: *"no longer supported. Using 'basic' instead"*) — an
  earlier POC-grade footgun removed. RBAC roles + SSO (OIDC/SAML/Google) supported; SAML/SCIM
  are EE (`backend/ee/onyx/db/saml.py`, `scim.py`).

---

## 6. Multi-tenancy isolation

(Byte-verified by sub-audit.)

- **Architecture: Postgres SCHEMA-PER-TENANT** (shared DB, one schema per tenant).
  `shared_configs/configs.py:167` `MULTI_TENANT` flag; default schema `"public"`
  (`:171-174`); prefix `tenant_` (`:186`). Schema set per session via SQLAlchemy
  `schema_translate_map = {None: tenant_id}` (`backend/onyx/db/engine/sql_engine.py:462-465`;
  async `async_sql_engine.py:132-136`). Tenant id = schema name, **validated** against
  `^[a-zA-Z0-9_-]+$` (`sql_engine.py:50-54`, raises 400 on bad id `:449-483`) and stricter
  UUID/format checks (`tenant_utils.py:16-31`, explicitly to prevent SQLi / dropping `public`).
- **Context propagation: auth-derived, fail-closed.** `CURRENT_TENANT_ID_CONTEXTVAR`
  (`shared_configs/contextvars.py:7-11`); `get_current_tenant_id()` **raises RuntimeError** if
  unset in MT mode (`contextvars.py:37-51`) rather than defaulting. API middleware derives
  tenant from the **authenticated token** (API key/PAT carries its own tenant; Redis session;
  anon JWT), each re-validated (`backend/ee/onyx/server/middleware/tenant_tracking.py:105-184`;
  `backend/onyx/auth/utils.py:57-108`). `get_session()` raises `BasicAuthenticationError` if
  tenant resolves to `public` while MT (`sql_engine.py:478-480`).
- **Cloud-oriented + EE-gated, but the flag is not license-enforced.** MT middleware/provisioning
  live in `backend/ee/onyx/...` and attach only when `MULTI_TENANT` (`ee/onyx/main.py:96-97`);
  subscription gating is *cloud-only* (`ee/onyx/configs/multi_tenant_gating_config.py:1-10`).
  **However `MULTI_TENANT` is a bare env var with no runtime license guard** (`configs.py:167`)
  — a self-hoster can flip it on; correctness then depends on consistent env across
  api-server/Celery/vector deployment. Separate tenant migrations in `backend/alembic_tenants/`.
- **Vector store: shared index + per-query `tenant_id` filter** (not index-per-tenant).
  Vespa adds a `tenant_id` field only when multi_tenant
  (`danswer_chunk.sd.jinja:10-16`) and filters **conditionally**
  (`vespa_request_builders.py:173-174`). OpenSearch `TENANT_ID_FIELD_NAME="tenant_id"`
  (`opensearch/schema.py:55`), filter conditional on `tenant_state.multitenant`
  (`opensearch/search.py:1291-1294`), with a **fail-closed** validator
  (`document_index/interfaces_new.py:61-65` raises if multitenant & no tenant_id).
- **🟠 Soft isolation risk (Vespa fail-open):** explicit unaddressed TODO at
  `vespa_request_builders.py:172`: *"# TODO: add error condition if MULTI_TENANT and no
  tenant_id filter is set."* Unlike OpenSearch, Vespa does **not** assert the filter; if
  `filters.tenant_id` were ever empty under MT, the query runs **without tenant scoping** ⇒
  cross-tenant retrieval over the shared index. Normal path always supplies it
  (`context/search/pipeline.py:143`), so it relies on caller discipline.
- **Celery tenant scoping is explicit and reset per task** (`app_base.py:85-103`,
  `finally` resets contextvar *"so it does not leak into any subsequent tasks"*). Beat fan-out
  iterates validated tenant ids. `git log` grep for cross-tenant/tenant-leak issues = **clean**
  (no such commits at this tag); in-code "leak" mentions are all preventive.
- **🟠 Model-server header trust:** `add_onyx_tenant_id_middleware`
  (`backend/onyx/utils/middleware.py:21-34`) trusts raw `X-Onyx-Tenant-ID` **unvalidated**, but
  is registered **only on the model server** (`model_server/main.py:120`), which holds no tenant
  DB/vector data — safe as wired, but a risk if that service is ever exposed/repurposed.

---

## 7. Production signals vs POC-smells

**Production-grade signals**
- Auth on by default and **cannot be disabled** (`users.py:177-179`); RBAC + SSO; SAML/SCIM (EE).
- Real schema-per-tenant isolation with input validation, fail-closed tenant resolution, and
  per-task Celery tenant reset — mature MT engineering.
- Default-deny document ACL enforced on every vector query (both engines); OpenSearch
  fail-closed tenant validator.
- Proper deletion orchestration: vector + Postgres + object-store purge wired, refcounting,
  retry/dirty-reconciliation on index-delete failure.
- AES credential encryption + RSA-4096 signed licenses (EE); key-rotation script
  (`backend/scripts/reencrypt_secrets.py`, `db/rotate_encryption_key.py`).
- Responsible-disclosure `SECURITY.md`; TLS shipped in prod compose; secret masking in logs.
- Vendor holds **SOC 2 Type II** (verified via website) for the Cloud product.

**POC-smells / governance gaps for an RGPD-sensitive enterprise**
- 🔴 **No audit logging anywhere** (FOSS or EE) — no who-saw-what; internal docs treat it as
  not-yet-built. Disqualifying for regulated accountability (art.5(2)) without external tooling.
- 🔴 **FOSS encrypts nothing at app level** — even credentials are stored plaintext
  (`encryption.py:16-30`). Real secret encryption is EE-only and still **excludes document
  content + chat history** in every edition.
- 🔴 **User hard-delete is broken for the common case** — FK-blocked by
  `UserFile/UserProject/ApiKey` (`models.py:4821,4793,502`), silently degrading to soft-delete
  and **retaining email/PII indefinitely** (art.17 defect).
- 🟠 **External per-user permission fidelity is EE-only** — FOSS cannot mirror source-system
  ACLs (docs become public/owner-only).
- 🟠 **PII redaction is a DIY webhook**, not a feature; no built-in detection.
- 🟠 **Telemetry default-ON** (and EE leaks your email domain) — privacy-by-default failure,
  though one env var fixes it.
- 🟠 **Vespa tenant filter fail-open TODO** and **unvalidated model-server tenant header** —
  latent cross-tenant risks (currently mitigated by call paths, not by enforcement).
- 🟠 **Retention OFF by default and EE-only**; soft-deleted chats linger.
- 🟠 **Self-host at-rest = disk encryption only**; AES-256 at-rest is Cloud-only.

---

## 8. Score & verdict

### Score: **2.5 / 5** for RGPD / data-governance readiness

| Sub-dimension | Score /5 | One-line rationale |
|---|---:|---|
| Data residency & sovereignty | 4.0 | Fully self-hostable; only killable anonymous telemetry leaves by default |
| Telemetry / privacy-by-default | 2.5 | Default-ON; anonymous (no content) but EE leaks email domain |
| PII handling / redaction | 1.5 | No built-in redaction; DIY webhook only |
| Encryption (at rest, app-level) | 1.5 | FOSS none; EE secrets-only; content plaintext everywhere |
| Right to erasure (art.17) | 2.0 | Doc deletion solid; **user erasure FK-broken → PII retained** |
| Retention (art.5) | 2.0 | EE-only, OFF by default, soft-delete lingers |
| Audit logging | **0.5** | **Does not exist in any edition** |
| Access governance | 3.0 | FOSS default-deny ACL good; per-user source perms EE-only |
| Multi-tenancy isolation | 3.5 | Strong schema-per-tenant; Vespa fail-open TODO + header trust |

**Verdict — Capable platform, NOT production-ready *premium* for an RGPD-sensitive
enterprise on the FOSS edition; conditionally acceptable only on EE with hard compensating
controls.** Onyx shows genuinely mature engineering (tenant isolation, deletion plumbing,
default-deny ACL, auth-by-default). But for a privacy-regulated deployment it has **three
near-disqualifying governance gaps**: (1) **no audit trail in any edition** — fatal for
art.5(2) accountability and any SOC2/ISO-style control without bolting on external logging;
(2) **encryption-at-rest of actual content is absent** (FOSS encrypts nothing; EE encrypts
only secrets) — you must rely on infra disk encryption and accept plaintext content in
Vespa/OpenSearch/Postgres; and (3) the **user right-to-erasure path is functionally broken**
(FK violation for any user with files/projects/keys), leaving identifiable PII behind. The
**SOC2 Type II + GDPR claims on the vendor site apply to Onyx *Cloud*, not to your
self-hosted instance** — self-host compliance is entirely the operator's burden and the docs
explicitly do **not** cover encryption-at-rest, audit logging, or erasure. **Self-host FOSS:
treat as POC / internal-only for non-sensitive data. Self-host EE: viable only with external
audit logging, enforced disk/volume encryption, a patched user-deletion path, configured
retention, `DISABLE_TELEMETRY=true`, and DPAs with any external model provider.**

---

## 9. Unverified items / limits

- **`ondelete` cascade rules read from `backend/onyx/db/models.py` only** — the live schema is
  set by Alembic migrations (`backend/alembic`, `backend/alembic_tenants`), not exhaustively
  diffed. The FK-block finding (§4.2) is from the model source-of-truth; a migration could in
  principle differ (none found suggesting otherwise).
- **Telemetry payload contents inferred from code paths**, not packet-captured against
  `telemetry.onyx.app`. No document/chat content is referenced at any `optional_telemetry` call
  site, but the live endpoint behavior was not observed.
- **Vendor compliance claims** (SOC 2 Type II, AES-256 Cloud at-rest, TLS 1.3) taken from
  `onyx.app` / `docs.onyx.app` (WebFetch) — **not independently audited**; no DPA / sub-processor
  list was retrievable here (the data_processing doc does not enumerate sub-processors).
- **EE behavior** (`backend/ee/...`) read as source; not run. PostHog/Cloud-only paths
  (`mt_cloud_*`, marketing PostHog, `$ip` GeoIP) confirmed gated by `MULTI_TENANT` /
  `POSTHOG_API_KEY` but not exercised.
- **Internal cross-service TLS** (Postgres/Redis/OpenSearch) is operator-configurable; default
  enforcement not asserted beyond the flags cited.
- Web/version specifics: audit at git **v4.1.1** / `33613e1`; the `docs/craft/` design docs are
  this fork's planning artifacts and describe intended (not shipped) audit/retention work.
