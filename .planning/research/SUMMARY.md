# Project Research Summary

**Project:** onix — sovereign self-hosted RAG, production-hardening milestone
**Domain:** Brownfield productionisation — regulated single-machine Docker Compose go-live (RGPD / assurance / prévoyance-santé)
**Researched:** 2026-06-18
**Confidence:** HIGH

---

## Executive Summary

onix is a mature, functionally complete sovereign RAG stack (Onyx 4.1.1 FOSS + Ollama + onix governance layer) that already implements RBAC, per-document ACL, HMAC-chained audit, deterministic guardrails, PII redaction, and DLP egress. This milestone is not a build-from-scratch effort: it is a hardening and evidence-production cycle whose single deliverable is a go-live that is **defensible to an auditor**. The four research threads converge on the same four workstreams — WS1 (backup/restore), WS2 (credential guard + healthchecks), WS3 (observability /metrics gap), WS4 (systemd boot framing) — and agree on their build order: WS2 and WS3 in parallel first, then WS1, then WS4, then the security proof.

The primary risk to the timeline is not implementation complexity — most gaps are LOW-complexity bash or Python additions to existing scripts and services — but two external-system dependencies: (1) the SharePoint ACL live integration test, which requires access to a non-production Azure/SharePoint tenant and is the only item that cannot be executed in isolation; and (2) the red-team re-run on the actual production model, which requires the production Ollama model to be pulled and available. Both are HIGH-priority security-proof blockers. If tenant access is blocked, it becomes a planning escalation, not a deferred task.

The FOSS-vs-EE boundary is a recurring theme across all four research files and must be stated honestly in the security proof dossier: Onyx FOSS does not encrypt secrets at rest, does not synchronise permissions at indexation time, and does not provide a native audit trail. The onix layer compensates for all three in FOSS — but these compensations (post-retrieval ACL filter, HMAC audit, backup encryption) are the claims that must be *provable*, not assumed. The security proof dossier is therefore the terminal deliverable of the milestone; it can only be assembled after every other workstream is complete.

---

## Key Findings

### Recommended Stack

The application stack is frozen and correct. Research confirms all existing component versions are current. The only version concerns are:

- **Promtail 3.3.0 is EOL** (February 2026). Keep for go-live; schedule migration to Grafana Alloy v1.9.0 as the first task of the next milestone.
- **cryptography 46.0.7 carries GHSA-537c-gmf6-5ccf (CVSS 7.5)**. Upgrade to `cryptography==49.0.0` (PyPI latest). Compatible with PyJWT 2.x (RS256/ES256 only).
- **pypdf must reach 6.13.3** (PyPI current). Already at 6.13.2 per codebase scan — minor bump only.
- **`anchore/sbom-action` is floating at `v0`** — pin to a fixed release for reproducibility.
- **gitleaks is at 8.18.2** — current release is 8.21.x; upgrade recommended (CI hygiene).

**Core additive tooling for this milestone:**
- `cosign` (sigstore/cosign-installer): keyless OIDC image signing, wires into existing `cd.yml`, no new secrets required
- `pg_dump --format=custom`: hot logical backup for Postgres — replaces cold volume tar for the database tier only
- `openssl enc -aes-256-cbc -pbkdf2`: backup archive encryption — key from `.env` (`BACKUP_ENCRYPTION_KEY` via `gen-secrets.sh`), never stored alongside archive
- `prometheus-client` `/metrics` endpoint on `actions/app/main.py`: the single missing piece for complete observability

### Expected Features

**Must have for go-live (P1):**
- Startup guard rejecting default credentials in `scripts/preflight-local.sh`
- CVE remediation: `cryptography==49.0.0`, `pypdf==6.13.3` — `pip-audit --strict` gate returns 0 CVE
- SharePoint ACL live integration test against non-production tenant **(external dependency, scheduling risk)**
- Verified backup + tested restore (`make restore-drill`) — RGPD art.32(1)(c)
- Cron/systemd-scheduled backup with documented off-machine copy procedure
- Audit-trail demonstrability: `make audit-verify` runnable by auditor (`verify_chain()` → `ok: true`)
- RGPD right-to-erasure: erasure script orchestrating both onix-actions and the Postgres/Onyx workaround
- Data retention TTL: explicit numeric values in `.env` — "configurable" is not a compliance answer
- Operational runbook: go-live acceptance checklist
- Security proof dossier: **terminal deliverable, assembled last**

**Should have — first week post-launch (P2):**
- Red-team guardrail re-run on the production model (`make rag-test-live`)
- ACL sync failure alerting (`onix_acl_sync_failures_total > 0` for 5m → critical)
- Guardrail failure alerting
- `GATEWAY_DOC_ACL_REFRESH_SECONDS=300` in prod `.env`
- DPIA documentation completed and signed

**Defer to next milestone:**
- Promtail → Grafana Alloy migration, SAML SSO, Admin UI, new connectors, AKS/Kubernetes, real-time webhook ACL sync, SLSA provenance

### Architecture Approach

Single-machine Docker Compose, two compose projects on a shared `onix-net`. The four workstreams map to four integration surfaces with minimal overlap:

1. **WS1 — backup/restore** (`scripts/backup.sh`, `scripts/restore.sh`, `scripts/gen-secrets.sh`): ordered stop sequence, backup encryption, hot pg_dump path, post-restore verify
2. **WS2 — credential guard** (`scripts/preflight-local.sh`, `Makefile`): banned-value checks before any container starts; healthcheck ordering already complete in `docker-compose.prod-local.yml`
3. **WS3 — observability** (`actions/app/main.py`): single missing `/metrics` endpoint; full monitoring stack is already deployed
4. **WS4 — systemd framing** (`deploy/local-prod/`): monitoring + backup units/timers — new files only, no modifications to existing compose or units

**Invariants that must not be broken:** `DISABLE_TELEMETRY=true` on api_server/background; metric labels use route templates only (never user/document values); Ollama via internal service name only; backup encryption key never stored alongside archive; `COMPOSE_PROJECT_NAME=onix` stable.

### Critical Pitfalls

1. **Untested backup/restore** — cold volume tar may produce a corrupt Postgres cluster; volume naming prefix may mismatch on restore if `COMPOSE_PROJECT_NAME` is not fixed. Prevention: `make restore-drill` as a go-live gate; fix `COMPOSE_PROJECT_NAME=onix`.
2. **Default credentials pass `${VAR:?error}` validation** — operator copies `.env.example` without running `make secrets`; secrets are defined but equal `password`/`minioadmin`. Prevention: banned-value list in `preflight-local.sh`.
3. **ACL staleness + silent sync failure** — default 3600s interval; Graph API failure extends the window indefinitely with no alert. Prevention: set 300s in prod; `onix_acl_sync_failures_total` Prometheus alert mandatory.
4. **Audit HMAC key absent → silent SHA-256 fallback** — chain is consistent but not tamper-proof (public algorithm, no key). Prevention: mandatory in preflight (non-empty, ≥ 32 chars); `make audit-verify` in `make verify`.
5. **PII in Prometheus labels / cardinality explosion** — `user=principal.user_id` or `path=request.url.path` → OOM on single-machine Prometheus + RGPD violation. Prevention: route-template-only labels enforced in code review and unit test.

---

## Implications for Roadmap

### Phase 1: CVE Remediation + Credential Guard (WS2 partial)
**Rationale:** Cheapest fixes, hardest blockers. CVE gate must be green before any subsequent CI run is meaningful. Credential guard is pure bash, zero risk of breaking existing services.
**Delivers:** `cryptography==49.0.0`, `pypdf==6.13.3` pinned; `pip-audit --strict` green; `preflight-local.sh` rejects banned values; `make up-local-prod` depends on `preflight-local`.
**Avoids:** CVE drift, default credentials.

### Phase 2: Observability Completion + Alerting (WS3)
**Rationale:** Monitoring stack already deployed. Completing the single missing `/metrics` endpoint means later phases have full observability including ACL-sync and guardrail alerts needed for the security proof.
**Delivers:** `actions/app/main.py` `/metrics`; ACL sync failure alert; guardrail block spike alert; audit chain break alert; `GATEWAY_DOC_ACL_REFRESH_SECONDS=300`; monitoring default-on in the go-live checklist.
**Avoids:** PII in metrics, silent ACL sync failure.

### Phase 3: Backup / Restore Hardening (WS1)
**Rationale:** Requires `BACKUP_ENCRYPTION_KEY` from Phase 1 (`gen-secrets.sh`). Restore drill must be documented before the runbook and security proof can reference it as evidence.
**Delivers:** ordered stop sequence in `backup.sh`; AES-256 archive encryption; hot `pg_dump` logical path; `make restore-drill`; off-machine copy procedure; retention/purge policy.
**Avoids:** untested backup; key stored alongside archive; unordered cold stop.

### Phase 4: systemd Boot Framing + Runbook (WS4)
**Rationale:** References artifacts from WS1 (backup script) and WS2 (preflight script) — must come after both. Low-risk (new files only).
**Delivers:** monitoring unit (After=onix.service); backup timer + service (scheduled); go-live acceptance checklist in `RUNBOOK.md`.
**Avoids:** wrong systemd Type for `docker compose up -d`.

### Phase 5: Security Proof — ACL Live Test + Audit Trail + RGPD Evidence
**Rationale:** Terminal phase and primary milestone deliverable. Cannot begin until prior phases complete. Contains the one external-system dependency (SharePoint live test). **Escalate tenant access need on Day 1.**
**Delivers:** SharePoint ACL live integration test (real tenant, permission revocation verified end-to-end); `make rag-test-live` on production model (target 21/21 pass); `make audit-verify` runnable by auditor; erasure script; explicit retention TTL values; cosign keyless signing wired into `cd.yml`; security proof dossier (threat model → code path → test → CI gate mapping; FOSS-vs-EE distinctions explicit; residual risks accepted and documented).
**Avoids:** non-demonstrable audit trail; incomplete RGPD erasure; guardrail bias to small models.

### Phase Ordering Rationale

- CVE first: a broken pip-audit gate makes every subsequent test result suspect.
- WS3 second rather than last: ACL-sync and guardrail alert rules generate real operational evidence for the security proof during later phases.
- WS1 third: requires `BACKUP_ENCRYPTION_KEY` from Phase 1; the restore drill is upstream evidence for Phases 4 and 5.
- WS4 fourth: references both WS1 and WS2 artifacts; pure file creation, zero risk.
- Security proof last: downstream aggregator — can only be assembled after all workstreams complete and evidence is collected.

### Research Flags

**Needs real-world coordination:**
- Phase 5 — SharePoint ACL live integration test: non-production Azure tenant access required. Single external-system dependency and the highest scheduling risk of the milestone. Escalate on Day 1. If blocked > 2–3 days, it becomes a go-live blocker.

**Standard patterns (no deeper research needed):**
- Phase 1: PyPI and GHSA are authoritative; compatibility verified.
- Phase 2: `access-gateway/app/metrics.py` is the reference; prometheus-client API is stable.
- Phase 3: pg_dump and volume backup are well-documented POSIX patterns.
- Phase 4: existing `onix.service` is the reference; `Type=oneshot` + `RemainAfterExit=yes` is established.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Versions verified against PyPI, Docker Hub, GitHub. Minor: gitleaks 8.18.2 vs current 8.21.x (not blocking). |
| Features | HIGH | Derived from codebase audit + RGPD regulatory baseline. No ambiguous requirements. |
| Architecture | HIGH | Source-verified against actual compose files and scripts. Integration surfaces precisely identified. |
| Pitfalls | HIGH | Byte-level codebase audit (CONCERNS.md). All pitfalls confirmed in actual code. |

**Overall confidence: HIGH**

### Gaps to Address

- **Promtail EOL:** 3.3.0 is safe for the go-live window; schedule Alloy migration as the first task of the next milestone.
- **gitleaks version:** upgrade from 8.18.2 to 8.21.x in Phase 1 alongside other CI hygiene.
- **Redis persistence:** `appendonly no` is documented acceptable (session cache, not primary store); revisit if task durability becomes a concern.
- **Audit HMAC key mandatory enforcement:** the silent SHA-256 fallback is the most dangerous "looks done but isn't" item — Phase 1 preflight guard must validate this key (present, ≥ 32 chars).
- **Entra ID groups claim:** if `groupMembershipClaims` is not configured in the Azure app registration, all users arrive without groups → systematic 403. Verify as a go-live runbook checklist item, not a code change.

---

## Sources

### Primary (HIGH — source-verified against actual codebase)
- `.planning/codebase/CONCERNS.md` — gaps audit
- `.planning/codebase/ARCHITECTURE.md` — existing invariants, anti-patterns
- `docker-compose.prod-local.yml`, `monitoring/docker-compose.monitoring.yml` — current implementation state
- `scripts/backup.sh`, `scripts/restore.sh`, `deploy/local-prod/onix.service` — existing scripts
- `actions/app/audit_log.py` — SHA-256 fallback behaviour confirmed in source
- `.planning/PROJECT.md` — authoritative scope, constraints, Out of Scope

### Primary (HIGH — external authoritative sources)
- GHSA-537c-gmf6-5ccf (cryptography advisory) — CVSS 7.5
- cryptography 49.0.0 on PyPI; pypdf 6.13.3 on PyPI
- Grafana Alloy migration guide — Promtail EOL February 2026
- OpenSearch Snapshot & Restore docs, Redis persistence docs
- RGPD Art.5(2), Art.17, Art.32, Art.33, Art.35

### Secondary (MEDIUM)
- cosign keyless signing with GitHub Actions OIDC — pattern verified; action version to confirm on release page before wiring
- pg_dump consistency vs volume tar — multiple sources agree on inconsistency of hot volume tar for Postgres

---

*Research completed: 2026-06-18*
*Ready for roadmap: yes*
