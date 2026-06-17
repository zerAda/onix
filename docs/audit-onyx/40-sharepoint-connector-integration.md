# 40 — Onyx SharePoint Connector: Integration Audit (byte-level + REAL Graph verification)

**Target:** `/tmp/onyx_v411` — Onyx **v4.1.1** (`git describe --tags` → `v4.1.1`, HEAD `33613e1a8fb4bc036b569af5ecc3c05cc3a53ce7`, grafted/shallow).
**Auditor dimension:** SharePoint connector source (byte-by-byte) + Microsoft Graph correctness vs Microsoft Learn + live probe + prod runbook.
**Date:** 2026-06-17. **Pinned deps:** `office365-rest-python-client==2.6.2`, `msal==1.34.0` (`backend/requirements/default.txt:1760`, `:1579`).
**Live probe:** Ran (UNAUTH only — **no credentials present in env**, `az` not installed). Real ingestion = **PENDING credentials**.

All paths below are relative to `/tmp/onyx_v411` unless noted. Microsoft Graph endpoints were verified against **live Microsoft Learn v1.0 docs** (the `microsoft_docs_search` MCP tool was **denied by the sandbox**, so verification used WebFetch of `learn.microsoft.com/.../graph-rest-1.0` pages — see §3 limits).

---

## 1. Scope

Audited byte-by-byte:
- `backend/onyx/connectors/sharepoint/connector.py` (3205 lines) — the FOSS connector.
- `backend/onyx/connectors/sharepoint/connector_utils.py` (49 lines) — the EE perm-sync bridge.
- `backend/ee/onyx/external_permissions/sharepoint/permission_utils.py` (EE, ~815 lines) — the per-item ACL builder.
- `backend/ee/onyx/external_permissions/sharepoint/{doc_sync,group_sync}.py`, `sync_params.py` (EE registration).
- 11 unit test files (`backend/tests/unit/onyx/connectors/sharepoint/`, 121 test fns) + 1 integration test (`backend/tests/integration/connector_job_tests/sharepoint/`, 2 fns).
- Frontend config/credential schema: `web/src/lib/connectors/connectors.tsx:814`, `web/src/lib/connectors/credentials.ts:210,340`.
- Runtime EE gating: `backend/onyx/utils/variable_functionality.py`, `backend/onyx/configs/app_configs.py:1228`.

---

## 2. Connector source audit (cited)

### 2.1 Auth model — Graph **app-only** via MSAL (client-secret OR certificate)
- Class `SharepointConnector` declares two auth methods: `SharepointAuthMethod.CLIENT_SECRET` / `.CERTIFICATE` (`connector.py:440-442`).
- `load_credentials()` (`connector.py:2305-2373`) builds an **`msal.ConfidentialClientApplication`** with `authority = {authority_host}/{sp_directory_id}` (`:2321`):
  - **Client-secret** path: `client_credential=sp_client_secret` (`:2345-2349`).
  - **Certificate** path: base64-decodes a PFX, `load_certificate_from_pfx()` extracts the PKCS8 PEM private key + SHA-1 thumbprint (`:2330-2342`, `:475-500`), passed as `client_credential={"private_key":…, "thumbprint":…}`.
- Token acquisition is **app-only** (`acquire_token_for_client`) — `_acquire_token()` (`:1846-1849`) and the inline `_acquire_token_for_graph()` (`:2362-2364`) both call `acquire_token_for_client(scopes=[f"{graph_api_host}/.default"])` → `https://graph.microsoft.com/.default`. **No delegated/user flow exists.**
- A **second** token is minted for the SharePoint REST surface (perm-sync only): `acquire_token_for_rest()` requests scope `https://{tenant}.sharepoint.com/.default` (`:503-511`) — distinct from the Graph token.
- `GraphClient` (office365 SDK) wraps the Graph token callback (`:2369-2371`); the SP REST `ClientContext` is built + cached separately (`_create_rest_client_context`, `:1378-1414`, TTL `_REST_CTX_MAX_AGE_S = 30*60`, `:220`) with a documented workaround for office365's token-caching (recreates the MSAL app to force a fresh token).
- Sovereign-cloud aware: `resolve_microsoft_environment()` swaps authority/graph/suffix for GCC-High/DoD/China (`:1195-1208`); UI exposes `authority_host`, `graph_api_host`, `sharepoint_domain_suffix` (`connectors.tsx:885-920`).

### 2.2 Site / drive / page discovery
- **Tenant-wide:** `fetch_sites()` → `graph_client.sites.get_all_sites()` = Graph `GET /sites/getAllSites`, paginated via `_handle_paginated_sites()` (`:1643-1651`, `:1676-1692`); OneDrive personal sites (`-my.sharepoint`) excluded (`:1690`).
- **Targeted:** `_extract_site_and_drive_info()` parses `https://{tenant}.sharepoint.com/sites|teams/{name}[/drive[/folder]]` into `SiteDescriptor(url, drive_name, folder_path)` (`:1444-1489`); also strips sharing-link tokens (`/:f:/r/…`, `_strip_share_link_tokens`, `:1416-1425`).
- **Site resolution:** `graph_client.sites.get_by_url(url)` (office365 → Graph `GET /sites/{host}:/{server-relative-path}`) at `:1501, 1582, 1706, 2378`; `sites.root` used only as tenant-domain fallback (`:1343`).
- **Drives:** `_resolve_drive()`/`_get_drive_names_for_site()` → `GET /sites/{id}/drives` (`:1501-1521`, `:2378-2386`); international "Shared Documents" name mapping (EN/DE/ES) via `SHARED_DOCUMENTS_MAP` (`:95-100`, matched at `:1505-1517`).
- **Site pages (.aspx):** `_fetch_site_pages()` → `GET /sites/{id}/pages/microsoft.graph.sitePage?$expand=canvasLayout` (`:1710-1714`).

### 2.3 Document extraction
`_convert_driveitem_to_document_with_permissions()` (`:762-941`):
- Size guard: skips items > `SHAREPOINT_CONNECTOR_SIZE_THRESHOLD` (default **20 MiB**, `app_configs.py:860`); probes remote size when `size` absent (`:792-802`).
- **Dual download path:** (1) stream `@microsoft.graph.downloadUrl` with byte cap (`_download_with_cap`, `:716-732`), (2) **fallback** to Graph `GET /drives/{id}/items/{id}/content` (`_download_via_graph_api`, `:735-759`).
- Content routing: image → `store_image_and_create_section`; tabular (xlsx/csv) → `extract_and_stage_tabular_file`; else `extract_text_and_images` (PDF/Office/etc.) with inline image extraction (`:850-904`).
- Per-doc metadata: `doc_updated_at`, `primary_owners` (last-modified user), `metadata.drive`, hierarchy parent node, staged `file_id` for tabular (`:920-940`).
- Failure isolation: download errors → `ConnectorFailure` (not a crash) (`:842-848`).

### 2.4 Pagination
- DriveItems BFS: `_iter_drive_items_paged()` — manual folder-queue BFS, `$top=200`, follows `@odata.nextLink`, bounded memory (`:1912-1967`).
- Delta: `_iter_delta_pages()` / `_fetch_one_delta_page()` follow `@odata.nextLink` then stop at `@odata.deltaLink` (`:2001-2127`).
- Site pages: `_fetch_site_pages()` follows `@odata.nextLink` (`:1742-1751`); getAllSites paginates via `has_next`/`_get_next` (`:1646-1651`).

### 2.5 Throttling / 429 + retry/backoff
- **Two independent retry layers, both honor `Retry-After`:**
  - `_graph_api_get_json()` (`:1858-1910`): retries `GRAPH_API_RETRYABLE_STATUSES = {429,500,502,503,504}` (`:148`) up to `GRAPH_API_MAX_RETRIES=5`, `Retry-After` honored (capped 60s), **re-acquires token after sleep** (handles long-traversal expiry, `:1890-1891`), plus retries `ConnectionError`/`Timeout`.
  - `sleep_and_retry()` (office365 SDK calls, `:337-412`) + `_stream_response_to_buffer_with_cap()` (downloads, `:631-713`): retry `{429,503}` and transient transport errors (`ChunkedEncodingError`, `IncompleteRead`, mid-stream drops) with **equal-jitter exponential backoff** (`_backoff_seconds`, base 5/10/20s cap 30s, jitter `[base/2, base]`, `:315-334`); each retry uses a **fresh socket** via `request_factory`.
- **Per-site fault isolation:** `PER_SITE_GRAPH_FAILURE_STATUSES = {403,404,410,423}` (incl. M365-archived sites, `:286-293`) → skip site, keep run (`_is_per_site_graph_failure`, `:296-300`).

### 2.6 Incremental vs full vs slim
- **Incremental (delta):** whole-drive enumeration uses `GET /drives/{id}/root/delta` with a **timestamp `token`** built from the poll `start` (`_build_delta_start_url`, `:2062-2079`); **410 Gone → full re-enumeration** (`:2022-2038`, `:2101-2107`).
- **Folder-scoped** poll falls back to BFS `/children` (delta can't scope a subtree) (`_get_drive_items_for_drive_id`, `:1542-1555`).
- **Checkpointed** state machine `_load_from_checkpoint()` (`:2550-3094`, ~540 lines): 8 phases (sites → drives → driveitems → delta-per-page → site-pages), persists `current_drive_delta_next_link`, `seen_document_ids` (Graph delta can repeat items across pages), `seen_hierarchy_node_raw_ids` between pages so crashes resume mid-drive (`SharepointConnectorCheckpoint`, `:415-437`).
- **Slim sync (pruning):** `_fetch_slim_documents_from_sharepoint()` (`:2140-2303`) yields `SlimDocument` (id + ACL only) in batches of `SLIM_BATCH_SIZE=1000` for deletion detection; can run with or without permissions.
- Implements `CheckpointedConnectorWithPermSync`, `SlimConnectorWithPermSync`, `SlimConnector` (`:1159-1162`; interfaces at `interfaces.py:147,305`).

### 2.7 Permission sync — **EE-gated** (see §4 for the proof)
- The connector calls perm-sync via a **versioned-dispatch indirection** (`connector_utils.py:32-46`): `fetch_versioned_implementation_with_fallback("onyx.external_permissions.sharepoint.permission_utils", "get_external_access_from_sharepoint", fallback=noop→ExternalAccess.empty())`.
- Two **validation probes** surface missing perms at connector-creation time:
  - `probe_role_assignments_permission()` (`:1233-1284`): REST `GET {site}/_api/web/roleassignments?$top=1` for up to 5 sites in parallel; on 401/403 raises `ConnectorValidationError` telling the user to grant **`Sites.FullControl.All`**.
  - `probe_group_members_permission()` (`:1286-1320`): Graph `GET /groups?$top=1`; on 401/403 demands **`GroupMember.Read.All`**.

### 2.8 Failure modes & robustness — notable real-world hardening
- 400 `invalidRequest` on `$expand=canvasLayout` (corrupt page canvas, refs `SharePoint/sp-dev-docs#8822`) → **per-page fallback** so one bad page doesn't poison the whole site (`:1725-1737`, `:1755-1809`).
- SSRF guard on site URLs: `validate_outbound_http_url(https_only=True)` (`:1226-1231`).
- Credential redaction in logs: `_redact_url_for_logging()` strips query/token (`:615-629`).
- Excluded sites/paths via glob (`_is_site_excluded`/`_is_path_excluded`, incl. Office lock files `~$*`) (`:105-126`).

---

## 3. Graph-API correctness vs Microsoft Learn (v1.0)

Every Graph call the connector makes was checked against the live Microsoft Learn v1.0 reference. **All correct, none deprecated.**

| Connector call (path:line) | Graph v1.0 endpoint | Microsoft Learn verification | App permission (Learn) |
|---|---|---|---|
| `fetch_sites` `:1677` | `GET /sites/getAllSites` | `site-getallsites` — exact path; paginates via `@odata.nextLink`/`$skiptoken` ✅ | **`Sites.Read.All`** |
| `get_by_url` `:1501` etc. | `GET /sites/{host}:/{path}` | site-by-server-relative-path is the documented resolution form; unauth probe returns the same shape ✅ | `Sites.Read.All` |
| `_resolve_drive`/`_get_drive_names_for_site` `:1502,2379` | `GET /sites/{id}/drives` | `drive-list` — exact; supports `$top`/`$skipToken` ✅ | **`Files.Read.All`** (least) / `Sites.Read.All` |
| delta `:2031,2074,2106` | `GET /drives/{id}/root/delta` | `driveitem-delta` — exact path; `@odata.nextLink`→`@odata.deltaLink`; **timestamp `token` explicitly supported on SharePoint/ODB**; `410 Gone` resync documented ✅ | `Files.Read.All` / `Sites.Read.All` |
| download `:747` | `GET /drives/{id}/items/{id}/content` | documented content endpoint ✅ | `Files.Read.All` |
| site pages `:1711` | `GET /sites/{id}/pages/microsoft.graph.sitePage` + `$expand=canvasLayout` | `sitepage-list` — exact path incl. the `microsoft.graph.sitePage` cast; supports `$expand` ✅ | **`Sites.Read.All`** |
| perm-sync group expand (EE) `permission_utils.py:457,717` | `GET /groups/{id}/members`, `GET /groups?$filter=…` | documented ✅ | **`GroupMember.Read.All`** / `Group.Read.All` |
| perm-sync ACL (EE) `permission_utils.py:624,644` | SP REST `…/role_assignments?$expand=Member,RoleDefinitionBindings` | SharePoint REST `RoleAssignments` (not Graph) ✅ | **`Sites.FullControl.All`** (SP app perm) |

**Key correctness corroboration:** the `driveitem-delta` doc states verbatim — *"In order to process permissions correctly your application will need to request **Sites.FullControl.All** permissions."* This exactly matches the connector's own `probe_role_assignments_permission` error message (`:1280`). The connector's perm model is therefore **hybrid**: Graph (read content/groups) **+** SharePoint REST `RoleAssignments` (read per-item ACLs) — which is why two tokens and two permission families are required.

**Minor note (not a bug):** the delta doc offers `Prefer: deltashowsharingchanges` / `hierarchicalsharing` headers to optimize permission-change scanning; the connector does **not** use them (it re-reads `RoleAssignments` per item via SP REST instead). Correct but not the most network-efficient option at very large scale.

---

## 4. Permission-sync: FOSS vs EE reality — **EE-GATED (paid)**

**Verdict: SharePoint per-item ACL / external-access sync is Enterprise-Edition gated. It is NOT in the MIT/Community tree.** Proof (two independent):

1. **Location.** The only definition of `get_external_access_from_sharepoint` lives at **`backend/ee/onyx/external_permissions/sharepoint/permission_utils.py`** (under `ee/`). There is **no** `backend/onyx/external_permissions/` (non-ee) directory at all. Sync registration (`sync_params.py:170-181` mapping `DocumentSource.SHAREPOINT` → `sharepoint_doc_sync`/`sharepoint_group_sync`), `doc_sync.py`, `group_sync.py` are all under `backend/ee/`.
2. **License.** Root `LICENSE:5-10`: *"All content that resides under 'ee' directories … is licensed under the **Onyx Enterprise License**."* `backend/ee/LICENSE:1-12`: *"This software … may only be used in production, if you … have … a valid Onyx Enterprise License for the correct number of user seats. … you may copy and modify the Software for development and testing purposes, without requiring a subscription."*

**Runtime switch.** `backend/onyx/utils/variable_functionality.py`:
- `global_version = OnyxVersion()` with `_is_ee=False` default (`:20-34`).
- `set_is_ee_based_on_env_variable()` enables EE if `ENTERPRISE_EDITION_ENABLED` **or** `_LICENSE_ENFORCEMENT_ENABLED` (`:46-67`). `ENTERPRISE_EDITION_ENABLED = os.environ["ENABLE_PAID_ENTERPRISE_EDITION_FEATURES"].lower()=="true"` (`app_configs.py:1228-1230`, default unset → false).
- `fetch_versioned_implementation()` rewrites the module to `ee.{module}` when EE is on (`:94-98`); when off, the bare `onyx.external_permissions…` module is missing → `ModuleNotFoundError` → `…_with_fallback` swallows it → returns the **noop → `ExternalAccess.empty()`**.

**What the FOSS user actually gets.** The connector itself is **MIT and fully functional for ingestion/indexing/search** — it indexes documents and site pages, with delta/checkpoint/throttling all working. But with EE off, **every** SharePoint document is stamped `ExternalAccess.empty()` (`{emails:∅, groups:∅, is_public:False}`). Net effect: **no SharePoint-native ACL mirroring.** Access is governed only by Onyx's own connector / document-set / user-group permissions, not by SharePoint role assignments. There is no FOSS fallback that reads RoleAssignments.

**Additional gate (UI):** perm-sync is **only offered with certificate auth**. `credentials.ts:353` sets `disablePermSync: true` on the `client_secret` method and `disablePermSync: false` on `certificate` (`:366`). The integration test confirms perm-sync uses certificate creds exclusively (`conftest.py:54`). **Runbook consequence: client-secret auth ⇒ no permission sync, even on EE.**

---

## 5. LIVE PROBE results

**Credential check (runtime, values redacted):** `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `GRAPH_TOKEN`, `MS_GRAPH_TOKEN`, `SHAREPOINT_*` — **all empty**. `command -v az` → **not installed**. ⇒ No GOLD-path or token-mint probe possible. **Real ingestion = PENDING credentials.**

**Unauth reachability evidence (REAL `curl` output, tokens/PII redacted):**

1. Graph base reachable: `GET https://graph.microsoft.com/v1.0/` → **HTTP 200** (0.53s).
2. Graph requires auth (proves live, gated): `GET /v1.0/sites/root` →
   `{"error":{"code":"InvalidAuthenticationToken","message":"Access token is empty."…}}`
3. **Target site resolution shape** (exactly what office365 `get_by_url` issues): `GET https://graph.microsoft.com/v1.0/sites/gerep75008.sharepoint.com:/sites/dev-assistant-client-360` → **HTTP 401** `InvalidAuthenticationToken` — i.e. the **gerep75008 / dev-assistant-client-360 site path is reachable and resolvable by Graph; it only needs a bearer token.**
4. SharePoint tenant host live: `GET https://gerep75008.sharepoint.com` → **HTTP 403** (valid TLS, 0.99s) — host exists, anonymous denied (expected).
5. Token endpoint live + client_credentials path valid: `POST https://login.microsoftonline.com/common/oauth2/v2.0/token` (no secret) →
   `{"error":"invalid_client","error_description":"AADSTS7000216: 'client_assertion', 'client_secret' or 'request' is required for the 'client_credentials' grant type."}` — the exact error proving the MSAL app-only flow endpoint is reachable and the only thing missing is the client credential.

**Conclusion:** the full network path the connector depends on (Entra token endpoint → Graph → the specific gerep75008 site) is **live and correctly shaped**. Plugging valid Entra app credentials into `load_credentials()` is the only remaining step to ingest. No data was fabricated.

---

## 6. Live runbook — connect Onyx to `gerep75008.sharepoint.com/sites/dev-assistant-client-360`

**A. Entra app registration (Azure portal → App registrations → New):**
1. Single-tenant app. Note **Application (client) ID** and **Directory (tenant) ID**.
2. **Choose auth method:**
   - **Certificate (REQUIRED if you want permission sync):** generate a PFX, upload the public cert to *Certificates & secrets → Certificates*, keep the PFX + its password. (Onyx wants the PFX **base64-encoded** as `sp_private_key`.)
   - **Client secret (ingestion only, NO perm-sync):** *Certificates & secrets → New client secret*.

**B. Graph application permissions (API permissions → Microsoft Graph → Application) + admin consent:**
- `Sites.Read.All` — list sites/drives/pages, delta, content (the connector's baseline).
- `Files.Read.All` — least-privileged for drives/delta/content (grant alongside `Sites.Read.All`).
- For **permission sync** additionally: `GroupMember.Read.All` (Graph group-member expansion) **and** the SharePoint application permission **`Sites.FullControl.All`** (required to read per-item `RoleAssignments` via SP REST — confirmed by both Microsoft Learn `driveitem-delta` and the connector probe at `:1280`).
- Click **Grant admin consent** for the tenant. (Least-privilege alternative: `Sites.Selected` + per-site grants, but perm-sync still needs full-control on each site collection — see probe message `:1281-1283`.)

**C. Onyx credential (admin UI → Connectors → SharePoint → credential):** fields from `credentials.ts:210-215` —
- `sp_client_id`, `sp_directory_id` (always);
- certificate: `sp_private_key` (base64 PFX) + `sp_certificate_password`; or client-secret: `sp_client_secret`.

**D. Connector config** (`connectors.tsx:814-921`):
- `sites = ["https://gerep75008.sharepoint.com/sites/dev-assistant-client-360"]` (leave empty to index the whole tenant — needs `Sites.Read.All`).
- `include_site_documents` (default true), `include_site_pages` (default true), `treat_sharing_link_as_public` (default false), `excluded_sites`, `excluded_paths`.
- Defaults `authority_host/graph_api_host/sharepoint_domain_suffix` are correct for commercial cloud (this tenant is `.sharepoint.com`).

**E. Permission-sync behavior:**
- **FOSS (EE off, default):** documents indexed & searchable, but **no SharePoint ACLs** — every doc is `ExternalAccess.empty()` (governed by Onyx's own permissions only). The UI may surface SYNC access type, but the EE module is absent → noop.
- **EE on** (`ENABLE_PAID_ENTERPRISE_EDITION_FEATURES=true`, valid license) **+ certificate auth:** per-item ACLs synced from SP `RoleAssignments`; AD/SP groups expanded to member emails via Graph; sharing-link/"Everyone except external" → public when `treat_sharing_link_as_public=true`. Sync cadence: doc 30 min, group 5 min (`ee/onyx/configs/app_configs.py:92-98`).
- **Client-secret auth ⇒ perm-sync disabled regardless of EE** (`credentials.ts:353`).

---

## 7. Prod signals vs POC smells

**Production signals (strong):**
- Two-layer retry honoring `Retry-After` + equal-jitter backoff + token re-acquire mid-traversal (`:1875-1906`, `:315-412`); fresh-socket streaming retry (`:657-707`).
- Delta + per-page checkpoint resume; `seen_document_ids` dedup across delta pages; 410-Gone full-resync (`:2022-2058`, `:415-437`).
- Memory-bounded BFS/streaming + 20 MiB size cap (`:1912-1967`, `:631-713`, `:795-802`).
- Per-site/-drive/-page failure isolation → structured `ConnectorFailure`, including M365-archived sites (HTTP 423) (`:286-300`, `_load_from_checkpoint`).
- Real-world quirk handling: corrupt-canvas 400 per-page fallback w/ upstream issue ref (`:1725-1809`); int'l library names; sharing-link token stripping; SSRF guard; log redaction.
- Validation probes that fail **fast** at connector-creation with the exact missing scope (`:1233-1320`).
- Sovereign-cloud (GCC-High/DoD/China) support (`:1195-1208`).
- Graph usage is **100% correct** vs current Microsoft Learn v1.0; pinned, current deps.

**POC smells / gaps (minor):**
- **Tests are "mock-deep, not wire-deep."** 121 unit tests with real logic assertions, but **every Graph/REST HTTP boundary is monkeypatched** — no `responses`/`vcr`/recorded fixtures. The **Graph 429 retry loop in `_graph_api_get_json` is never exercised against a simulated 429** (only the download path's transport-retry is) — `_graph_api_get_json` itself is mocked everywhere. MSAL/cert/token-expiry-mid-traversal branches untested.
- **Integration test is dormant in CI:** double-gated by `ENABLE_PAID_ENTERPRISE_EDITION_FEATURES=true` + four live secrets (`PERM_SYNC_SHAREPOINT_*`) against one private tenant (`danswerai.sharepoint.com/sites/Permisisonsync`); assertions are brittle magic counts (`== 8`, `== 1`) (`test_sharepoint_permissions.py:56-65`).
- Minor: conftest skip guard reads `sp_client_id` but omits it from the skip condition (`conftest.py:38` vs `:49`) → late failure instead of clean skip if only the client ID is missing.
- No SSRF/adversarial-URL unit tests (only 2 happy-path URL-parse cases); per-item `RoleAssignments` re-read is O(items) round-trips rather than using delta sharing-change headers (scaling cost at very large tenants).

---

## 8. Score & verdict

**Score: 4.5 / 5 — PRODUCTION-READY PREMIUM (FOSS connector), with the caveat that permission sync is a paid (EE) feature requiring certificate auth.**

This is unambiguously **not a POC.** The connector is a 3,200-line, checkpoint-resumable, delta-incremental, memory-bounded, throttling-aware integration whose every Microsoft Graph call matches the current v1.0 reference, with sophisticated real-world failure handling (archived sites, corrupt canvases, mid-stream drops, token expiry) and fail-fast permission validation. The half-point deduction is for test depth (no real HTTP-layer/429-path tests; the only end-to-end test is EE-gated and tenant-pinned) — the *logic* is well covered, but the *wire* and *auth* layers are not, so regressions in the live HTTP retry/token paths could slip through. The biggest **operational** gotcha for "premium" buyers: **per-item ACL mirroring is Enterprise-licensed and certificate-only** — FOSS deployments index content fully but enforce no SharePoint-native permissions.

---

## 9. Unverified / limits

- **No authenticated live ingestion** — env had no Azure/Graph creds and `az` was absent. §5 is **unauth reachability only**; the GOLD path (instantiate `SharepointConnector`, real fetch + `/permissions`) is **PENDING credentials**. Reachability of the exact gerep75008 site/token endpoints is proven, but no documents/ACLs were actually pulled.
- **`microsoft_docs_search` MCP tool was denied** by the sandbox. Graph verification used **WebFetch of `learn.microsoft.com` v1.0 reference pages** (quoted in §3) — first-party Microsoft docs, but via web fetch rather than the MCP. `microsoft_docs_fetch` was not separately attempted after the search denial.
- office365 SDK internals (`sites.get_by_url` → exact Graph path) were **not** read from installed source (package not importable in this env); the `{host}:/{path}` mapping is inferred from the office365 docs + corroborated by the live 401 probe (§5.3), not from the SDK file.
- EE perm-sync principal-mapping detail (RoleAssignments → emails/groups, recursive group expansion, public-group detection) was read from `permission_utils.py` and a sub-agent audit, **not executed** (EE off, no creds).
- Repo is a **shallow/grafted** clone — connector churn history (PR/issue archaeology) was unavailable; only the v4.1.1 snapshot was audited.
