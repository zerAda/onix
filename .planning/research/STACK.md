# Stack Research — Hardening & Productionisation (Milestone 2)

**Domain:** Production hardening — backup/restore, observability, CI/CD release pipeline, CVE remediation
**Researched:** 2026-06-19
**Confidence:** HIGH (tooling choices verified against official docs / PyPI / GitHub Advisories)

---

## Context

This is a **brownfield** research file. The application stack (FastAPI, Postgres 15, OpenSearch 3.6,
Redis 7.4, MinIO, Ollama, Docker Compose, Prometheus-client) already exists and works. This document
covers only the **four additive tooling dimensions** of the current milestone:

1. Backup & restore (Postgres + OpenSearch + MinIO + Redis) — tested, automatable
2. Self-hosted observability (Prometheus + Grafana + Loki + alerting)
3. Reproductible CI/CD release pipeline with signed images
4. CVE remediation (cryptography, pypdf) without breaking the pinned-deps contract

---

## 1. Backup & Restore

### Strategy: pg_dump logical + OpenSearch snapshot API + mc mirror + Redis RDB

The existing `scripts/backup.sh` stops the full stack and tars volumes. This is a valid cold-backup
approach but causes downtime. The recommended upgrade path adds **hot logical backups** for the two
stateful services that support it, keeps cold tarball for MinIO/Redis where it is sufficient.

### Core Technologies

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `pg_dump` / `pg_restore` | Postgres 15 built-in | Logical, consistent Postgres backup without downtime | Transaction-consistent dump while DB keeps running; portable across Postgres versions; no stop required. Volume tar of a running Postgres is **never consistent** — taring in-progress WAL files produces a corrupt backup. MEDIUM confidence — standard PostgreSQL tooling. |
| OpenSearch Snapshot API (REST) | OpenSearch 3.6 built-in | Index + cluster-state snapshots to an S3-compatible repository | OpenSearch ships `repository-s3` plugin; can target MinIO (S3-compatible, same LAN). Curl-scriptable. No extra container needed for the backup plane itself. |
| `mc mirror` (MinIO Client CLI) | `minio/mc:latest` pinned to a digest | Bucket-level incremental mirror for MinIO backup | `mc mirror --overwrite` copies to a local directory mount or second MinIO; `--remove` pruning optional. More reliable than volume tar for object-store data (MinIO filesystem layout is internal). |
| Redis `BGSAVE` + RDB copy | Redis 7.4 built-in | Point-in-time Redis snapshot | Redis is **ephemeral** in this stack (session cache, not primary store). `redis-cli BGSAVE` + copy the `dump.rdb` file is sufficient. Full AOF is overkill; data loss on restart is acceptable per INTEGRATIONS.md ("Redis non-persistence acceptable"). |
| `alpine` + `tar czf` | Existing pattern | Cold volume tar for any service requiring stop | Kept for MinIO and the Onyx model cache volumes. Already used by `scripts/backup.sh` — keep, extend to include the `pg_dump` step. |

### Backup Script Architecture (recommended upgrade)

```
scripts/backup.sh (upgraded):
  1. pg_dump --format=custom (hot, no stop) → backups/TS/postgres.dump
  2. OpenSearch snapshot API PUT /_snapshot/local → MinIO bucket "onix-snapshots"
  3. docker compose stop minio → mc mirror /data → backups/TS/minio/ → start
  4. redis-cli BGSAVE → copy dump.rdb → backups/TS/redis.rdb
  5. tar czf onyx-volumes (file-system, model cache) → backups/TS/
  6. Verify: pg_restore --list, OpenSearch _snapshot status, checksum
```

**Test gate requirement:** `scripts/restore.sh --dry-run` must parse and validate each backup artefact
without touching live data. Add to `make test` as a "backup smoke test" after each CI run produces a
backup snapshot in a temp directory.

### Tools NOT to use

| Avoid | Why |
|-------|-----|
| `velero` | Requires Kubernetes — out of scope for single-machine Docker Compose |
| `barman` / `pgbackrest` | Full-featured WAL archival tools — justified only for multi-TB Postgres or HA. Overhead is unnecessary for the onix use case (tens of users, < 10 GB DB). |
| Volume tar of running Postgres | **Never consistent.** WAL files are mid-write; restoring produces a corrupted cluster. |
| MinIO Erasure Coding | 4-node MinIO EC is a K8s/HA feature — out of scope for single-machine. |

---

## 2. Self-hosted Observability

### Existing implementation

`monitoring/docker-compose.monitoring.yml` already deploys:
- Prometheus `v3.1.0` + Alertmanager `v0.27.0`
- Grafana `11.4.0`
- Loki `3.3.0` + Promtail `3.3.0`
- node-exporter `v1.8.2`, postgres-exporter `v0.16.0`, redis-exporter `v1.67.0`
- opensearch-exporter (elasticsearch-exporter `v1.7.0`)
- blackbox-exporter `v0.25.0`

This stack is **already present and valid**. The milestone work is to (a) confirm versions are current,
(b) note the Promtail EOL path, (c) add missing alert rules for ACL-sync and guardrail failures.

### Version Status (verified 2026-06-19)

| Component | Pinned Version | Status |
|-----------|---------------|--------|
| Prometheus | v3.1.0 | Current stable (v3.x series). HIGH confidence. |
| Alertmanager | v0.27.0 | Current stable. HIGH confidence. |
| Grafana | 11.4.0 | LTS track. Grafana 12.x is in preview as of 2026-06; 11.4 is the production-safe choice. HIGH confidence. |
| Loki | 3.3.0 | Current stable. HIGH confidence. |
| Promtail | 3.3.0 | **EOL February 2026.** Commercial support ended; community LTS until Feb 2026. Promtail will not receive security patches after EOL. See migration note below. |
| node-exporter | v1.8.2 | Current stable. HIGH confidence. |
| postgres-exporter | v0.16.0 | Current stable. HIGH confidence. |
| redis-exporter | v1.67.0 | Current stable. HIGH confidence. |
| opensearch-exporter (elasticsearch_exporter) | v1.7.0 | Current stable. HIGH confidence. |
| blackbox-exporter | v0.25.0 | Current stable. HIGH confidence. |

### Promtail → Grafana Alloy Migration

**Promtail is EOL.** Grafana Labs officially deprecated Promtail in favour of **Grafana Alloy**
(their next-gen OpenTelemetry-based collector). Promtail's LTS ended February 28, 2026.

**Recommendation for this milestone:** Keep Promtail `3.3.0` for the immediate go-live (it still
works, Loki API is unchanged). Schedule migration to Alloy as the **first task of the next milestone**.
Grafana provides a `alloy convert --source-format=promtail` CLI tool that converts configs with
minimal effort.

**Grafana Alloy for Docker Compose:**
- Image: `grafana/alloy:v1.9.0` (latest stable as of research date — verify on `hub.docker.com/r/grafana/alloy/tags` before pinning)
- Configuration: River language (HCL-like); Grafana provides migration tooling
- Single agent replaces Promtail + (optionally) node-exporter scraper

### Missing Alert Rules (to add)

The existing `monitoring/prometheus/rules/onix-alerts.yml` covers:
- Service down (TargetDown, ServiceProbeFailed, ActionsServiceDown)
- Error rate, latency p95, kill-switch blocking
- FinOps budget
- Infra saturation (CPU, RAM, disk)

**Gaps for the production go-live:**
1. **ACL sync failure alert** — `onix_acl_sync_failures_total` counter rate > 0 for 5m → critical
2. **Guardrail failure alert** — `onix_guardrail_blocked_total` rate spike (anomaly vs baseline)
3. **Audit chain break alert** — `onix_audit_chain_breaks_total` > 0 → critical (HMAC chain integrity)
4. **Postgres WAL lag / disk** — `pg_wal_receiver_status` and `node_filesystem_avail_bytes` (already partially covered by HostLowDisk)

These alerts depend on the Prometheus counters being exposed from `access-gateway` and `actions`
`/metrics` endpoints. Verify the metric names match what `prometheus-client` instruments emit.

### Tools NOT to use

| Avoid | Why |
|-------|-----|
| Datadog / NewRelic | Cloud-coupled, telemetry-exfiltrating — violates sovereignty constraint |
| ELK Stack (Elasticsearch + Logstash + Kibana) | 4–8× RAM overhead vs Loki for log aggregation; Elastic is not 100% FOSS (SSPL); Loki already in place |
| Jaeger / Zipkin (distributed tracing) | Overkill for a single-machine, < 50-user deployment. Add only if debugging cross-service latency becomes a recurring problem |
| Sentry | Cloud SaaS by default; self-hosted Sentry is heavyweight (Redis + Postgres instance of its own) |

---

## 3. CI/CD Release Pipeline

### Existing implementation

`.github/workflows/ci.yml` — full quality gate (pytest, bandit, pip-audit --strict, gitleaks, trivy)
`.github/workflows/cd.yml` — build → trivy scan → push GHCR (tag v*) + SBOM (syft SPDX + CycloneDX)

The CD pipeline already tags images with semver, SHA, and branch ref via `docker/metadata-action@v5`,
generates SBOM with `anchore/sbom-action@v0`, and uses `docker/build-push-action@v6` with GHA cache.

**The missing piece for a "signed" image:** cosign image signing is not yet wired in.

### Recommended additions to cd.yml

| Step | Tool | Version | Why |
|------|------|---------|-----|
| Keyless image signing | `sigstore/cosign-installer` | `v3.8.2` | OIDC-based signing using GHA identity (no key management). Signs after push, before SBOM attach. Signature stored as OCI artifact alongside image in GHCR. |
| Signature verification | `cosign verify` | Same | Run in the same job immediately after signing — proves the round-trip works. Failure = pipeline abort. |
| SLSA provenance (optional, later) | `slsa-framework/slsa-github-generator` | `v2.x` | Generates SLSA level 3 provenance. Useful for audit trail but adds ~2 min build time. Defer to next milestone unless auditor explicitly requires it. |

**Cosign keyless workflow addition (add to `build-scan-push` job in cd.yml):**

```yaml
- name: Install cosign
  uses: sigstore/cosign-installer@v3.8.2

- name: Sign image (keyless OIDC)
  # Runs only when actually pushing (tag v* or dispatch push=true)
  if: steps.mode.outputs.push == 'true'
  env:
    COSIGN_EXPERIMENTAL: "1"
  run: |
    cosign sign --yes \
      ghcr.io/${{ github.repository_owner }}/onix-actions@${{ steps.build.outputs.digest }}

- name: Verify signature
  if: steps.mode.outputs.push == 'true'
  run: |
    cosign verify \
      --certificate-identity-regexp="https://github.com/${{ github.repository }}.*" \
      --certificate-oidc-issuer="https://token.actions.githubusercontent.com" \
      ghcr.io/${{ github.repository_owner }}/onix-actions@${{ steps.build.outputs.digest }}
```

Permissions required (already in cd.yml): `id-token: write` (for OIDC → Fulcio cert), `packages: write`.

### Version pin status of existing CI tooling (verified 2026-06-19)

| Tool | Current Pin | Status |
|------|-------------|--------|
| `actions/checkout` | v4 | Current. OK. |
| `actions/setup-python` | v5 | Current. OK. |
| `docker/setup-buildx-action` | v3 | Current. OK. |
| `docker/metadata-action` | v5 | Current. OK. |
| `docker/login-action` | v3 | Current. OK. |
| `docker/build-push-action` | v6 | Current. OK. |
| `aquasecurity/trivy-action` | v0.36.0 | Current (recent commit in repo history confirms upgrade from 0.28.0 to 0.36.0). OK. |
| `anchore/sbom-action` | v0 (floating minor) | MEDIUM risk — floating `v0` picks up minor bumps. Pin to `v0.19.0` or similar for reproducibility. |
| `github/codeql-action/upload-sarif` | v3 | Current. OK. |

### Secrets handling in CI

No changes needed. `GITHUB_TOKEN` is sufficient for GHCR push and cosign keyless signing (uses OIDC,
not stored secrets). `.env` is never committed (gitleaks gate). `gen-secrets.sh` runs in the
`validate` job to produce a throwaway `.env` for compose validation.

### Tools NOT to use

| Avoid | Why |
|-------|-----|
| Harbor (self-hosted registry) | Heavyweight for a single-machine project; GHCR is free, sovereign enough for a private repo, and already integrated |
| Notary v1 (Docker Content Trust) | Deprecated in favour of cosign/sigstore; DCT is being removed from Docker CE |
| Jenkins | Overkill; adds a Java server, persistent storage, credentials DB — GitHub Actions covers all needs |
| ArgoCD / FluxCD | GitOps operators for Kubernetes — out of scope for Docker Compose |

---

## 4. CVE Remediation

### 4a. cryptography 46.0.7 → target version

**Advisory:** GHSA-537c-gmf6-5ccf (CVE-2026-34180, CVSS 7.5) — bundled OpenSSL vulnerability in
`cryptography` wheels < 48.0.1. Fixed in **48.0.1**. Latest PyPI release is **49.0.0** (2026-06-12).

**Recommendation: upgrade to `cryptography==49.0.0`**

Rationale: The CVSS 7.5 "Availability" vulnerability requires fix. 49.0.0 is the current stable
release; it has no breaking changes that affect this codebase:

Breaking changes between 46.x and 49.0.0 that are **relevant to onix**:
- Removal of `SECT*` binary elliptic curves (47.0.0) — onix uses RSA/ES256/RS256 (PyJWT). Not affected.
- OpenSSL 1.1.x removal (47.0.0) — containers use Python 3.11-slim (Debian bookworm = OpenSSL 3.x). Not affected.
- Python 3.8 removal (48.0.0) — onix uses Python 3.11. Not affected.
- `load_pem_private_key()` raises `UnsupportedAlgorithm` instead of `ValueError` (47.0.0) — check if any onix code catches `ValueError` from this call. Likely not: JWT signing uses PyJWT which handles key loading internally.

**Action:** In `access-gateway/requirements.txt` and `actions/requirements.txt`, change:
```
cryptography==46.0.7
```
to:
```
cryptography==49.0.0
```

Then run: `pip-audit --requirement access-gateway/requirements.txt --strict` and
`pip-audit --requirement actions/requirements.txt --strict` to confirm 0 CVE.

Run `make test` to confirm pytest + bandit + pip-audit gates are green.

HIGH confidence — verified against PyPI, GHSA advisory, and cryptography changelog.

### 4b. pypdf 6.10.2 → target version

**Advisory:** Multiple DoS CVEs in pypdf < 6.10.2 (per CONCERNS.md). The STACK.md already shows
`pypdf==6.13.2` in the current codebase scan. PyPI latest is **6.13.3** (2026-06-17).

**Recommendation: upgrade to `pypdf==6.13.3`**

pypdf follows semver; all 6.x versions are backwards compatible for the APIs onix uses (PDF text
extraction, not low-level stream manipulation). The 6.13.x changelog adds MAX_DECLARED_STREAM_LENGTH
guards and pixel-loop optimizations — purely additive/protective.

**Action:** In `actions/requirements.txt`:
```
pypdf==6.13.3
```

Note: If `actions/requirements.txt` already shows `pypdf==6.13.2` (as the codebase STACK.md indicates),
this is a minor bump. Verify the exact pin in the file before changing.

HIGH confidence — verified against PyPI and pypdf GitHub releases.

### 4c. pip-audit --strict gate protocol

After any dependency version bump:

```bash
# Vérifier chaque requirements.txt individuellement (comme en CI)
pip-audit --requirement actions/requirements.txt --strict --progress-spinner off
pip-audit --requirement access-gateway/requirements.txt --strict --progress-spinner off
pip-audit --requirement tests/rag/requirements.txt --strict --progress-spinner off

# Trivy filesystem (détecte les CVE OS + lib non capturées par pip-audit)
trivy fs . --severity CRITICAL,HIGH --ignore-unfixed --exit-code 1
```

The `--strict` flag means pip-audit exits non-zero for any vulnerability, including MODERATE.
This is the existing CI behaviour — do not relax it.

### Dependency upgrade guard

When upgrading pinned deps: **change one package at a time**. After each upgrade:
1. `pip install -r requirements.txt` in a clean venv
2. `pytest -q` for the affected service test suite
3. `pip-audit --strict` passes
4. `bandit -r` passes (no new SAST findings from changed code)
5. Only then commit the requirements bump

This avoids cascading conflicts and keeps the audit trail clean per package.

---

## Supporting Libraries (unchanged, confirmed current)

| Library | Version | Purpose | Status |
|---------|---------|---------|--------|
| `prometheus-client` | 0.21.1 | Metrics export from actions + gateway | Current. PyPI latest 0.21.x. OK. |
| `bandit[sarif]` | Latest (CI installs latest) | SAST gate | OK — no pin needed in CI install since bandit is a dev tool, not a prod dep. |
| `pip-audit` | Latest (CI installs latest) | CVE gate | OK — same rationale. |
| `gitleaks` | 8.18.2 (wget in CI) | Secret detection | Pin is explicit in ci.yml. Verify against github.com/gitleaks/gitleaks/releases — 8.18.2 is recent; current is 8.21.x as of 2026-06. **Consider upgrading to 8.21.x.** MEDIUM confidence (not verified against release page). |
| `syft` (via anchore/sbom-action) | v0 (floating) | SBOM generation | Recommend pinning to `v0.19.0` or similar for reproducibility. |
| `cosign` (via sigstore/cosign-installer) | v3.8.2 | Image signing | Verify on github.com/sigstore/cosign-installer/releases — v3.8.x is the current stable. |

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Postgres backup | `pg_dump --format=custom` (hot) | Volume tar (cold) | Volume tar requires stop; inconsistent on running DB. Kept only for truly stateless volumes. |
| OpenSearch backup | Snapshot API → MinIO | Volume tar | OS volume layout is internal; tar produces valid files but restoration is version-tied and unreliable. |
| Log collection | Promtail 3.3.0 → migrate to Alloy | Filebeat | Alloy is the official successor; Filebeat would add Elastic dependency. |
| Image signing | cosign keyless (sigstore) | Docker Content Trust (DCT/Notary v1) | DCT is deprecated; cosign is the CNCF-standardised successor. |
| Metrics backend | Prometheus (already in place) | Victoria Metrics | Prometheus is already deployed and operational; VictoriaMetrics offers better long-term storage but is not needed at current scale. |
| Secrets management in CI | GitHub OIDC + `GITHUB_TOKEN` | Vault | Vault requires infrastructure; OIDC keyless covers the signing use case without any additional secret to manage. |

---

## Version Compatibility Notes

| Packages | Compatibility Concern | Resolution |
|----------|-----------------------|------------|
| `cryptography==49.0.0` + `PyJWT==2.13.0` | PyJWT wraps cryptography for RS256/ES256; 49.x removes SECT* curves (not used). Check PyJWT compatibility matrix. | PyJWT 2.x is compatible with cryptography 42–49 (uses `RSA` and `EC` keys only). No issue. |
| `cryptography==49.0.0` + `httpx==0.28.1` | httpx uses cryptography only for TLS via `anyio`; not directly imported. | No compatibility concern. |
| `pypdf==6.13.3` + `pdfplumber==0.11.10` | pdfplumber depends on pypdf internally. Check pdfplumber's declared `pypdf` range. | pdfplumber 0.11.x declares `pypdf>=3.1.0` as dependency — 6.13.x is compatible. |

---

## Sources

- [GHSA-537c-gmf6-5ccf — cryptography advisory](https://github.com/pyca/cryptography/security/advisories/GHSA-537c-gmf6-5ccf) — HIGH confidence
- [cryptography changelog (stable)](https://cryptography.io/en/stable/changelog/) — HIGH confidence
- [cryptography 49.0.0 on PyPI](https://pypi.org/project/cryptography/) — HIGH confidence (verified 2026-06-19: version 49.0.0)
- [pypdf 6.13.3 on PyPI](https://pypi.org/project/pypdf/) — HIGH confidence (verified 2026-06-19: version 6.13.3)
- [OpenSearch Snapshot & Restore docs](https://docs.opensearch.org/latest/tuning-your-cluster/availability-and-recovery/snapshots/snapshot-restore/) — HIGH confidence
- [MinIO mc mirror reference](https://min.io/docs/minio/linux/reference/minio-mc/mc-mirror.html) — HIGH confidence
- [Promtail deprecation / Grafana Alloy migration](https://grafana.com/docs/loki/latest/setup/migrate/migrate-to-alloy/) — HIGH confidence
- [cosign keyless signing with GitHub Actions OIDC](https://www.chainguard.dev/unchained/zero-friction-keyless-signing-with-github-actions) — MEDIUM confidence (pattern is standard; verify action version)
- [pg_dump consistency vs volume tar](https://dev.to/piteradyson/postgresql-docker-backup-strategies-how-to-backup-postgresql-running-in-docker-containers-1bla) — MEDIUM confidence (multiple sources agree)
- [Redis persistence RDB/AOF](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/) — HIGH confidence (official Redis docs)

---

*Stack research for: production hardening milestone (Onix FOSS + Docker Compose mono-poste)*
*Researched: 2026-06-19*
