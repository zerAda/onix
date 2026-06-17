# Onyx v4.1.1 — Security Posture Audit

> Dimension: **Security posture** (AuthN/Z, RBAC, EE-gating, secrets/crypto, injection/SSRF, supply-chain, container hardening).
> Target: real source at `/tmp/onyx_v411`, git tag **v4.1.1** (commit `33613e1`, 2026-06-12).
> Method: byte-level evidence — every claim cites `path:line` (relative to `/tmp/onyx_v411`), real command output, or advisory ID. FOSS (MIT) vs EE (Onyx Enterprise License) vs Cloud are distinguished throughout.
> Date of audit: 2026-06-17.

---

## 1. Scope

| Item | Value |
|------|-------|
| Repo / version | `onyx-dot-app/onyx` @ `v4.1.1` (`git describe --tags` → `v4.1.1`; HEAD `33613e1`, dated 2026-06-12) |
| Backend size | ~542K Py LOC (caller-supplied); 2,591 `.py` files under `backend/` (`find backend -name '*.py' | wc -l`) |
| Licensing | Dual: code outside `ee/` dirs is **MIT Expat**; everything under `backend/ee/`, `web/src/app/ee/`, `web/src/ee/` is the **Onyx Enterprise License** (`LICENSE:1-11`, `backend/ee/LICENSE`) |
| Auth stack | `fastapi-users==15.0.4` (+ `pwdlib`/Argon2, bcrypt 5.0.0), `pyjwt==2.12.0`, `authlib==1.6.12`, `httpx-oauth==0.15.1`, `cryptography==46.0.7` |
| Editions audited | FOSS (MIT) build, EE build, and Cloud/multi-tenant paths |

Auditable artefacts present: `SECURITY.md` (real responsible-disclosure policy, GitHub Private Vulnerability Reporting, 90-day window, safe harbour), `backend/ee/LICENSE`, Dockerfiles, fully hash-pinned `requirements/*.txt`, `.github/` CI.

---

## 2. AuthN / AuthZ + the EE-gating reality

### 2.1 Authentication (mostly FOSS)

**Backbone.** `fastapi-users` 15.0.4 drives login/sessions. Auth type is selected by `AUTH_TYPE` and **defaults to `basic`** when unset/invalid (`backend/onyx/configs/app_configs.py:132-136`). Supported: `basic`, `google_oauth`, `oidc`, `saml`, `cloud` (`backend/onyx/configs/constants.py` `AuthType`).

**Hardening over legacy Danswer — auth can no longer be disabled.** `AUTH_TYPE='disabled'` is explicitly rejected and downgraded to `basic` (`backend/onyx/auth/users.py:177-180`); `AUTH_TYPE='cloud'` is rejected for self-hosted (`users.py:172-174`). This closes the old "no-auth" footgun. **Prod signal.**

**Sessions / JWT.** Two backends (`AUTH_BACKEND`): Redis-strategy or DB-strategy tokens, plus cookie transport (`users.py:47-55`). Session JWTs signed HS256 with `USER_AUTH_SECRET` (`users.py:1532-1536`).

**External JWT auth (`backend/onyx/auth/jwt.py`).** For SSO-style bearer JWTs: fetches a JWKS or PEM from `JWT_PUBLIC_KEY_URL`, matches by `kid`/`x5t`, verifies **RS256 only** (`jwt.py:141-146`) — no `alg:none`/algorithm-confusion. Caveats: `verify_aud: False` (audience not validated, `jwt.py:145`) and the JWKS URL fetch (`requests.get`, `jwt.py:37`) has no SSRF guard — but the URL is **admin-configured**, not user input, so this is INFO.

**OAuth/OIDC flow is well-built** (`users.py:2183-2600`):
- Signed **state token** (JWT, 1 h TTL) carries `next_url` + CSRF token (`users.py:2153-2160`, `2270-2275`).
- **CSRF**: double-submit cookie compared with `secrets.compare_digest` — constant-time (`users.py:2432-2437`).
- **PKCE S256** supported (`generate_pkce_pair` uses `secrets`, SHA-256 challenge — `users.py:2171-2174`).
- **Host-header poisoning explicitly defended**: callback URL built from `WEB_DOMAIN`, not `request.url_for()` (`users.py:2262-2265`).
- **WebSocket auth (voice)**: single-use 60 s Redis token + **CSWSH Origin check** with loopback-aware same-origin compare (`users.py:2044-2134`).

> **LOW — OAuth open redirect.** `next` query param → signed state → `RedirectResponse(next_url)` with **no same-origin/allowlist check** (`users.py:2267`, `2536`, `2580-2585`). Signing prevents tampering but not a crafted login link (`/auth/oauth/authorize?next=https://evil.com` lands the victim on an attacker site post-login). Mitigated by requiring a completed real OAuth login and `samesite=lax` cookies.

**"First user = admin" flow — correctly implemented, race-safe.** Password+email path forces `role = BASIC`, then upgrades to `ADMIN` only if `user_count == 0` **or** the email is in `get_default_admin_user_emails()` (`users.py:632-642`). The insert is guarded by a same-session lock + `IntegrityError` rollback for the create race (`users.py:644-696`). The OAuth path mirrors this in `SQLAlchemyUserAdminDB.create` (`backend/onyx/db/auth.py:110-115`). `get_user_count` excludes API-key/system/external users (`backend/onyx/db/auth.py:45-101`). **Note:** `get_default_admin_user_emails()` is **EE-only** — FOSS returns `[]` (`db/auth.py:34-42` + `users.py:2137-2139` "No default seeding available for Onyx MIT"); seed-config admin emails come from `backend/ee/onyx/auth/users.py:34-38`.

**Email verification.** `REQUIRE_EMAIL_VERIFICATION` defaults **off** (`app_configs.py:296-298`); when on, tokens are signed with `USER_AUTH_SECRET` and emailed (requires SMTP). Disposable-email blocking, captcha (cloud), and signup rate-limiting all run before user creation (`users.py:541-587`). Acceptable for self-hosted.

**Password handling — modern.** Hashing via `fastapi-users` `PasswordHelper` → **Argon2** (pwdlib) with bcrypt-5.0.0 verify/rehash fallback (`users.py:1272` `verify_and_update`). Complexity is configurable (`validate_password`, `users.py:805-841`) but **defaults are weak: 8-char minimum, all four character-class requirements default OFF** (`app_configs.py:138-150`). **LOW.**

**API keys & PATs — sound.**
- API keys: `secrets.token_urlsafe(192)` (`auth/api_key.py:31`, `constants.py` `API_KEY_LENGTH=192`), stored as unsalted SHA-256 (justified by 192-byte entropy; `api_key.py:41-50`); legacy `sha256_crypt` path with `salt=""` is deprecated-only (INFO).
- PATs (`auth/pat.py`): `secrets.token_urlsafe`, SHA-256 hashed, **Bearer-only**, **expirable**, and **scoped** with **fail-closed** enforcement — a scoped PAT may only reach routes carrying a satisfiable `require_permission` (`users.py:1857-1917`, `_scoped_pat_permitted_on_route`).

> **LOW — non-expiring anonymous JWT.** `generate_anonymous_user_jwt_token` mints an HS256 token explicitly with **no expiry** ("Token does not expire", `backend/ee/onyx/auth/users.py:57-64`). Only relevant where anonymous access is enabled.

### 2.2 Authorization / RBAC — mature, mostly EE for the advanced bits

**Permission model (FOSS core).** `backend/onyx/auth/permissions.py`: a granular `Permission` enum stored as a JSONB column on `User`, with an **implication graph** (`IMPLIED_PERMISSIONS`) expanded at read time, an admin short-circuit (`FULL_ADMIN_PANEL_ACCESS` ⇒ all), and a `require_permission()` FastAPI dependency that **caps the request by the authenticating token's scopes** (defense-in-depth — `permissions.py:101-128`). Role gates (`current_admin_user`/`current_curator_or_admin_user`/`current_limited_user`) layer on top (`users.py:2004-2013`). This is production-grade.

**Per-document access control (ACL).** FOSS computes a base ACL; the **EE override adds user-group and external-group ACLs** via the versioned-dispatch mechanism (`backend/onyx/access/access.py:130-146` dispatches → `backend/ee/onyx/access/access.py:183-210`).

**Permission sync (data-level access mirroring from source systems) — EE-ONLY.** There is **no FOSS `external_permissions` directory**. The entire connector permission-sync stack (doc_sync + group_sync for Confluence, Jira, Google Drive, Slack, Teams, SharePoint, GitHub, Salesforce, Gmail, plus post-query censoring) lives only under `backend/ee/onyx/external_permissions/`. FOSS entry points are no-ops (`fetch_ee_implementation_or_noop` → noop when not EE). **This is decisive:** on a pure-FOSS deployment, connector-level access controls from the source system are **not enforced** — documents are searchable by anyone with chat access subject only to the coarse base ACL.

### 2.3 The EE-gating mechanism (HOW features are gated)

**Runtime module-redirect, not compile-time stripping. EE source ships in the image.** A global `OnyxVersion` flag (`backend/onyx/utils/variable_functionality.py:20-34`) is set by `set_is_ee_based_on_env_variable()` (`variable_functionality.py:46-66`), which turns EE on if **either** `ENABLE_PAID_ENTERPRISE_EDITION_FEATURES=true` **or** `LICENSE_ENFORCEMENT_ENABLED` (which **defaults to `"true"`**). `fetch_versioned_implementation(module, attr)` then imports `ee.<module>` instead of `<module>` (`variable_functionality.py:69-119`), with `fetch_ee_implementation_or_noop` for pure add-ons. Even the FastAPI app factory is swapped at startup (`backend/onyx/main.py:741-742`). The whole `backend/ee/` tree is physically present and is `COPY`'d into the image (`backend/Dockerfile:182`).

**License enforcement = offline RSA-4096/PSS signature, pinned public key.** `backend/ee/onyx/utils/license.py:49-103` verifies `base64(JSON{payload,signature})` with `RSA-PSS(MGF1-SHA256)+SHA256` against `backend/keys/license_public_key.pem` (the only `.pem` in the repo; overridable via env `LICENSE_PUBLIC_KEY_PEM`, `license.py:29-46`). No network call is required to validate (an optional phone-home `/license/claim` to `cloud.onyx.app` exists but the **trust anchor is always the local signature check**, `ee/onyx/server/license/api.py:182-183`). Two middlewares enforce state vs tier: `license_enforcement.py` (returns **HTTP 402** on expired-past-grace or seat-overage; **fail-OPEN** on DB/cache error, `license_enforcement.py:169-172`) and `tier_gate.py` (per-path-prefix min-tier; **fail-CLOSED** to `COMMUNITY` on error, `tier_gate.py:90-98`).

> **Operator-bypassable by design (legal, not technical, barrier).** Self-hosted EE unlocks with `ENABLE_PAID_ENTERPRISE_EDITION_FEATURES=true` + `LICENSE_ENFORCEMENT_ENABLED=false` (no license needed — `ee/onyx/utils/tier.py:93-99` returns `ENTERPRISE`); or by swapping `LICENSE_PUBLIC_KEY_PEM` to a self-signed key; or by editing the shipped Python. The signature scheme only stops forging a license against *Onyx's* key. Production use without a valid seat-correct license is prohibited by `backend/ee/LICENSE` (dev/test explicitly allowed). Cloud/multi-tenant ignores these endpoints and gates via the control-plane `gated_tenants` key — the hardened path.

#### FOSS vs EE Security Feature Matrix (decisive for "premium")

| Security feature | FOSS (MIT) | EE-only | Evidence |
|---|:---:|:---:|---|
| Basic auth / sessions / password login | ✅ | | `backend/onyx/auth/users.py` |
| Google OAuth | ✅ | | `onyx/main.py` OAuth router |
| **OIDC** | ✅ | | router `onyx/main.py:635-664`; `AuthType.OIDC` |
| **SAML** | ✅ (router) | seat/JIT hooks EE | `backend/onyx/server/saml.py`; seat enforcement via `fetch_ee_implementation_or_noop` |
| External JWT (RS256/JWKS) | ✅ | | `backend/onyx/auth/jwt.py` |
| API keys / PATs (scoped) | ✅ | | `auth/api_key.py`, `auth/pat.py`, `auth/permissions.py` |
| Granular permission/role model | ✅ | | `backend/onyx/auth/permissions.py` |
| **User Groups / Curator RBAC** | | ✅ | API only at `backend/ee/onyx/server/user_group/`; tier `BUSINESS` |
| **Per-document ACL with groups / external groups** | base only | ✅ override | `onyx/access/access.py` → `ee/onyx/access/access.py:183-210` |
| **Connector permission-sync (doc + group)** | ❌ no-op | ✅ | `backend/ee/onyx/external_permissions/*` (no FOSS dir) |
| **SCIM user provisioning** | | ✅ | `backend/ee/onyx/server/scim/`; tier `ENTERPRISE` |
| **Query-history / chat audit** | | ✅ | `backend/ee/onyx/server/query_history/`; tier `BUSINESS` |
| **Usage analytics / reporting** | | ✅ | `backend/ee/onyx/server/analytics/`,`reporting/` |
| **Secrets encryption at rest** | ❌ **no-op (plaintext)** | ✅ AES-CBC | `onyx/utils/encryption.py:16-30` vs `ee/onyx/utils/encryption.py` |
| **Token rate limits / outbound webhooks** | | ✅ | `ee/onyx/server/token_rate_limits/`, `ee/onyx/hooks/` |
| SSRF protection (web/MCP/OAuth/open_url) | ✅ | | `onyx/server/security/models.py`, `connectors/web/connector.py` |
| Multi-tenant schema isolation | ✅ (core) | provisioning EE | `onyx/db/engine/sql_engine.py` |

**Verdict on premium split:** Authentication itself (incl. OIDC/SAML/JWT) is FOSS — good. But the security features that make Onyx *enterprise-trustworthy* — **at-rest secret encryption, document permission-sync, group RBAC, SCIM, and audit logging — are EE-only.** A FOSS-only deployment stores credentials in plaintext (see §3) and does not enforce source-system document permissions.

### 2.4 Multi-tenant isolation — solid

Tenant = Postgres schema. Schema is switched via SQLAlchemy `schema_translate_map` (parameterized, **not** raw `SET search_path` interpolation — `sql_engine.py:461-466`). Every session entry point validates the tenant id against a strict whitelist regex `^[a-zA-Z0-9_-]+$` (`is_valid_schema_name`, `sql_engine.py:50-54`) and raises 400 otherwise (`sql_engine.py:449,482,501`; `async_sql_engine.py:120`). Unauthenticated requests in MT mode are rejected before query (`sql_engine.py:479-480`). **No tenant-id SQL-injection or search-path-injection vector found.** Prod signal.

---

## 3. Secrets & Cryptography

### 3.1 Connector credential / secret encryption — the headline weakness

Secrets (connector `credential_json`, LLM provider `api_key`, OAuth tokens, federated creds) use `EncryptedString`/`EncryptedJson` SQLAlchemy type-decorators (`backend/onyx/db/models.py:136-241`, `Credential` at `:1898-1910`), which route through a **version-dispatched** `_encrypt_string` (`db/models.py:163`).

> **CRITICAL — FOSS build performs NO encryption.** `backend/onyx/utils/encryption.py:16-30`: `_encrypt_string` returns `input_str.encode()` (plaintext); setting `ENCRYPTION_KEY_SECRET` only logs `"MIT version of Onyx does not support encryption of secrets."`. On a pure-MIT build, all connector credentials / OAuth tokens / LLM API keys are stored in Postgres **in cleartext**.

> **CRITICAL — empty `ENCRYPTION_KEY_SECRET` (the default) silently disables EE encryption too.** Default is `""` (`app_configs.py:156`). The EE encryptor short-circuits to plaintext when the key is empty (`ee/onyx/utils/encryption.py:35-36, 52-53`), and the startup self-test only checks an encrypt→decrypt round-trip — which **passes for the no-op empty key** (`ee/onyx/main.py:85`). So an EE deployment that forgot to set the key boots cleanly and stores secrets in plaintext **with no error**. **Asymmetry:** `USER_AUTH_SECRET` *does* hard-fail startup when empty (§3.2) — `ENCRYPTION_KEY_SECRET` does not.

> **MEDIUM — EE crypto is unauthenticated AES-CBC.** `ee/onyx/utils/encryption.py:33-47`: AES-256/192/128 **CBC** with per-message `urandom(16)` IV + PKCS7, IV prepended. Sound confidentiality but **no MAC/GCM** → ciphertexts are malleable; key is truncated to ≤32 bytes (`_get_trimmed_key`). Defense-in-depth on top of Postgres ACLs, so lower priority.

### 3.2 User auth secret / JWT signing — guarded

`USER_AUTH_SECRET` defaults to `""` (`app_configs.py:270`) and signs sessions, password-reset/verify tokens, OAuth state, captcha cookies, and the anonymous JWT. **MEDIUM, mitigated:** `verify_user_auth_secret()` **hard-fails production boot** when empty (`users.py:185-210`, called at `main.py:364`); only `DEV_MODE`/`INTEGRATION_TESTS_MODE` downgrade to a warning.

### 3.3 Secrets in code & configs

- **No real committed private keys / cloud creds.** Repo-wide scans for `BEGIN PRIVATE KEY`, `AKIA…`, key files → only fake test fixtures, runtime-generated keys, and base64 image false-positives. `backend/keys/license_public_key.pem` is a **public** key (INFO).
- **HIGH — weak defaults in dev/base docker-compose:** `POSTGRES_PASSWORD:-password`, `MINIO_ROOT_PASSWORD:-minioadmin`, `S3_AWS_SECRET_ACCESS_KEY:-minioadmin` (`deployment/docker_compose/docker-compose.yml:380,526,86`, also `*.dev.yml`, `*.multitenant-dev.yml`). The **prod** compose files use fail-fast `${VAR:?...}` and ECS sources secrets from AWS Secrets Manager — good. Risk is only if base/dev compose is exposed without overrides.
- **MEDIUM — committed hardcoded secrets in Helm `values-localdev.yaml`:** `encryption_key_secret: "6767676767676767"` (exactly 16 bytes — passes the key check) and a sandbox `private_key` (`deployment/helm/charts/onyx/values-localdev.yaml:102,109`). Local-dev scoped; production `values.yaml` defaults empty. `install.sh` auto-generates `USER_AUTH_SECRET` via `openssl rand -hex 32`.

### 3.4 Crypto primitives & RNG — clean

- All MD5/SHA1 uses are `usedforsecurity=False` (request-id, advisory-lock id, content fingerprint) or protocol-required (SHA1 MSAL cert thumbprint, `# noqa: S303`). No ECB, no hardcoded IV.
- Token generation uses `secrets` throughout (API keys, PATs, SCIM, CSRF, PKCE). **LOW:** `generate_password` selects chars with `secrets.choice` but permutes with non-CSPRNG `random.shuffle` (`users.py:246`) — entropy effectively preserved.

---

## 4. Injection / SSRF / Supply-chain (severity-tagged)

### 4.1 SSRF — strong centralized framework, two real gaps

There is a **centralized SSRF library** `backend/onyx/utils/url.py` (`validate_outbound_http_url`, `ssrf_safe_get`) governed by `SSRFProtectionLevel` (`backend/onyx/server/security/models.py:12-78`):
- Blocks `localhost`, `169.254.169.254`, `fd00:ec2::254`, `metadata.{azure,google,gke}*`, `kubernetes.default*` (`url.py:16-29`); rejects non-http(s) schemes (`file://`/`gopher://` blocked, `url.py:126-129`) and embedded credentials (`url.py:143-144`).
- Resolves DNS and checks **every** resolved IP via `is_global`/`is_multicast` (`url.py:36-57`); `ssrf_safe_get` follows redirects **manually, re-validating each hop** and pins the validated IP for HTTP (`url.py:348-392, 444-477`).
- **Default = `VALIDATE_ALL`** — every outbound path blocks private IPs (`models.py:19-21`, `store.py:79`).
- **Always-on floor:** even at `DISABLED`, cloud-metadata/link-local `169.254.0.0/16` stays blocked (`models.py:46-65`) — IMDS protected regardless of config. **Prod signal.**
- **Best-in-class call sites:** MCP injects the guard at the **httpx transport**, so every SDK-generated URL incl. OAuth discovery/redirects is validated per-hop (`tools/.../mcp/mcp_ssrf.py:40-47`, `mcp_client.py:150`); `open_url`/web_search use `ssrf_safe_get` (`onyx_web_crawler.py:222-229`); OAuth token manager, SharePoint (`https_only=True`), and voice all validated.

> **MEDIUM — Custom Tools (OpenAPI actions) bypass SSRF entirely (prompt-injection → SSRF + credential replay).** `backend/onyx/tools/tool_implementations/custom/custom_tool.py:193-198` calls raw `requests.request(method, url, json=..., headers=self.headers)` with **no** `ssrf_safe_get`/`validate_outbound_http_url` and **default `allow_redirects=True`**. `self.headers` carries `Authorization: Bearer <token>` (`custom_tool.py:78-87`), so a redirect to an internal host replays the credential. The base host is admin/curator-set, but **path/query params are LLM-controlled at call time** (`custom_tool.py:171-193`, f-string `build_url` in `openapi_parsing.py:52-66`) — a malicious document can steer the LLM to drive an existing tool toward internal endpoints, and a malicious upstream can redirect inward. **This is the platform's primary prompt-injection→SSRF vector.** Fix: route through `ssrf_safe_get`.

> **MEDIUM — web connector SSRF guard is a no-op below `VALIDATE_ALL`.** `web_connector_ssrf_enforced` is True only at `VALIDATE_ALL` (`server/security/models.py:74-78`); at `VALIDATE_LLM`/`ALLOW_PRIVATE_NETWORK`/`DISABLED`, `protected_url_check` returns immediately (`connectors/web/connector.py:110-121`) and an admin-configured web connector can crawl `169.254.169.254`/localhost/RFC1918. Intended (internal crawl), but a sharp edge the code's own docstring flags. Default is safe.

> **LOW — DNS-rebinding TOCTOU.** HTTPS path in `ssrf_safe_get` validates the IP then requests the original URL (re-resolves DNS), acknowledged in-code (`url.py:356-361`); EE webhook `_check_ssrf_safety` then fetches via a separate client (`ee/.../hooks/api.py:131-137`, mitigated by `https_only`/`follow_redirects=False`/admin-only). Also INFO: admin-only LLM-provider model-listing endpoints fetch an admin `api_base` with no guard (by design, e.g. Ollama localhost — `server/manage/llm/api.py:1185,1314,1452,1629`).

### 4.2 Injection — clean (full sweep)

- **SQL:** ORM (SQLAlchemy 2.0) everywhere; request-path tenant isolation parameterized via `schema_translate_map` (§2.4). No exploitable SQLi in any request path. Raw DDL that must interpolate schema identifiers (Postgres can't bind them) is guarded by `validate_tenant_id`/`is_valid_schema_name` — **except** `get_current_alembic_version` (`ee/onyx/server/tenants/schema_management.py:98` `SET search_path TO "{tenant_id}"`) which omits the local guard. **MEDIUM (latent, not reachable):** all current callers pass server-generated UUIDs; add the guard for defense-in-depth. **LOW:** tenant id parsed from a bearer token is returned unvalidated at extraction (`auth/utils.py:88-94`) but still hits `is_valid_schema_name` before any SQL string.
- **Command injection:** **no `os.system`/`shell=True`/`os.popen`** in request paths (grep clean). The build sandbox control plane uses exec/argv, with the only `/bin/sh -c` strings interpolating UUID-only ids or `shlex.quote`'d paths.
- **Unsafe deserialization:** **Celery uses JSON, not pickle** — all pickle config is commented out (`background/celery/configs/base.py:85-104`), avoiding the classic Onyx/Danswer pickle-RCE class. Only active `pickle.load` reads self-written tempfiles (`indexing/chunk_batch_store.py:55`). YAML is `yaml.safe_load` only (`skills/bundle.py:102`). No Python `eval`/`exec` on user input (the `eval` hits are Redis Lua wrappers).
- **Code sandbox ("Craft") — strongly hardened.** Per-user K8s Pod (default) or Docker container with `cap_drop=["ALL"]`, `no-new-privileges`, `run_as_non_root`/uid 1000, `seccompProfile=RuntimeDefault`, no host net/pid/ipc, **no Docker socket / host paths mounted**, fail-closed egress proxy, and **Ed25519-signed** control plane to the in-pod sidecar (`sandbox/{docker,kubernetes}/*_sandbox_manager.py`, `sandbox_daemon/`). **MEDIUM:** on EC2, blocking IMDS from the Docker bridge relies on a host `DOCKER-USER` iptables rule from `install.sh --include-craft` ("no application-level fallback", `sandbox/README.md:59`) — mitigated by the in-container firewall in the proxy posture but an operator obligation.

### 4.3 File upload — path traversal structurally prevented, one content-type XSS

Every upload stores bytes in an abstract `FileStore` keyed by a **server-generated UUID**; the user filename is kept only as opaque `display_name` (`file_store/file_store.py:340-345`, `postgres_file_store.py:126-127`). S3 key sanitizer additionally strips `..`/`/`/`\` (`s3_key_utils.py:52,78`). The only disk-extraction path (skill bundles) is textbook zip-slip/symlink/zip-bomb hardened (`skills/bundle.py:143-166, 288-301, 314-327`); connector ZIPs are read in-memory (no `extractall`).

> **MEDIUM — avatar upload: no type/size validation, client content-type replayed (stored-XSS).** `server/features/persona/api.py:285-298` saves `file_type=file.content_type or ...` (client-controlled) with only `BASIC_ACCESS`, and the stored content-type is replayed on download (`chat_backend.py:907,922`) — an `image/svg+xml`/`text/html` upload yields content-type confusion → stored XSS. Mitigated by auth + `Vary: Cookie`. Fix: image allowlist / magic-byte sniff (`puremagic` already a dep). **LOW:** size limits are best-effort/per-endpoint with no global body-size middleware (`projects_file_utils.py:60-69`).

### 4.4 Prompt-injection posture

MCP and `open_url`/web_search tools are SSRF-guarded (§4.1). The exposure is **Custom Tools** (§4.1 MEDIUM) — the unguarded `requests.request` driven by LLM-controlled path/query is the main prompt-injection→SSRF/credential-replay amplifier. Tool *creation* requires curator/admin (limits reachable hosts), but call-time params and upstream redirects are unconstrained.

### 4.3 Supply chain — strong hygiene, recent-disclosure tail

- **Fully pinned + hash-locked.** `requirements/default.txt` (3,461 lines) + `ee.txt` are `uv export` lockfiles with `--hash=sha256:` for every package; the Dockerfile installs with `--require-hashes` (`backend/Dockerfile:45`). 357 distinct packages.
- **Dependabot** covers github-actions, pip(`/backend`), and bun(`/web`) weekly (`.github/dependabot.yml`) — explains the near-latest pins.
- **`pip-audit` run** (v2.10.1): the resolver mode failed because the host is Python 3.11 while the project requires 3.13 (`audioop-lts==0.2.2` is 3.13-only) — documented limitation. Substituted an **OSV `querybatch`** over the 356 pinned versions.

**OSV result: 8 packages flagged — all MODERATE/LOW, all advisories published 2026-05-22…06-16 (the release week), no CRITICAL/HIGH RCE:**

| Package (pinned) | Advisory / severity | Fixed in | Exploitable in Onyx? |
|---|---|---|---|
| `cryptography==46.0.7` | GHSA-537c-gmf6-5ccf — bundled OpenSSL (CVSS 7.5, Avail) | 48.0.1 | Transitive OpenSSL DoS; **MEDIUM** — bump recommended |
| `starlette==0.49.3` | GHSA-86qp-… BadHost path-poisoning (MOD); GHSA-wqp7-… StaticFiles SSRF (Windows) | 1.0.1 / 1.1.0 | BadHost partly mitigated (Onyx uses `WEB_DOMAIN`, §2.1); StaticFiles for user paths unused → **LOW**. NB the fix is a 0.x→1.x major bump (likely gated by FastAPI's starlette cap), so not a drop-in upgrade |
| `pyjwt==2.12.0` | GHSA-993g-… PyJWKClient SSRF; GHSA-fhv5-… JWKS DoS | 2.13.0 | **Not exploitable** — Onyx does **not** use `PyJWKClient` (own `requests.get`, `jwt.py:37`); no detached-JWS use → **LOW/INFO** |
| `aiohttp==3.14.0` | GHSA-2fqr-… cookie-domain (LOW) + others | 3.14.1 | **LOW** |
| `python-multipart==0.0.27` | GHSA-v9pg-… negative Content-Length buffering (LOW) | 0.0.31 | **LOW** (DoS) |
| `pypdf==6.10.2` | several parser DoS (CVSS ~4.x) | 6.12.0 | **LOW** — bump recommended (parses untrusted PDFs) |
| `tornado==6.5.6` | GHSA-pw6j-… CurlAsyncHTTPClient cred leak | 6.5.7 | **Not exploitable** — transitive; `CurlAsyncHTTPClient` not used |
| `nltk==3.9.4` | GHSA-p4gq-… path traversal in `nltk.data.load` (CVSS 7.5) | 3.9.4 | Pinned == fixed (OSV range boundary) → effectively patched / **INFO** |

**Net:** the only items warranting a bump are `cryptography`→48.0.1, `pypdf`→6.12.0, `aiohttp`→3.14.1 — all MODERATE/LOW; the rest are non-exploitable in Onyx's usage. This is a **healthy** supply-chain posture: near-latest pins caught by the natural disclosure cadence, not stale dependencies. (Reproduce: `osv.dev/v1/querybatch` over `name==version` from `default.txt`+`ee.txt`; details fetched per-advisory.)

---

## 5. Container Hardening

| Aspect | Backend (`backend/Dockerfile`) | Model server (`Dockerfile.model_server`) | Web (`web/Dockerfile`) |
|---|---|---|---|
| Base image | `python:3.13-slim` **digest-pinned** (`@sha256:b04b5d…`) | same digest-pinned | `node:24-alpine` + `bun:1-alpine`, digest-pinned |
| Multi-stage (no toolchain in runtime) | ✅ | ✅ | ✅ |
| Hash-verified installs | ✅ `--require-hashes` | ✅ | bun lock |
| Non-root user **created** | ✅ `onyx` uid 1001 (`:80-81`) | ✅ uid 1001 (`:47-48`) | ✅ `nextjs` uid 1001 (`:121`) |
| **Runs as non-root?** | ❌ **NO `USER` directive → runs as ROOT** | ❌ **NO `USER` → ROOT** | ✅ `USER nextjs` (`:122`) |
| CVE-surface reduction | removes `py`, `perl-base`, tornado test key | — | standalone Next output |
| Extra tooling in runtime | `nano`,`vim`,`curl`,`postgresql-client`,`procps` (larger surface) | minimal | minimal |

> **MEDIUM — backend & model_server run as root.** The `onyx` user is created but never activated; worse, **all prod compose files pin `user: root` for the API server** (`docker-compose.prod.yml:393`, `docker-compose.yml:548`, `prod-no-letsencrypt.yml:345`, `multitenant-dev.yml:534`). Only the web container drops privileges. A container-escape or RCE in the Python app therefore starts as root.
> **INFO:** model_server uses `SentenceTransformer(..., trust_remote_code=True)` (`Dockerfile.model_server:35`) — pulls/executes remote model architecture code at build (supply-chain consideration; the model id is pinned to `nomic-ai/nomic-embed-text-v1`).

---

## 6. Prod signals vs POC-smells

**Prod signals (this is not a POC):**
- Real `SECURITY.md` with private vuln reporting, 90-day SLA, scope, safe harbour.
- Auth cannot be disabled; `cloud` type rejected for self-hosted (`users.py:172-180`).
- Race-safe first-admin creation; fail-closed scoped PATs; constant-time CSRF/token compares; PKCE; CSWSH origin checks; host-header-poisoning defenses.
- Secure-by-default centralized SSRF (`utils/url.py`) with per-hop redirect re-validation, IP pinning, and an always-on cloud-metadata block; MCP guard at the httpx transport.
- UUID-keyed file storage (path traversal structurally impossible); zip-slip-hardened skill bundles; JSON (not pickle) Celery serializer; strongly-hardened code sandbox (cap-drop, non-root, no Docker socket, Ed25519-signed control plane).
- Parameterized multi-tenant schema isolation with strict id whitelist.
- Fully hash-pinned dependencies + Dependabot + near-latest versions; `zizmor` GitHub-Actions security linter (SARIF upload, `permissions: {}`, SHA-pinned actions); `ripsecrets` pre-commit hook.
- Digest-pinned, multi-stage images with deliberate CVE-surface trimming.
- `reencrypt_secrets.py` / `rotate_llm_provider_keys.py` operational scripts shipped (key-rotation maturity).

**POC-smells / gaps:**
- **At-rest secret encryption is OFF by default** and **silently no-ops** (FOSS always; EE with empty key) — no startup guard (CRITICAL).
- Backend/model_server containers **run as root** (MEDIUM).
- Weak default password policy (8 chars, no classes) and email verification off by default (LOW).
- Dev/base docker-compose ship `password`/`minioadmin` defaults (HIGH if misdeployed).
- **Custom Tools (OpenAPI actions) bypass the SSRF framework** and replay auth headers across redirects — the main prompt-injection→SSRF vector (MEDIUM); web-connector SSRF guard switchable off (MEDIUM).
- Avatar upload content-type replay → stored-XSS (MEDIUM); OAuth `next` open redirect; non-expiring anonymous JWT (LOW).
- No CodeQL/Semgrep SAST or Trivy/Grype image-scan workflow in CI (only `zizmor` for Actions + Dependabot).
- The security features that define "enterprise-grade trust" (encryption, permission-sync, RBAC groups, SCIM, audit) are **EE-licensed**, not FOSS.

---

## 7. Score & Verdict

### Score: **4 / 5 — Production-ready, with one must-fix default and an EE paywall on trust-critical security**

Onyx v4.1.1 demonstrates **genuinely premium, production-grade security engineering** in the authentication/authorization core: fail-closed scoped tokens, race-safe admin bootstrap, RS256/JWKS with algorithm pinning, PKCE + constant-time CSRF, host-header and CSWSH defenses, parameterized multi-tenant isolation, and a **secure-by-default unified SSRF layer with an always-on cloud-metadata block** that exceeds what most competitors ship. Supply chain is hash-pinned and current; the only CVEs are MODERATE/LOW advisories disclosed the very week of release, mostly non-exploitable in Onyx's usage. This is unambiguously **not a POC**.

It is held back from a 5 by: (1) **the at-rest secret-encryption default**, which is the single most serious finding — credentials sit in plaintext in Postgres on any FOSS build and on any EE build that forgot `ENCRYPTION_KEY_SECRET`, with no startup error (the project guards `USER_AUTH_SECRET` but not this); (2) **containers running as root**; and (3) the **FOSS-vs-EE reality** — the security features that make a RAG platform trustworthy for sensitive corpora (encryption at rest, document permission-sync, group RBAC, SCIM, audit logging) are **EE/paid-only**. For "premium": the *paid* product is premium-secure; the *free* product is not, by design.

**Must-fix to reach 5/5:** hard-fail startup on empty `ENCRYPTION_KEY_SECRET` in EE (mirror the `USER_AUTH_SECRET` guard); add `USER onyx` to backend/model_server images and drop `user: root` in prod compose; bump `cryptography`/`pypdf`/`aiohttp`; add an open-redirect allowlist on OAuth `next`; ship SAST + image-scan in CI.

---

## 8. Unverified / limits

- **`pip-audit` resolver mode could not run** (host Python 3.11 vs required 3.13 — `audioop-lts` is 3.13-only). Substituted **OSV `querybatch`** over the 356 pinned `name==version` pairs + per-advisory range/CVSS lookups. OSV range boundaries (e.g. `nltk` pinned==fixed) introduce minor ambiguity; CVSS strings are from OSV `severity`. EE requirements (`ee.txt`) were included.
- **Exploitability** for pyjwt/tornado/starlette CVEs was assessed by grepping for the vulnerable API usage (`PyJWKClient`, `CurlAsyncHTTPClient`, `StaticFiles`); deeper transitive call-graph analysis not performed. The starlette-fix-requires-major-bump note is informed inference, not byte-verified from the lockfile.
- Findings are **static** (no running instance, no dynamic/DAST); the MEDIUM injection/SSRF items (custom-tool SSRF, avatar XSS, web-connector toggle, sandbox-IMDS) were code-traced, not exploited live. Secrets scan was grep-based, not a full entropy scanner. Frontend (`web/`) auth/render handling, and the desktop/CLI clients, were out of scope for this dimension (the avatar stored-XSS payoff depends on web-side rendering not audited here).
- License-bypass claims are about the *self-hosted* technical posture; the `backend/ee/LICENSE` legal restriction was read but not legally interpreted.
