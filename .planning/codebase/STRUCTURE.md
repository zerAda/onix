# Codebase Structure

**Analysis Date:** 2026-06-18

## Directory Layout

```
onix/
├── README.md                    # Project overview + quickstart
├── ARCHITECTURE.md              # System-wide architecture (entry point for arch)
├── AGENTS.md                    # Agent onboarding guide (read FIRST — rules, scopes)
├── CLAUDE.md                    # Claude Code quick reference
├── SECURITY.md                  # Security posture, threat model, requirements
├── Makefile                     # One-command control (tune/secrets/up/verify/test)
├── docker-compose.yml           # Main stack (prod-local hardened)
├── docker-compose.gpu.yml       # Overlay: NVIDIA GPU support
├── docker-compose.performance.yml  # Overlay: high-throughput tuning
│
├── .github/
│   └── workflows/               # CI/CD (GitHub Actions)
│       ├── ci.yml              # Lint, pytest, bandit, gitleaks, trivy, compose validation
│       ├── cd.yml              # Deploy to Azure/AKS
│       └── ragas-nightly.yml    # RAG quality evaluation (nightly)
│
├── access-gateway/              # ONIX LAYER: RBAC proxy + cache + guardrails
│   ├── README.md               # Gateway-specific setup
│   ├── Dockerfile              # Multi-stage build (alpine)
│   ├── app/                    # Main FastAPI app
│   │   ├── __init__.py         # Version, imports
│   │   ├── main.py             # FastAPI app, lifespan, endpoints (/v1/chat, /health, /metrics)
│   │   ├── config.py           # 12-factor settings (env vars → Settings dataclass)
│   │   ├── identity.py         # OIDC claims parsing, principal resolution, group fetching (Graph or claims)
│   │   ├── cache.py            # HMAC-keyed cache (exact + semantic), LRU/Redis backends
│   │   ├── guardrail.py        # Post-filter heuristics (citation, hallucination, injection, exfil)
│   │   ├── doc_acl.py          # Document-level ACL (static JSON + Graph, filtering)
│   │   ├── graph_acl.py        # Microsoft Graph item permission resolution (SharePoint)
│   │   ├── graph_client.py     # Graph API client (transitive group membership, item permissions)
│   │   ├── onyx_proxy.py       # Proxy logic (enforce document sets, extract answer, reconstruct context)
│   │   ├── streaming.py        # SSE streaming proxy (Onyx → client)
│   │   ├── audit.py            # Access decision + guardrail decision logging (HMAC pseudonymize)
│   │   ├── metrics.py          # Prometheus metrics (requests, cache hits, guardrails, latency)
│   │   └── mapping.py          # Group→DocumentSet mapping (loaded from JSON, live reloads)
│   │
│   ├── config/                 # Configuration templates
│   │   ├── group_map.example.json   # Example: "group-uuid" → ["doc-set-uuid-1", ...]
│   │   ├── doc_acl.example.json     # Example static ACL: {"doc-id": {"user-id": ["read"]}}
│   │   └── doc_acl.example.json.md  # Doc of ACL format
│   │
│   ├── requirements.txt         # Python dependencies (fastapi, httpx, prometheus-client, etc.)
│   ├── requirements-dev.txt     # Dev/test deps (pytest, pytest-asyncio, pytest-mock)
│   ├── .env.template           # Template for local .env (copy and fill)
│   │
│   └── tests/                  # Unit + integration tests (pytest, offline except e2e)
│       ├── conftest.py         # Shared fixtures
│       ├── test_api.py         # Endpoint routing, status codes
│       ├── test_identity.py    # OIDC claims, group resolution
│       ├── test_cache.py       # Cache key isolation, RBAC, LRU eviction
│       ├── test_cache_semantic.py  # Semantic cache + factual divergence detection
│       ├── test_doc_acl.py     # Document filtering, citation redaction
│       ├── test_graph_acl.py   # Graph item permission + filtering
│       ├── test_graph_client.py    # Graph API calls (mocked)
│       ├── test_guardrail.py   # Post-filter heuristics (citations, injection, exfil)
│       ├── test_guardrail_deployed.py  # Guardrail on live Onyx (real API)
│       ├── test_mapping.py     # Group→DocSet mapping loading
│       ├── test_metrics.py     # Prometheus metrics collection
│       ├── test_onyx_proxy.py  # Proxy logic (enforce sets, extract answer)
│       ├── test_streaming.py   # SSE streaming
│       ├── test_audit.py       # Audit logging
│       ├── test_failclosed.py  # Fail-closed scenarios (identity missing, graph down)
│       ├── test_integration_cache_acl.py  # Cache + ACL together
│       ├── test_integration_semantic.py   # Semantic cache integration
│       ├── test_integration_streaming.py  # Streaming + caching
│       │
│       └── e2e/                # End-to-end tests (requires live Onyx + Ollama)
│           ├── run_e2e.py      # Test runner
│           ├── llm_relay.py    # Dummy LLM relay (mock Ollama for reproducible testing)
│           ├── vectors.py      # Pre-computed embeddings (semantic cache testing)
│           ├── RUN_TRANSCRIPT.txt  # Sample test output
│           └── RESULTS.md      # E2E results summary
│
├── actions/                     # ONIX LAYER: audit, docgen, tasks, admin, audit-log
│   ├── README.md               # Actions-specific setup
│   ├── Dockerfile              # Multi-stage build
│   ├── app/                    # Main FastAPI app
│   │   ├── __init__.py         # Version
│   │   ├── main.py             # FastAPI app, endpoints (audit, docgen, tasks, notify, etc.)
│   │   ├── admin_state.py      # Kill-switch, feature flags, user blocks (sqlite/postgres)
│   │   ├── audit_engine.py     # Extract canonical fields from OCR, normalize, compare vs reference
│   │   ├── audit_log.py        # HMAC-chained audit trail (tamper-evident), verification
│   │   ├── caller_identity.py  # HMAC or JWT signature validation (Onyx→Actions)
│   │   ├── celery_app.py       # Celery queue config (broker: Redis, result backend optional)
│   │   ├── cost_tracker.py     # Track usage (tokens, files, $$), budget enforcement
│   │   ├── db.py               # Stateless DB abstraction (SQLite default, Postgres opt-in)
│   │   ├── dlp.py              # Data loss prevention (egress filtering)
│   │   ├── docgen.py           # Generate .docx files from structured data
│   │   ├── llm.py              # LLM calls (Ollama local, fallback heuristic)
│   │   ├── notify.py           # Notifications (email, webhook)
│   │   ├── objstore.py         # Object store (MinIO/S3) for artifacts
│   │   ├── ocr.py              # OCR wrapper (extract text/tables from PDFs)
│   │   ├── retention.py        # RGPD right-to-be-forgotten (delete user data)
│   │   ├── security.py         # Rate limiting, role-based access (require_admin, require_caller)
│   │   ├── safe_logger.py      # PII redaction in logs (no JWT/IBAN/NIR/email leakage)
│   │   ├── tasks.py            # Task queue + state (async job tracking)
│   │   └── usage_tracker.py    # Usage events (per-user, per-feature)
│   │
│   ├── requirements.txt        # Dependencies (fastapi, celery, prometheus-client, pydantic, etc.)
│   ├── requirements-dev.txt    # Dev/test deps
│   │
│   └── tests/                  # Unit tests (pytest, offline)
│       ├── conftest.py         # Shared fixtures
│       ├── test_audit_engine.py    # Field extraction, normalization, comparison
│       ├── test_audit_log.py       # HMAC chain, verification
│       ├── test_admin_state.py     # Kill-switch, feature flags
│       ├── test_caller_identity.py # HMAC/JWT signature validation
│       ├── test_cost_tracker.py    # Cost estimation, budget check
│       ├── test_db.py              # DB abstraction (SQLite/Postgres translation)
│       ├── test_dlp.py             # DLP filtering rules
│       ├── test_docgen.py          # .docx generation
│       ├── test_llm.py             # LLM calls (mocked)
│       ├── test_ocr.py             # OCR output parsing
│       ├── test_retention.py       # Data deletion logic
│       ├── test_safe_logger.py     # PII redaction in logs
│       └── test_tasks.py           # Task queue state
│
├── prompts/                     # ONIX LAYER: agent commercial (LLM prompts)
│   ├── system_prompt.md        # System instructions for commercial assistant
│   ├── anti_injection.md       # Injection prevention patterns
│   └── examples/               # Few-shot examples (optional)
│
├── tests/
│   └── rag/                    # RAG evaluation + red-team + guardrail testing
│       ├── conftest.py         # Fixtures (golden dataset, LLM mocks)
│       ├── requirements.txt    # pytest, ragas, requests, pyyaml (offline + live)
│       ├── test_guardrails.py  # Red-team + guardrail verification (heuristics)
│       ├── test_citations.py   # Citation accuracy (real vs expected)
│       ├── test_dataset.py     # Golden dataset QA (offline contract tests)
│       ├── ragas_eval/         # RAGAS evaluation runner (faithfulness, precision, relevancy)
│       │   ├── runner.py       # Main eval loop (LLM-judge Ollama local)
│       │   ├── gates.py        # Quality thresholds (min scores)
│       │   └── reports/        # Output reports (per-run)
│       └── data/
│           ├── golden_qa_fr.yaml     # Reference QA pairs (French, factual)
│           ├── red_team_prompts.yaml # Adversarial prompts (injection, exfil, hallucination)
│           └── context_corpus.md     # Knowledge base (short, for reproducibility)
│
├── monitoring/                 # Observability stack (local)
│   ├── docker-compose.monitoring.yml  # Prometheus, Grafana, Loki
│   ├── prometheus.yml          # Scrape config (Onyx, gateway, actions, ollama)
│   ├── rules.yml               # Alert rules (KillSwitchBlockingTraffic, etc.)
│   ├── loki-config.yml         # Log aggregation config
│   └── dashboards/             # Grafana dashboards (JSON)
│       ├── onix-gateway.json   # Cache hits, ACL blocks, guardrails
│       ├── onix-actions.json   # Cost tracking, audit log, admin state
│       └── onyx-overview.json  # Onyx performance, indexing, errors
│
├── deploy/
│   ├── k8s/onix-ha/            # Kubernetes Helm chart (production HA)
│   │   ├── Chart.yaml          # Helm metadata
│   │   ├── values.yaml         # Default values (replicas, resource limits, etc.)
│   │   ├── values-kind-smoke.yaml  # KinD smoke test config
│   │   ├── templates/          # K8s resources
│   │   │   ├── configmap.yaml  # Config maps (group mapping, doc ACL)
│   │   │   ├── secret.yaml     # Secrets template (generated by setup script)
│   │   │   ├── postgres-cluster.yaml  # CloudNative-PG cluster (HA, automatic failover)
│   │   │   ├── redis.yaml      # Redis Helm subchart (HA with Redis Operator)
│   │   │   ├── opensearch.yaml # OpenSearch Helm subchart (multi-node statefulset)
│   │   │   ├── minio.yaml      # MinIO Helm subchart (distributed S3)
│   │   │   ├── api.yaml        # Onyx API deployment (HPA, liveness, readiness)
│   │   │   ├── background.yaml # Onyx background workers (scaled by Celery)
│   │   │   ├── model-servers.yaml  # Inference model-server replicas
│   │   │   ├── ollama.yaml     # Ollama statefulset (GPU nodeSelector, shared cache)
│   │   │   ├── access-gateway.yaml  # Access-gateway deployment (stateless, HPA)
│   │   │   ├── actions.yaml    # Onix-actions deployment (stateless, HPA)
│   │   │   ├── actions-queue.yaml   # Optional: separate Celery worker pool
│   │   │   ├── webserver.yaml  # Onyx web frontend
│   │   │   ├── ingress.yaml    # K8s Ingress (TLS, routing to gateway + Onyx)
│   │   │   ├── migrations-job.yaml  # Pre-install Job (Alembic migrations)
│   │   │   ├── cronjob-opensearch-snapshot.yaml  # Scheduled OpenSearch backups
│   │   │   ├── cronjob-minio-mirror.yaml        # Scheduled MinIO sync
│   │   │   └── NOTES.txt       # Helm post-install notes
│   │   └── charts/             # Helm subcharts (pre-downloaded)
│   │       ├── cloudnative-pg-0.26.0.tgz
│   │       ├── redis-0.16.6.tgz
│   │       ├── opensearch-3.6.0.tgz
│   │       └── minio-5.4.0.tgz
│   │
│   ├── azure/                  # Azure/AKS deployment
│   │   ├── values-azure.yaml   # Helm values (Azure-specific: managed services, etc.)
│   │   ├── setup-entra.sh      # Setup Entra ID app registration + OIDC config
│   │   └── bicep/              # Infrastructure-as-Code (Azure Resource Manager)
│   │       ├── main.bicep      # Main template (AKS cluster, App Gateway, etc.)
│   │       ├── modules/        # Reusable components (VNet, NSG, storage, etc.)
│   │       ├── parameters.json # Parameter values (subscriptionId, location, etc.)
│   │       └── deploy.sh       # Deployment script (bicep build + az deployment)
│   │
│   ├── prod/                   # Production Compose deployment (exposed via Caddy TLS + OIDC)
│   │   ├── docker-compose.yml  # Caddy, oauth2-proxy, onix stack
│   │   └── Caddyfile           # TLS + domain routing
│   │
│   └── local-prod/             # Single-machine production (systemd unit)
│       ├── onix.service        # systemd unit (start at boot, restart on failure)
│       └── README              # Setup instructions (copy service, enable, start)
│
├── scripts/                    # Automation (bash/PowerShell)
│   ├── detect-hardware.sh      # CPU/RAM/GPU detection, --apply writes to .env
│   ├── detect-hardware.ps1     # Windows version
│   ├── gen-secrets.sh          # Generate random secrets, write to .env (idempotent)
│   ├── pull-models.sh          # Pre-pull Ollama models (ollama pull + verify)
│   ├── verify.sh               # End-to-end health check (connectivity, generation, guardrails)
│   ├── preflight-local.sh      # Pre-flight checks (docker, vm.max_map_count, RAM, ports)
│   ├── backup.sh               # Backup volumes (db, opensearch, ollama, minio)
│   ├── restore.sh              # Restore from backup
│   └── sync-doc-acl.py         # Sync SharePoint ACL (Graph API) → doc_acl.json
│
├── docs/                       # Full documentation (index: DOCS_INDEX.md)
│   ├── DOCS_INDEX.md           # Index of all docs (scopes → files)
│   ├── ARCHITECTURE.md         # Component-level architecture (detailed)
│   ├── RBAC.md                 # RBAC design + decision reasoning
│   ├── DECISION_RBAC.md        # Why RBAC is output-filtered (perm-sync = EE)
│   ├── CACHE.md                # Cache design (exact + semantic, RBAC isolation)
│   ├── STREAMING.md            # SSE streaming implementation
│   ├── ACTIONS.md              # Onix-actions capabilities
│   ├── FINOPS.md               # Cost tracking + budget enforcement
│   ├── AGENT_COMMERCIAL.md     # Commercial assistant (prompts, anti-injection)
│   ├── SECURITY.md             # Threat model, CVE tracking, hardening
│   ├── SECURITY_RGPD_ACTIONS.md    # RGPD compliance in actions
│   ├── RGPD.md                 # Right-to-be-forgotten, data retention
│   ├── PERFORMANCE.md          # Tuning guide (num_ctx, batch size, etc.)
│   ├── RAG_OPTIMIZATION.md     # RAG retrieval + reranking tuning
│   ├── RAG_EVAL.md             # RAGAS evaluation framework
│   ├── PLAYBOOK_ONYX_RAG.md    # Onyx RAG troubleshooting
│   ├── OBSERVABILITY.md        # Prometheus/Grafana/Loki + alerts
│   ├── RUNBOOK.md              # Operational guide (mono-poste)
│   ├── POC_LOCAL.md            # Local POC setup (dev/demo, 1-2 users, SharePoint)
│   ├── PROD_LOCAL.md           # Single-machine production (hardened compose + systemd)
│   ├── HA_SCALING.md           # HA architecture, Kubernetes, horizontal scaling
│   ├── DEPLOY_PROD.md          # Production Compose deployment (Caddy TLS + oauth2-proxy)
│   ├── DEPLOY_AZURE.md         # Azure/AKS deployment runbook
│   ├── PARITE_ENTREPRISE.md    # Feature parity with Copilot/AC360 (honest gaps)
│   ├── COMPARATIF_COPILOT_AC360.md  # Comparison matrix
│   │
│   ├── connectors/             # Connector-specific docs
│   │   └── SHAREPOINT.md       # SharePoint Graph connector, ACL sync, limitations
│   │
│   └── audit-onyx/             # Byte-level Onyx v4.1.1 audit (7 dimensions)
│       ├── 00-VERDICT.md       # Executive summary + decisions (FOSS gaps → onix solutions)
│       ├── 01-features.md      # Feature matrix (RAG, admin, security)
│       ├── 02-security.md      # Security audit (auth, encryption, audit trail)
│       ├── 03-architecture.md  # Architecture audit (scalability, SPOF)
│       ├── 04-performance.md   # Performance characteristics
│       ├── 05-operations.md    # Ops (deployment, monitoring, runbooks)
│       ├── 06-compliance.md    # Compliance gaps (RGPD, SOC2, audit)
│       └── 10-architecture-scalability.md  # Detailed scalability analysis
│
├── .gitleaks.toml              # Secret detection config (CI gate)
├── .gitignore                  # Ignore secrets, cache, volumes, .env
├── LICENSE                     # MIT license
└── .env                        # Runtime config (gitignored, generated by scripts)
```

## Directory Purposes

**Root Config & Entry:**
- `AGENTS.md` - **READ FIRST** — agent rules, architecture overview, build/test commands, scope map
- `ARCHITECTURE.md` - System-wide architecture (4 layers: ingress, gateway, Onyx, data)
- `Makefile` - Single-command control (tune, secrets, up, verify, test, deploy)
- `docker-compose*.yml` - Stack definition (main + gpu/perf overlays)

**Access-Gateway (RBAC + Cache):**
- `access-gateway/app/` - FastAPI application (main, config, identity, cache, guardrails, ACL)
- `access-gateway/config/` - Configuration templates (group mapping, document ACL)
- `access-gateway/tests/` - pytest suite (unit + integration, offline except e2e)

**Actions Microservice (Audit, DocGen, Admin):**
- `actions/app/` - FastAPI application (audit engine, docgen, tasks, cost tracker, admin state)
- `actions/tests/` - pytest suite (unit tests, offline)

**Prompts & RAG:**
- `prompts/` - Agent commercial system prompt (LLM instructions, anti-injection)
- `tests/rag/` - RAG evaluation (RAGAS, red-team, golden dataset, guardrail testing)

**Monitoring & Observability:**
- `monitoring/` - Prometheus, Grafana, Loki stack (local, for dev)

**Deployment & Infrastructure:**
- `deploy/k8s/onix-ha/` - Kubernetes Helm chart (production HA: postgres, opensearch, redis, minio, HPA)
- `deploy/azure/` - Azure/AKS deployment (values, Entra setup, bicep IaC)
- `deploy/prod/` - Production Compose (Caddy TLS + oauth2-proxy)
- `deploy/local-prod/` - Single-machine production (systemd unit)

**Scripts & Automation:**
- `scripts/` - Bash/PowerShell (hardware detection, secrets generation, model pre-pull, preflight checks, backup/restore, ACL sync)

**Documentation:**
- `docs/` - Full documentation index + component-level guides (RBAC, cache, actions, security, deployment, audit)
- `docs/audit-onyx/` - Byte-level audit of Onyx v4.1.1 (7 dimensions, verdict)

## Key File Locations

**Entry Points:**
- `access-gateway/app/main.py` - Gateway FastAPI app (routes, lifespan, health, metrics, chat endpoint)
- `actions/app/main.py` - Actions FastAPI app (routes, gating, audit, docgen, task endpoints)
- `docker-compose.yml` - Stack definition (services, volumes, networks)
- `Makefile` - Command orchestration (make up, make verify, make test)

**Configuration:**
- `.env` - Runtime variables (generated by `make secrets` + `make tune`, gitignored)
- `access-gateway/.env.template` - Gateway config template
- `deploy/k8s/onix-ha/values.yaml` - Helm defaults
- `deploy/azure/values-azure.yaml` - Azure-specific Helm values
- `deploy/azure/bicep/main.bicep` - Infrastructure-as-Code

**Core Logic:**
- `access-gateway/app/identity.py` - OIDC claim parsing, group resolution
- `access-gateway/app/cache.py` - HMAC-keyed cache (exact + semantic)
- `access-gateway/app/guardrail.py` - Post-filter heuristics
- `access-gateway/app/doc_acl.py` - Document-level ACL filtering
- `actions/app/audit_engine.py` - OCR field extraction, normalization, comparison
- `actions/app/audit_log.py` - HMAC-chained audit trail
- `actions/app/admin_state.py` - Kill-switch, feature flags

**Testing:**
- `access-gateway/tests/test_cache.py` - Cache isolation, RBAC
- `access-gateway/tests/test_guardrail.py` - Post-filter guardrails
- `access-gateway/tests/test_doc_acl.py` - Document ACL
- `actions/tests/test_audit_engine.py` - Audit field extraction
- `tests/rag/test_dataset.py` - RAG offline contract tests
- `tests/rag/ragas_eval/runner.py` - RAGAS quality evaluation

**Deployment & Ops:**
- `scripts/detect-hardware.sh` - CPU/RAM/GPU tuning
- `scripts/gen-secrets.sh` - Secret generation
- `scripts/pull-models.sh` - Ollama model pre-pull
- `scripts/verify.sh` - End-to-end health check
- `deploy/azure/setup-entra.sh` - Entra ID app registration setup
- `deploy/azure/bicep/deploy.sh` - AKS deployment

## Naming Conventions

**Files:**
- `test_*.py` - pytest modules
- `conftest.py` - pytest fixtures (shared across test directory)
- `.env` - Runtime config (gitignored)
- `.env.template` - Template (checked in)
- `*-compose.yml` - Docker Compose overrides (suffix pattern: `.gpu`, `.performance`, `.prod-local`)
- `*.md` - Documentation (French preferred, except code examples)

**Directories:**
- `app/` - Application source (FastAPI modules)
- `tests/` - Test modules (pytest)
- `config/` - Configuration templates
- `deploy/` - Deployment artifacts (compose, k8s, azure, scripts)
- `docs/` - Documentation
- `scripts/` - Executable scripts
- `monitoring/` - Observability stack

**Python Modules (Naming & Casing):**
- `lowercase_with_underscores.py` - Module files (PEP 8)
- `CamelCase` - Class names (dataclasses, Pydantic models)
- `snake_case` - Function and variable names
- `UPPERCASE` - Constants (env var names, config keys)

**Environment Variables:**
- `ONYX_*` - Onyx backend config
- `GATEWAY_*` - Access-gateway config
- `ONIX_*` - Onix-actions config (except ONIX_ACTIONS_DB → ONIX_ACTIONS_DB)
- `OLLAMA_*` - Ollama runtime config
- `POSTGRES_*`, `OPENSEARCH_*`, `REDIS_*`, `S3_*` - Data tier config

**Kubernetes / Helm:**
- `*.yaml` - Manifest files
- `values*.yaml` - Helm value overrides (suffix: `-azure`, `-kind-smoke`)
- `.helmignore` - Files to exclude from Helm package

## Where to Add New Code

**New Gateway Feature (RBAC, Caching, Guardrails):**
- **Implementation**: `access-gateway/app/` (add module if needed, import in `main.py`)
- **Tests**: `access-gateway/tests/test_<feature>.py` (offline, no Onyx dependency)
- **Config**: Add settings to `access-gateway/app/config.py` (12-factor env vars)
- **Example**: See `access-gateway/app/guardrail.py` (deterministic heuristics, no LLM)

**New Actions Capability (Audit, DocGen, Cost):**
- **Implementation**: `actions/app/<feature>.py` (follow db.py abstraction for state)
- **Tests**: `actions/tests/test_<feature>.py` (offline, use fixtures)
- **Endpoints**: Add route to `actions/app/main.py` (apply `_gate()` for admin control)
- **Metrics**: Update `actions/app/main.py` metrics (Prometheus labels bounded)
- **Example**: See `actions/app/cost_tracker.py` (stateless, db-backed, metric exported)

**New RAG Guardrail (Red-Team):**
- **Test Case**: `tests/rag/test_guardrails.py` (new test class/method)
- **Dataset**: Add to `tests/rag/data/red_team_prompts.yaml` (adversarial examples)
- **Evaluation**: Add to `tests/rag/ragas_eval/gates.py` (thresholds)
- **Example**: See `tests/rag/test_guardrails.py:test_citation_required()` (heuristic-based)

**New Utility / Shared Code:**
- **Helpers**: Add to existing modules in `access-gateway/app/` or `actions/app/`
- **Avoid new files** for small utils — keep module count low (prefer internal functions)
- **If multi-use**: Create a new module, but only if used by 2+ existing modules

**New Deployment Target (k8s, Azure, Docker Compose overlay):**
- **Compose overlay**: Create `docker-compose.<name>.yml` (reference in Makefile)
- **K8s**: Add template to `deploy/k8s/onix-ha/templates/`
- **Azure**: Add to `deploy/azure/bicep/` (or create module)
- **Values**: Add override to `deploy/k8s/onix-ha/values-<name>.yaml`
- **Docs**: Add runbook to `docs/DEPLOY_<NAME>.md`

**New Connector (e.g., Jira, GitHub, Notion):**
- **Implementation**: Onyx native — see `docs/connectors/SHAREPOINT.md` for pattern
- **ACL Sync**: If permission-based, add sync logic to `scripts/sync-doc-acl.py` or new script
- **Testing**: `tests/rag/` dataset should include samples from connector (for RAG eval)

**New Monitoring Dashboard / Alert:**
- **Dashboard**: Add JSON to `monitoring/dashboards/<scope>.json`
- **Rules**: Add to `monitoring/rules.yml` (Prometheus)
- **Loki queries**: Update `monitoring/loki-config.yml`
- **Example**: See `monitoring/dashboards/onix-gateway.json` (cache hits, ACL blocks)

## Special Directories

**Volumes (Persisted, Gitignored):**
- `db_volume` - Postgres data (docker-compose)
- `opensearch-data` - OpenSearch shards (docker-compose)
- `ollama_data` - Ollama model weights (docker-compose)
- `minio_data` - MinIO S3 storage (docker-compose)
- `file-system` - Onyx document files (docker-compose)
- `model_cache_huggingface` - Embedding model cache (docker-compose)

**Generated / Temporary:**
- `.env` - Runtime config (generated, gitignored)
- `access-gateway/data/` - SQLite state (if GATEWAY_CACHE_BACKEND=sqlite)
- `actions/data/onix_actions.db` - SQLite state (if ONIX_DB_BACKEND=sqlite)
- `tests/rag/ragas_eval/reports/` - Evaluation output (per-run)
- `docker-compose.<name>.yml` - Overlays (committed, but composed dynamically via Makefile)

**Configuration (Committed, Not Secrets):**
- `access-gateway/config/doc_acl.example.json` - Example ACL (committed)
- `access-gateway/config/group_map.example.json` - Example mapping (committed)
- `.env.template` - Template (committed, no secrets)
- `Makefile` - Build/test/deploy commands (committed)

---

*Structure analysis: 2026-06-18*
