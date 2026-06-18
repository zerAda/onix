# Feature Research

**Domain:** Sovereign self-hosted RAG — regulated production go-live (RGPD / insurance / prévoyance-santé, single machine, ~dozens of users)
**Milestone context:** Brownfield — production-readiness & provable-security hardening. Core RAG + RBAC + ACL + guardrails + HMAC-audit already exist. Research covers only NEW production-readiness capabilities.
**Researched:** 2026-06-19
**Confidence:** HIGH (derived from authoritative codebase audit in `.planning/codebase/`, PROJECT.md Active requirements, and domain knowledge of RGPD art. 5/17/30/32 and regulated deployment standards)

---

## Feature Landscape

### Table Stakes — Must have for a secure regulated go-live (cannot ship without)

These are capabilities an auditor, DPO, or internal IT security team would check on Day 1. Missing any of these = the deployment is either unsafe, non-compliant, or operationally fragile.

| Feature | Why Expected | Complexity | Dependency | RGPD / Security Anchor |
|---------|--------------|------------|------------|------------------------|
| **Startup guard: reject default credentials** | `POSTGRES_PASSWORD=password`, `minioadmin`, weak Redis password must cause an immediate fatal error in prod mode — not silently boot | LOW | Needs prod-mode detection flag already in `.env`; `gen-secrets.sh` already generates strong secrets | RGPD art.32 (mesures techniques appropriées) ; CNIL recommends fail-fast on known-weak credentials |
| **Verified backup + tested restore** | `scripts/backup.sh` exists and `make backup` is documented. The gap: restore has never been run in a verified sequence as part of go-live acceptance. A backup that has never been restored is not a backup | LOW–MEDIUM | Requires scripted restore + `make verify` chain to confirm stack comes back healthy | RGPD art.32(1)(c) "capacité à rétablir les données" — mandatory for personal-data processing |
| **Cron-scheduled automated backup with off-machine copy** | Daily backup at 02h30 is documented (PROD_LOCAL.md §4). What is missing: (1) verified cron entry in runbook evidence, (2) documented off-machine copy procedure (NAS/encrypted USB), (3) retention/purge policy for old archives | LOW | Depends on verified restore above; off-machine copy is ops process | RGPD art.32 data availability; single-machine = disk failure = total loss without off-machine copy |
| **`restart: always` + systemd at-boot unit** | Already defined in `docker-compose.prod-local.yml` and `deploy/local-prod/onix.service`. Gap: these must be activated and verified as part of go-live checklist, not optional | LOW | `make up-local-prod`; systemd unit deployment | Operational availability; regulated deployment requires documented continuity |
| **Preflight guard before prod start** | `scripts/preflight-local.sh` exists. Must block startup if: secrets missing, default creds detected, `vm.max_map_count` < 262144, disk < threshold, Docker daemon not running | LOW–MEDIUM | Extends existing `preflight-local.sh`; secret detection is the new piece (gaps with default-creds guard above) | Prevents "fat finger" production incidents; security posture |
| **Complete healthchecks + ordered startup** | `docker-compose.prod-local.yml` overlay provides `depends_on … service_healthy` for all services. Must be verified green in acceptance test (the prod-local overlay is done; gap is validated evidence of the chain running) | LOW | Already implemented; gap is acceptance test record | Availability; auditor needs to see this is real, not asserted |
| **CVE remediation: `cryptography` → 48.x, `pypdf` → 6.12.x** | `pip-audit --strict` gate is a quality gate that must stay green. Two known CVEs remain. Upgrading is table stakes before go-live (CVSS 7.5 DoS is not acceptable for a prod deployment) | LOW | Test suite must stay green after pin upgrades; coordinate with Onyx upstream pins | Supply chain security; `pip-audit --strict` gate |
| **Audit-trail demonstrability: end-to-end proof** | HMAC-chained audit log exists in `actions/app/audit_log.py`. Gap: there is no documented, runnable procedure that an auditor can witness — "run this command, see tamper-evident log, run verify command, see chain valid". This evidence is the thing, not the mechanism | LOW | `actions/app/audit_log.py:verify_chain()` is the verification; needs a demo script + runbook section | RGPD art.5(2) accountability; the auditor must be able to observe the chain, not just be told it exists |
| **RGPD right-to-erasure: verified endpoint** | `onix-actions /erasure` endpoint exists. Gap: there is no verified test case that erases a real user's data and confirms PII is gone from Postgres + chat history. Must be runnable as an ops procedure, not just unit-tested | MEDIUM | Requires live Postgres + chat history setup; `actions/app/` erasure logic already exists | RGPD art.17; CNIL requires documented, exercised erasure procedure |
| **Data retention TTL policy: configured and documented** | `actions/` has configurable TTL purge. Gap: no explicit TTL value is set for production (chat history, audit log, task records). A DPO or auditor will ask "how long do you keep data?" — the answer must be a number, not "configurable" | LOW | `actions/app/` TTL configuration; needs .env variable with real values + docs | RGPD art.5(1)(e) storage limitation; specific retention period must be defined for each data category |
| **Security proof dossier: threat model verified against code** | PROJECT.md Active requirements list "dossier de preuve sécurité : modèle de menace vérifié contre le code". Gap: the threat model exists implicitly in CONCERNS.md and SECURITY_RGPD_ACTIONS.md but has never been assembled as a single auditor-facing document that maps each threat to the mitigating code path, test, and CI gate | MEDIUM | Requires cross-referencing existing docs; no new code needed; output is a document | Auditor-facing evidence; regulated deployment cannot rely on "trust me, it's secure" |
| **SharePoint ACL live integration test (staging tenant)** | CONCERNS.md flags this as HIGH-priority security gap: `graph_acl.py` is only tested against mocks. A real-tenant test is required before go-live to confirm ACL filtering works under real SharePoint permission structures | HIGH | Requires a real (non-prod) SharePoint tenant + `make sync-doc-acl` run against it; existing `graph_acl.py` code unchanged | Security-critical path; RBAC/ACL is the core value — must be proven against reality |
| **Red-team guardrail rotation: test against a larger model** | CONCERNS.md: red-team was run against `qwen2.5:7b` only. If production uses a larger model (Llama 3.1 70B, Mistral Large), the guardrail pass/fail profile may differ. Must run `tests/rag/test_guardrails.py` against the actual production model before go-live | MEDIUM | Requires the production model to be pulled; `tests/rag/` test suite is the mechanism | Security posture; guardrail coverage is model-dependent |
| **Operational runbook: go-live checklist + day-2 ops** | `docs/RUNBOOK.md` exists but covers general ops. Gap: no single "go-live acceptance checklist" that an operator can run through step-by-step (preflight → secrets → startup → healthcheck → admin account creation → backup test → monitoring up → audit trail demo) | LOW | Aggregates existing docs; no new code | Regulated deployment requires documented, repeatable go-live procedure |

---

### Differentiators — Nice-to-have hardening that strengthens the security proof (add if time allows)

These are not blockers for go-live but materially strengthen the auditable security posture or operational resilience.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **SSRF hardening: Custom Tool URL path validation** | CONCERNS.md flags that upstream Onyx Custom Tool calls validate only the base host, not path/query params. Routing through `ssrf_safe_get()` closes the full-URL vector | MEDIUM | Upstream Onyx code change; not blocking (Custom Tools are admin-configured) |
| **Avatar upload MIME type validation** | CONCERNS.md: stored-XSS via SVG/HTML avatar upload. Adding `puremagic` magic-byte validation closes this. `puremagic` is already a dependency | LOW | 5-10 line upstream Onyx change; security hardening, not a blocker (requires auth + specific attack) |
| **Monitoring stack activated by default for prod-local** | `make monitor-up` starts Prometheus/Grafana/Loki. Gap: it is optional in the runbook. For a regulated deployment, observability should be on by default, not opt-in | LOW | Already implemented; just change the runbook default and activate in go-live checklist |
| **ACL sync failure alerting** | Alert rule for sync failure (`graph_acl.py` errors) exists as a listed need in CONCERNS.md §Fragile Areas. An ACL sync failure that goes undetected means stale permissions for up to `GATEWAY_DOC_ACL_REFRESH_SECONDS` | LOW | Needs one alert rule added to `monitoring/prometheus/rules/onix-alerts.yml`; straightforward |
| **Guardrail failure alerting** | Counter `onix_gateway_guardrail_total{blocked=true}` exists. A Prometheus alert on sustained guardrail blocks would surface an ongoing injection/exfil attempt | LOW | One alert rule; the metric already exists |
| **Postgres disk / WAL alerting** | CONCERNS.md flags WAL disk space as a risk under high indexing. A disk-usage alert (< 10% free) already exists in onix-alerts.yml. Adding a WAL-specific alert strengthens this | LOW | One additional Prometheus alert; low-risk addition |
| **Reproducible CI/CD release pipeline** | PROJECT.md Active: "Pipeline CI/CD de release reproductible (gate qualité vert → image taguée publiable)". Having a tagged, auditable release artifact means the deployed version is pinnable and the chain is closed | MEDIUM | `.github/workflows/cd.yml` partially exists; needs a release-tagging job |
| **DPIA documentation completed and signed** | `docs/DPIA_TEMPLATE.md` and `docs/REGISTRE_TRAITEMENTS.md` exist. For insurance/prévoyance-santé data processing, a completed DPIA is legally required (art.35 RGPD — high-risk processing). Having this signed and dated is a differentiator for audit readiness | LOW (document) | No code; requires DPO review + signature; RGPD art.35 |
| **Disk encryption reminder in go-live checklist** | RGPD.md §5 recommends BitLocker/LUKS for the host disk. For a single-machine deployment with Postgres secrets stored in plaintext in Docker volumes, host-disk encryption is a critical compensating control (especially given CONCERNS.md: secrets unencrypted in Postgres by default in FOSS) | LOW (ops procedure) | No code; checklist item + documented in runbook |

---

### Anti-Features — Deliberately NOT building this cycle

Aligned with PROJECT.md Out of Scope and the <1-month constraint. These are things that could be requested or seem natural but would consume the timeline without advancing the core goal of "provable security at go-live."

| Anti-Feature | Why Requested | Why to Avoid | What to Do Instead |
|--------------|---------------|--------------|-------------------|
| **AKS / Kubernetes HA** | Enterprise deployments typically use k8s; `deploy/k8s/onix-ha/` already exists | Out of scope by explicit decision; adds weeks of ops complexity; single-machine is the stated target | Use `docker-compose.prod-local.yml`; k8s is the next milestone |
| **SAML SSO** | Some enterprise environments require SAML; security team may request | OIDC/Entra already works for internal GEREP; SAML = new auth surface + planning risk on a <1-month window | Defer to next milestone; OIDC satisfies the go-live requirement |
| **Admin UI self-service** | Operators prefer a GUI over CLI for user/group management | Not needed to prove security; adds frontend scope; CLI/config suffices for go-live | Document CLI procedures in runbook; defer UI to future milestone |
| **New document connectors** | Stakeholders may request Teams, Confluence, Drive connectors | Documented as out of scope; SharePoint covers the go-live document perimeter; new connectors = new ACL surface to test | Defer; use existing SharePoint connector |
| **Multi-tenancy** | Insurance broker may have multiple client portfolios | Excluded by design for FOSS; isolation = separate instances; GEREP is a single internal client | Not needed for internal mono-client deployment |
| **Cloud LLM / OpenAI fallback** | Users may find local model slower or less capable | Contradicts sovereignty constraint; any cloud call = RGPD data transfer risk; Ollama is non-negotiable | Tune `num_ctx` and model selection via `make tune`; cache amortizes repetitive queries |
| **Real-time webhook ACL sync** | Reduces ACL staleness window from up to 3600s to seconds | Complex Microsoft Graph webhook plumbing; higher failure surface; current TTL-based sync with low interval (< 300s) is adequate for internal usage | Set `GATEWAY_DOC_ACL_REFRESH_SECONDS=300`; document the staleness window; alert on sync failures |
| **Encryption of secrets at rest in Postgres (Onyx EE)** | CONCERNS.md flags this as CRITICAL for FOSS | This is an Onyx Enterprise Edition feature; implementing it FOSS-side would require forking the encryption module — high complexity, high risk | Compensating controls: host disk encryption (BitLocker/LUKS), Postgres restricted to internal Docker network, strong Postgres password from `gen-secrets.sh`, backup files stored on encrypted media |
| **Per-document permission trimming at retrieval (Onyx EE)** | Would eliminate the residual LLM-sees-all-chunks risk within a Document Set | Onyx EE/Cloud feature; explicitly documented as out-of-scope architectural constraint | Design Document Sets around homogeneous access groups; post-filter ACL on response path (already implemented) handles the citation-level control |
| **Full SOC 2 / ISO 27001 audit package** | Regulated context may prompt scope creep toward formal certification | Out of scope for this milestone; the goal is "defensible to an auditor", not formal certification | The security proof dossier (threat model + evidence) is the right output for this cycle |

---

## Feature Dependencies

```
[Startup guard: reject default credentials]
    └──enables──> [Preflight guard before prod start]
                      └──enables──> [Operational runbook: go-live checklist]

[Verified backup + tested restore]
    └──requires──> [Cron-scheduled automated backup with off-machine copy]
    └──feeds──> [Operational runbook: go-live checklist]

[CVE remediation: cryptography + pypdf]
    └──enables──> [pip-audit --strict gate stays green]
    └──must precede──> [Security proof dossier] (CVEs cannot be open in proof artifact)

[SharePoint ACL live integration test]
    └──required by──> [Security proof dossier] (ACL is the core security claim)
    └──required by──> [Audit-trail demonstrability] (audit logs ACL decisions — must be proven real)

[Red-team guardrail rotation on production model]
    └──required by──> [Security proof dossier] (guardrail coverage is model-dependent)

[Audit-trail demonstrability: end-to-end proof]
    └──feeds──> [Security proof dossier]

[RGPD right-to-erasure: verified endpoint]
    └──feeds──> [Security proof dossier]
    └──feeds──> [DPIA documentation] (erasure procedure must exist before DPIA can state it)

[Data retention TTL policy: configured]
    └──feeds──> [Security proof dossier]
    └──feeds──> [DPIA documentation]

[Monitoring stack activated for prod-local]
    └──enables──> [ACL sync failure alerting]
    └──enables──> [Guardrail failure alerting]
    └──feeds──> [Operational runbook: go-live checklist]
```

### Dependency Notes

- **Startup guard requires prod-mode detection:** The guard must know it is running in production mode (not dev). This is already achievable via `ONIX_ENV=prod` or checking the presence of the prod overlay — low implementation cost.
- **Security proof dossier is a downstream aggregator:** It cannot be written until ACL live tests, guardrail rotation, CVE fixes, audit-trail demo, RGPD erasure, and retention policy are all complete. It is the final deliverable of this milestone, not an early task.
- **Backup/restore must be verified before monitoring comes up:** Monitoring stack records metrics to disk; if disk fails, the backup must cover monitoring state too (or monitoring is excluded from backup scope — which must be documented explicitly).
- **SharePoint ACL live test is the highest-risk item:** It is the only item that requires an external system (Azure/SharePoint tenant). If access to a staging tenant is blocked, this is a planning blocker — must be escalated immediately.

---

## MVP Definition (Go-Live Gate)

### Must ship (go-live is blocked without these)

- [ ] **Startup guard: reject default credentials** — a deployment with `password` as Postgres password is a critical security incident waiting to happen
- [ ] **CVE remediation: `cryptography` → 48.x, `pypdf` → 6.12.x** — `pip-audit --strict` gate must be green; shipping with known CVSS 7.5 is indefensible
- [ ] **SharePoint ACL live integration test** — RBAC/ACL is the core security claim; it must be proven against a real tenant, not mocks
- [ ] **Verified backup + tested restore** — single-machine deployment with personal data; RGPD art.32(1)(c) requires documented restore capability
- [ ] **Audit-trail demonstrability: runnable demo** — the auditor must be able to witness the HMAC chain, not just read that it exists
- [ ] **RGPD right-to-erasure: verified endpoint** — art.17 compliance requires a tested, documented erasure procedure
- [ ] **Data retention TTL: configured with real values** — "configurable" is not a compliance answer; specific periods must be set and documented
- [ ] **Operational runbook: go-live checklist** — regulated deployment requires a documented, repeatable procedure
- [ ] **Security proof dossier** — the milestone's primary deliverable; the auditor-facing evidence package

### Add before broad rollout (first week after go-live)

- [ ] **Red-team guardrail rotation on production model** — run `tests/rag/` against the actual prod model; medium complexity, medium risk
- [ ] **ACL sync failure alerting** — low complexity; important for catching silent permission staleness
- [ ] **Guardrail failure alerting** — low complexity; catches ongoing injection attempts post-launch
- [ ] **DPIA documentation completed and signed** — DPO review needed; not a blocker for internal pilot but required before broad rollout with sensitive data

### Future consideration (next milestone)

- [ ] **SAML SSO** — deferred by explicit decision; revisit when external partners need access
- [ ] **Admin UI self-service** — deferred; CLI suffices for current user count
- [ ] **Real-time webhook ACL sync** — deferred; TTL < 300s is adequate for internal usage
- [ ] **AKS / HA deployment** — next milestone; current single-machine is validated path to scale

---

## Feature Prioritization Matrix

| Feature | Security/Compliance Value | Implementation Cost | Priority |
|---------|--------------------------|---------------------|----------|
| Startup guard: reject default credentials | HIGH | LOW | P1 |
| CVE remediation (cryptography, pypdf) | HIGH | LOW | P1 |
| SharePoint ACL live integration test | HIGH | HIGH | P1 |
| Verified backup + tested restore | HIGH | LOW–MEDIUM | P1 |
| Audit-trail demonstrability | HIGH | LOW | P1 |
| RGPD erasure: verified endpoint | HIGH | MEDIUM | P1 |
| Data retention TTL: configured | MEDIUM | LOW | P1 |
| Operational runbook: go-live checklist | MEDIUM | LOW | P1 |
| Security proof dossier | HIGH | MEDIUM | P1 |
| Red-team guardrail rotation (prod model) | HIGH | MEDIUM | P1–P2 |
| Monitoring stack activated by default | MEDIUM | LOW | P2 |
| ACL sync failure alerting | MEDIUM | LOW | P2 |
| Guardrail failure alerting | MEDIUM | LOW | P2 |
| DPIA documentation signed | MEDIUM | LOW (doc) | P2 |
| Disk encryption reminder in checklist | MEDIUM | LOW (ops) | P2 |
| SSRF: Custom Tool full-URL validation | LOW–MEDIUM | MEDIUM | P2 |
| Avatar upload MIME validation | LOW | LOW | P2 |
| Reproducible CI/CD release pipeline | LOW | MEDIUM | P2 |
| Postgres WAL alerting | LOW | LOW | P3 |
| Real-time webhook ACL sync | LOW | HIGH | P3 (defer) |
| AKS / Kubernetes HA | — | HIGH | ANTI (this cycle) |
| SAML SSO | — | HIGH | ANTI (this cycle) |
| Admin UI self-service | — | HIGH | ANTI (this cycle) |

**Priority key:**
- P1: Must have for go-live (blocked without it)
- P2: Should have; add in first week post-launch
- P3: Nice to have; future milestone
- ANTI: Explicitly out of scope this cycle

---

## Sources

- `.planning/PROJECT.md` — Active requirements, Out of Scope, Constraints (authoritative)
- `.planning/codebase/CONCERNS.md` — HIGH-priority gaps: default secrets, CVEs, ACL live test gap, red-team model bias (authoritative)
- `.planning/codebase/ARCHITECTURE.md` — existing security architecture (authoritative)
- `AGENTS.md` — rules of the game, security defaults (authoritative)
- `docs/PROD_LOCAL.md` — production single-machine runbook, backup/restore, healthchecks (authoritative)
- `docs/RGPD.md` — RGPD compliance posture, data mapping (authoritative)
- `docs/SECURITY.md` — security baseline, secret generation, surface reduction (authoritative)
- `docs/SECURITY_RGPD_ACTIONS.md` — threat model, PII redaction, HMAC audit, erasure (authoritative)
- `docs/OBSERVABILITY.md` — monitoring stack, alert rules, metrics (authoritative)
- `docs/PARITE_ENTREPRISE.md` — enterprise parity matrix, open asterisks (authoritative)
- RGPD art.5(2) accountability, art.17 erasure, art.30 records, art.32 security, art.35 DPIA — regulatory baseline
- CNIL recommendations on self-hosted AI systems (regulated deployment checklist)

---
*Feature research for: sovereign self-hosted RAG — production-readiness & provable-security milestone*
*Researched: 2026-06-19*
