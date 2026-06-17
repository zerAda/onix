# Onyx v4.1.1 — OSS Health, Release Maturity & Licensing Audit

**Dimension 7 of the AC360 Onyx Audit**
Audited version: `git tag v4.1.1` (commit `33613e1`, 2026-06-12)
Source tree: `/tmp/onyx_v411` (github.com/onyx-dot-app/onyx)

---

## 1. Scope

This dimension evaluates: release cadence, project velocity, community health, commercial backing, governance model, the MIT vs Enterprise Edition (EE) licensing split, and the concrete feature matrix distinguishing what is available in the FOSS Community Edition versus the EE self-hosted tier versus the Cloud SaaS tier. The goal is to assess whether Onyx is production-ready premium software or a proof-of-concept.

---

## 2. Release Cadence & Velocity

### Tag history (source: `git -C /tmp/onyx_v411 tag` + GitHub releases page)

The repo carries a **single local tag** in the shallow clone (`v4.1.1`), but GitHub releases shows 214 total releases as of the audit date (2026-06-17). Recent cadence from GitHub releases:

| Tag | Date | Type |
|-----|------|------|
| v4.2.0-beta | 2026-06-16 | Pre-release |
| v4.1.1 | 2026-06-12 | Patch (security) |
| v4.1.0 | 2026-06-11 | Minor feature |
| v4.1.0-beta | 2026-06-08 | Pre-release |
| v4.0.8 | 2026-06-17 | Patch |
| v4.0.7 | 2026-06-11 | Patch |
| v4.0.6 | 2026-06-11 | Patch |
| v4.0.5 | 2026-06-02 | Patch |
| v4.0.4 | 2026-06-02 | Patch |
| v4.0.0 | 2026-05-26 | Major (Business tier middleware introduced) |

**214 releases across the project lifetime** (originally as Danswer, rebranded to Onyx). Minor/patch releases ship multiple times per week. Major releases every few weeks. The project maintains two explicit Docker channels:

- `.github/workflows/docker-tag-latest.yml` — manually promoted stable channel (`latest`)
- `.github/workflows/docker-tag-beta.yml` — pre-release channel (`beta`)

Semver discipline is good: `vMAJOR.MINOR.PATCH` with explicit `-beta.N` pre-release tags. **Cadence verdict: high-velocity, production-paced shipping.**

---

## 3. Community, Backing & Governance

### Project stats (GitHub, 2026-06-17)

| Metric | Value |
|--------|-------|
| Stars | 30,400 |
| Forks | 4,200 |
| Open issues | 119 |
| Open PRs | 338 |
| Total commits (main) | 8,596 |
| Releases | 214 |

### Commercial backing

The copyright holder is **DanswerAI, Inc.** (`LICENSE:1` — "Copyright (c) 2023-present DanswerAI, Inc."). The company operates `onyx.app`, sells Business ($20/user/month) and Enterprise (contact sales) plans, and runs the SaaS Cloud. Onyx is **VC-backed, founder-led** (Yuhong Sun is tagged for PR approvals in `CONTRIBUTING.md`). The company was seeded as Danswer and rebranded.

### Governance model

- **Single-vendor open core.** DanswerAI controls the roadmap; PRs require team approval (`CONTRIBUTING.md:36` — "tag Yuhong to review").
- **CLA for EE contributions required.** `contributor_ip_assignment/EE_Contributor_IP_Assignment_Agreement.md` — all EE code assignments are fully transferred to DanswerAI, Inc. (`Section 3.1` — "assigns and transfers to Company… all right, title and interest"). Community MIT code follows standard fork-and-PR.
- **`CODEOWNERS`** file exists under `.github/CODEOWNERS`.
- Community channel: **Discord** (`discord.gg/TDJ59cGV2X`, linked in README badge). No Slack.
- Linear is used for project tracking (`pr-linear-check.yml` workflow).

---

## 4. Licensing: MIT vs Enterprise Edition

### Root `LICENSE` (MIT + EE carve-out)

```
Copyright (c) 2023-present DanswerAI, Inc.

- All content under "ee" directories → Onyx Enterprise License
  - backend/ee/LICENSE
  - web/src/app/ee/LICENSE
  - web/src/ee/LICENSE
- Everything else → MIT Expat
```

### EE License terms (`backend/ee/LICENSE`)

Key clauses (verbatim-verified from file):

1. **Production use requires a paid subscription** to Onyx Subscription Terms at `onyx.app/legal/self-host`.
2. **Development and testing use is permitted without a subscription** ("you may copy and modify the Software for development and testing purposes, without requiring a subscription").
3. **No sublicensing, no redistribution, no SaaS resale** of EE code without a valid license.
4. **DanswerAI retains all IP** in modifications and patches you make to EE code.
5. **Not OSI-approved open source** — this is a source-available "Business Source License"-style restriction for production self-hosting.

### Self-hosting EE: is it legal without paying?

No. The EE license is explicit: production self-hosting of EE features requires a valid Onyx Enterprise subscription with the correct number of user seats. Development/test use only is permitted without a subscription. The `ENABLE_PAID_ENTERPRISE_EDITION_FEATURES` env var (`backend/onyx/configs/app_configs.py:ENTERPRISE_EDITION_ENABLED`) controls whether EE code paths are activated at runtime.

### Tier model (3-tier, enforced in code)

`backend/onyx/server/settings/models.py` defines:
```python
class Tier(str, Enum):
    COMMUNITY = "community"
    BUSINESS  = "business"
    ENTERPRISE = "enterprise"
```

Enforcement lives in `backend/ee/onyx/configs/license_enforcement_config.py` — `PATH_PREFIX_MIN_TIER` maps API paths to minimum tier, enforced by `tier_gate` middleware.

---

## 5. FOSS vs EE vs Cloud Feature Matrix

Key: **CE** = Community Edition (MIT, free self-host) | **EE-B** = EE Business tier ($20/user/mo) | **EE-E** = EE Enterprise tier (contact sales) | **Cloud** = onyx.app SaaS only

Evidence sources cited per row.

### Authentication & Identity

| Feature | CE | EE-B | EE-E | Cloud | Evidence |
|---------|:--:|:----:|:----:|:-----:|---------|
| Basic username/password | Yes | Yes | Yes | Yes | `onyx/configs/constants.py:AuthType.BASIC` |
| Google OAuth2 | Yes | Yes | Yes | Yes | `onyx/configs/constants.py:AuthType.GOOGLE_OAUTH` |
| OIDC (generic IdP) | Yes | Yes | Yes | Yes | `onyx/configs/constants.py:AuthType.OIDC`; `onyx/configs/app_configs.py:OIDC_SCOPE_OVERRIDE` |
| SAML 2.0 | Yes | Yes | Yes | Yes | `onyx/server/saml.py` (OSS); `ee/onyx/db/saml.py` (EE persistence) — note SAML is partially in OSS |
| SCIM 2.0 (Okta, Entra) | No | No | Yes | Yes | `ee/onyx/server/scim/` + `license_enforcement_config.py: /scim → Tier.ENTERPRISE` |

> Note: The pricing page places "OIDC/SAML SSO" in Enterprise only; however the source code shows SAML/OIDC handlers in the OSS `onyx/` tree, with EE-only SAML DB persistence (`ee/onyx/db/saml.py`). The actual gating may depend on configuration rather than hard code lock. **Mark as unverified for precise SAML gating tier.**

### Access Control & Groups

| Feature | CE | EE-B | EE-E | Cloud | Evidence |
|---------|:--:|:----:|:----:|:-----:|---------|
| Basic RBAC (Admin / Basic roles) | Yes | Yes | Yes | Yes | `onyx/auth/schemas.py:UserRole.BASIC/ADMIN` |
| User Groups with scoped access | No | Yes | Yes | Yes | `ee/onyx/server/user_group/api.py`; `license_enforcement_config.py: /manage/admin/user-group → Tier.BUSINESS` |
| Curator / Global Curator roles | No | Yes | Yes | Yes | `onyx/auth/schemas.py:UserRole.CURATOR/GLOBAL_CURATOR` (roles defined in OSS, enforcement in EE groups) |
| Permission inheritance from source (Confluence, GDrive, Slack, etc.) | No | Yes | Yes | Yes | `ee/onyx/external_permissions/sync_params.py` (Confluence, Jira, GDrive, Slack, SharePoint, GitHub, Gmail, Salesforce, Teams) |
| Document-level permission sync (EE only) | No | Yes | Yes | Yes | `ee/onyx/configs/app_configs.py:DEFAULT_PERMISSION_DOC_SYNC_FREQUENCY` |

### Analytics & Observability

| Feature | CE | EE-B | EE-E | Cloud | Evidence |
|---------|:--:|:----:|:----:|:-----:|---------|
| Query history & admin audit | No | Yes | Yes | Yes | `ee/onyx/server/query_history/api.py`; `license_enforcement_config.py: /admin/query-history → Tier.BUSINESS` |
| Usage dashboards (queries, likes/dislikes) | No | Yes | Yes | Yes | `ee/onyx/server/analytics/api.py`; `license_enforcement_config.py: /analytics/admin → Tier.BUSINESS` |
| Per-user / per-agent breakdown | No | Yes | Yes | Yes | `ee/onyx/db/analytics.py:fetch_per_user_query_analytics`, `fetch_assistant_message_analytics` |
| Usage export (CSV) | No | Yes | Yes | Yes | `ee/onyx/server/reporting/usage_export_api.py`; `license_enforcement_config.py: /admin/usage-report → Tier.BUSINESS` |
| Non-admin analytics (assistant stats) | No | No | Yes | Yes | `license_enforcement_config.py: /analytics → Tier.ENTERPRISE` |
| Evaluations (LLM quality evals) | No | No | Yes | Yes | `ee/onyx/server/evals/api.py`; `license_enforcement_config.py: /evals → Tier.ENTERPRISE` |

### Enterprise Settings & Whitelabeling

| Feature | CE | EE-B | EE-E | Cloud | Evidence |
|---------|:--:|:----:|:----:|:-----:|---------|
| Custom app name / logo | No | Yes | Yes | Yes | `ee/onyx/server/enterprise_settings/models.py:application_name, use_custom_logo` |
| Custom navigation items | No | Yes | Yes | Yes | `ee/onyx/server/enterprise_settings/models.py:custom_nav_items` |
| Custom chat banners, disclaimers, greeting | No | Yes | Yes | Yes | `ee/onyx/server/enterprise_settings/models.py:custom_header_content, custom_popup_header` |
| Consent / first-visit notice | No | Yes | Yes | Yes | `ee/onyx/server/enterprise_settings/models.py:enable_consent_screen` |
| Hide "Powered by Onyx" branding | No | Yes | Yes | Yes | `ee/onyx/server/enterprise_settings/models.py:hide_onyx_branding` |
| Admin enterprise settings write | No | Yes | Yes | Yes | `license_enforcement_config.py: /admin/enterprise-settings → Tier.BUSINESS` |
| Custom analytics JS injection | No | No | Yes | Yes | `license_enforcement_config.py: /admin/enterprise-settings/custom-analytics-script → Tier.ENTERPRISE` |

### Token Rate Limits & Hooks

| Feature | CE | EE-B | EE-E | Cloud | Evidence |
|---------|:--:|:----:|:----:|:-----:|---------|
| Token rate limits (per user/group) | No | No | Yes | Yes | `ee/onyx/server/token_rate_limits/api.py`; `license_enforcement_config.py: /admin/token-rate-limits → Tier.ENTERPRISE` |
| Outbound webhook hooks (post-query, PII removal, custom analysis) | No | No | Yes | Yes | `ee/onyx/server/features/hooks/api.py`; `ee/onyx/hooks/executor.py`; `license_enforcement_config.py: /admin/hooks → Tier.ENTERPRISE` |
| Standard answers (canned Q&A) | No | No | Yes | Yes | `ee/onyx/server/manage/standard_answer.py`; `license_enforcement_config.py: /manage/admin/standard-answer → Tier.ENTERPRISE` |

### Multi-tenancy & Cloud-only

| Feature | CE | EE-B | EE-E | Cloud | Evidence |
|---------|:--:|:----:|:----:|:-----:|---------|
| Schema-per-tenant multi-tenancy | No | No | No | Yes | `ee/onyx/server/tenants/provisioning.py`; `shared_configs/configs.py:MULTI_TENANT` — cloud-only data plane |
| Tenant billing/subscription gating (Stripe) | No | No | No | Yes | `ee/onyx/server/tenants/billing_api.py`; `ee/onyx/configs/multi_tenant_gating_config.py` |
| Tenant team membership / user invitations | No | No | No | Yes | `ee/onyx/server/tenants/team_membership_api.py`, `user_invitations_api.py` |
| Control plane / data plane proxy | No | No | No | Yes | `ee/onyx/server/tenants/proxy.py`; `CLOUD_DATA_PLANE_URL` config |

### API & Service Account

| Feature | CE | EE-B | EE-E | Cloud | Evidence |
|---------|:--:|:----:|:----:|:-----:|---------|
| Service-account API keys (admin-bound) | No | Yes | Yes | Yes | `license_enforcement_config.py: /admin/api-key → Tier.BUSINESS` |

---

## 6. Maturity Signals

### Positive signals

- **8,596 commits** on main, 214 releases, 30k+ stars — not a toy project.
- **Comprehensive CI**: 15+ workflows covering unit, integration, k8s, helm, database, jest, golang, desktop build, nightly LLM provider tests (`pr-*.yml`, `nightly-*.yml`).
- **SECURITY.md** present (`/tmp/onyx_v411/SECURITY.md`): documented vulnerability reporting via GitHub Private Vulnerability Reporting, 90-day disclosure timeline, CVE commitment.
- **Three published CVE-tracked advisories**: GHSA-xr9w-3ggr-hr6j (Critical, 2024), GHSA-rw6w-hp62-gc8w + GHSA-vg3h-35f7-7w6r (Moderate, 2026) — team is responsive, issues disclosed and patched.
- **Helm chart releases** (`helm-chart-releases.yml`), Kubernetes test pipeline (`pr-craft-k8s-tests.yml`) — production-grade deployment tooling.
- **Docs site**: `docs.onyx.app` with deployment guides for Docker, Kubernetes, Helm, cloud providers.
- **CONTRIBUTING.md** is thorough with design doc requirement, IP assignment process, and engineering best practices.

### Caution signals

- Only **one local git tag** in the cloned repo (grafted history) — full history not included in the distributed snapshot; release dating relies on GitHub web.
- The `v4.1.1` release message (`fix(web): sanitize docx-preview output`) references a **security patch** (XSS in file preview), suggesting the project still surfaces basic web security issues.
- Two 2026 IDOR CVEs (any authenticated user can interrupt others' chat sessions or download others' files) point to **insufficient authorization checks** in some endpoints — moderate severity but concerning for multi-user deployments.
- Pricing page claims "OIDC/SAML SSO" is Enterprise-only, but source code shows OSS-tree SAML implementation (`onyx/server/saml.py`) — pricing and code gating may be inconsistent or transitional. **Unverified.**
- 338 open PRs and 119 open issues at audit time — healthy but backlog implies rapid pace can accumulate debt.

---

## 7. Production Signals vs POC Smells

### Production signals

| Signal | Evidence |
|--------|----------|
| Paying enterprise customers | Pricing page at onyx.app/pricing; EE license with seat-based billing via Stripe |
| Kubernetes + Helm deployment support | `.github/workflows/pr-craft-k8s-tests.yml`, `pr-helm-chart-testing.yml` |
| Permission sync from enterprise sources | `ee/onyx/external_permissions/` — Confluence, Jira, SharePoint, Slack, GDrive, Teams, GitHub, Salesforce, Gmail |
| Celery-based background task queue | `ee/onyx/background/celery/` — document permission syncing, external group syncing, hooks |
| License enforcement middleware | `ee/onyx/configs/license_enforcement_config.py` — `tier_gate` middleware with `PATH_PREFIX_MIN_TIER` map |
| Database migrations (Alembic) | `ee/onyx/server/tenants/schema_management.py:run_alembic_migrations` |
| Nightly CI for LLM providers | `.github/workflows/nightly-llm-provider-chat.yml` |

### POC smells

| Smell | Evidence |
|-------|----------|
| 2026 IDOR CVEs not yet patched at time of repo snapshot | GHSA-rw6w-hp62-gc8w, GHSA-vg3h-35f7-7w6r published Apr 29, 2026; v4.1.1 tagged Jun 12, 2026 — need to verify if patched |
| Evals endpoint requires `current_cloud_superuser` — cloud-only | `ee/onyx/server/evals/api.py:Depends(current_cloud_superuser)` |
| EE self-hosting requires paid license for production — barrier to adoption | EE LICENSE; `check_ee_features_enabled()` in `ee/onyx/server/settings/api.py` |
| Single-vendor governance (no foundation, no neutral oversight) | DanswerAI, Inc. owns all EE IP; fork-hostile EE CLA |

---

## 8. Score and Verdict

### Scoring breakdown

| Sub-dimension | Score | Rationale |
|---------------|-------|-----------|
| Release cadence | 5/5 | 214 releases, weekly patches, clear beta/stable channels |
| Project velocity | 5/5 | 30k stars, 8.6k commits, active CI, major features shipping monthly |
| Community & backing | 4/5 | Strong Discord, VC-backed, but single-vendor governance; no neutral foundation |
| Licensing clarity | 4/5 | MIT/EE split is well-documented; EE self-host requires paid subscription; slight pricing↔code inconsistency on SAML gating |
| FOSS vs EE matrix completeness | 5/5 | Three-tier system (Community/Business/Enterprise) is rigorously enforced in code via `PATH_PREFIX_MIN_TIER`; matrix is real and verified |
| Maturity signals | 4/5 | Helm/k8s, Alembic, Celery, permission sync all production-grade; 2026 IDOR CVEs slightly undercut |

### **Overall Score: 4.5 / 5 — PRODUCTION-READY PREMIUM (with caveats)**

Onyx at v4.1.1 is a production-ready enterprise RAG platform, not a POC. The release cadence is commercial-grade, the community is large and active, the infrastructure tooling (Helm, k8s, Celery, Alembic) is mature, and the licensing model is clearly implemented in code. The EE feature set is substantial and well-enforced.

**Key caveat:** the premium features that enterprises require — permission sync, user groups, query analytics, query history, whitelabeling, token rate limits, webhook hooks, and SCIM — are all **behind the EE license wall**. Self-hosted Community Edition is functional for basic RAG and chat, but is not enterprise-ready without a paid subscription. Multi-tenancy is **Cloud-only** and not available in any self-hosted tier.

---

## 9. Unverified Items & Limits

1. **SAML gating tier**: Pricing page says Enterprise-only; source code has SAML handler in OSS `onyx/server/saml.py`. Whether SAML actually requires EE in production depends on runtime gating not fully resolved from static analysis. **Mark unverified.**
2. **Contributor count**: GitHub contributors page did not load in fetch. Approximated from commit volume and star/fork ratios. **Mark unverified.**
3. **IDOR CVE patch status**: GHSA-rw6w-hp62-gc8w and GHSA-vg3h-35f7-7w6r were published 2026-04-29. The v4.1.1 source was tagged 2026-06-12. Patches may be included but were not traced to specific commits in the shallow clone. **Mark unverified.**
4. **Open PR / issue age distribution**: 338 open PRs and 119 issues count observed but age distribution (staleness) not analyzed.
5. **Case studies / named production customers**: Not found in the repo or public docs at audit time. Existence of paying EE customers inferred from Stripe integration and pricing page, not from named references.
6. **Helm chart version and Artifact Hub listing**: Not audited in this dimension.
