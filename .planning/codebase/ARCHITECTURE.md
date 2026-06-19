<!-- refreshed: 2026-06-19 -->
# Architecture

**Analysis Date:** 2026-06-19

## System Overview

```text
                         Utilisateur (navigateur / API)
                                   │  HTTPS
                         ┌─────────▼──────────┐
                         │ Ingress (nginx)    │  TLS + oauth2-proxy OIDC
                         │ + GEREP branding   │  (Entra ID, X-OIDC-Claims)
                         └───┬──────────┬─────┘
                /api/chat/* → │          │ ← reste → Onyx natif
                       ┌──────▼────────┐│
  COUCHE ONIX ────────►│ access-gateway││  RBAC (groupes→Document Sets),
  (valeur ajoutée)     │ (FastAPI)     ││  ACL par-doc (SharePoint + Fabric),
                       │ + Fabric      ││  cache RBAC-safe, streaming SSE,
                       │ X-OIDC-Claims ││  post-filtre garde-fous, /metrics
                       └──────┬────────┘│
                              ▼         ▼
                       ┌──────────────────────────┐
  COUCHE ONYX ────────►│ api_server (FastAPI)     │   ┌─────────────────┐
  (FOSS, MIT)          │ background (Celery)      │◄──┤ inference (emb) │
                       │ `onyx-backend:4.1.1`     │   │ model-server    │
                       └───┬───────┬──────┬───────┘   └─────────────────┘
                           │       │      │
              ┌────────────┼───────┼──────┼──────────────┐
              ▼            ▼       ▼      ▼              ▼
        ┌──────────┐ ┌──────────┐ ┌──────┐ ┌────────┐ ┌──────────────────┐
        │ Postgres │ │OpenSearch│ │Redis │ │MinIO   │ │ Ollama (LLM)      │
        │ (métadon)│ │(vecteur+ │ │(cache│ │(fichier│ │ `ollama_chat`     │
        │          │ │ BM25)    │ │broker│ │ S3)    │ │ CPU/GPU local     │
        └──────────┘ └──────────┘ └──────┘ └────────┘ └──────────────────┘
              ▲ DATA TIER (COUCHE INFRA)

   ┌─────────────────────────────────────────────────────────────┐
   │ onix-actions (microservice FastAPI)                         │
   │ - audit engine (OCR, champs normalisés)                     │
   │ - docgen (.docx)                                            │
   │ - tâches, notifications, usage/coût                         │
   │ - admin (kill-switch, feature flags)                        │
   │ - audit HMAC chaîné (tamper-evident)                        │
   │ - PII, DLP, rétention/effacement                            │
   └─────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────┐
   │ Ralph (orchestration des boucles production-ready)          │
   │ - `ralph/loop.sh` (runner itérations)                       │
   │ - `ralph/scopes/<scope>.md` (prompts spécialisés)           │
   │ - `ralph/state/<scope>.md` (journal d'état par scope)       │
   │ - `ralph/ORCHESTRATION.md` (grille A1–A7, gates qualité)    │
   └─────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────┐
   │ Observabilité (stack séparée : monitoring/)                 │
   │ - Prometheus (collecte métriques + règles d'alerte)         │
   │ - Grafana (dashboards onix-gateway, onix-actions, onix-infra)
   │ - Loki + Promtail (agrégation de logs)                      │
   │ - Alertmanager (routage, déduplication)                     │
   │ - exporters (postgres, redis, opensearch, node, blackbox)   │
   └─────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Composant | Responsabilité | Fichier |
|-----------|----------------|--------|
| **access-gateway** | Proxy RBAC identity-aware ; enforcement Document Set ; ACL par-doc (SharePoint + **Fabric**) ; cache RBAC-safe ; guardrails post-filtre ; SSE streaming ; `/metrics` | `access-gateway/app/main.py` |
| **fabric_client** | Client Microsoft Fabric/OneLake/Power BI (app-only, read-only, fail-closed) ; énumération workspaces + items ; roleAssignments ; OneLake DFS (GOLD-only) ; principalAccess (PREVIEW) | `access-gateway/app/fabric_client.py` |
| **fabric_acl** | Décision RBAC par-document pour Fabric (fail-closed) ; OR des sources : roleAssignments + principalAccess OneLake ; périmètre GOLD imposé | `access-gateway/app/fabric_acl.py` |
| **identity** | Parse claims OIDC (X-OIDC-Claims) ; résolution groupes Entra ID (Graph ou claims) ; cache TTL | `access-gateway/app/identity.py` |
| **cache** | Stockage réponses HMAC-keyed (exact + sémantique) ; isolation par périmètre autorisé ; clé = question + Document Sets + locale | `access-gateway/app/cache.py` |
| **guardrail** | Post-filtre déterministe (citations, hallucination, injection, exfiltration) — hors-LLM → non-manipulable | `access-gateway/app/guardrail.py` |
| **doc_acl** | Filtre ACL par-document (SharePoint via Graph) | `access-gateway/app/doc_acl.py` |
| **graph_acl** | Dérivation RBAC SharePoint depuis Microsoft Graph (app-only) ; sync avec mapping doc→item | `access-gateway/app/graph_acl.py` |
| **audit** | Décisions d'accès + guardrails avec pseudonymisation HMAC | `access-gateway/app/audit.py` |
| **onyx-actions** | Motor audit (OCR→champs), docgen (.docx), tâches, notifications, cost tracking, admin kill-switch, audit log chaîné | `actions/app/*` |
| **audit_engine** | Extraction champs canoniques (OCR), normalisation montants/dates/noms, comparaison référence | `actions/app/audit_engine.py` |
| **audit_log** | Chaîne HMAC audit-trail (tamper-evident, vérifiable) | `actions/app/audit_log.py` |
| **ollama** | Inférence LLM local (CPU/GPU, pas d'appels cloud, télémétrie off) ; modèle en mémoire | `docker-compose.yml` service |
| **Ralph (orchestration)** | Boucles itératives production-ready ; grille A1–A7 (tests/sécurité/observabilité/fiabilité/reproductibilité/RGPD) ; gates qualité ; journalisation d'état | `ralph/loop.sh`, `ralph/scopes/<scope>.md`, `ralph/state/<scope>.md` |
| **Monitoring stack** | Collecte métriques + logs ; alertes ; dashboards ; séparé de la stack applicative (opt-in) | `monitoring/docker-compose.monitoring.yml` + configs |

## Pattern Overview

**Patterns clés:**
- **Fail-closed** : identité manquante, Graph indisponible, ACL erreur → refus systématique
- **Déterminisme sécurité** : post-filtre hors-LLM (non-manipulable par injection)
- **Tier applicatif stateless** : api/background/gateway/actions → réplicas + HPA (état → DB/Redis)
- **Souveraineté** : LLM local (Ollama), aucun appel cloud IA, télémétrie off
- **Auditable** : compose unique, images épinglées, audit HMAC chaîné, config-driven
- **Fabric intégré** : deuxième source d'ACL (SharePoint + Fabric, coexistent, fail-closed)
- **Observabilité dédiée** : stack opt-in, aucun overhead si off ; Prometheus + Grafana + Loki
- **Production-ready par boucles** : Ralph orchestre itérations → A1–A7 vérifiées → `RALPH_DONE` → déploiement

## Layers

**Couche Ingress:**
- **Purpose** : Terminaison TLS, authentification OIDC (Entra ID), injection X-OIDC-Claims
- **Location** : `nginx` + `oauth2-proxy` (docker-compose ou Caddy prod)
- **Contains** : Config nginx, intégration SSO, surcouche branding GEREP (nginx sub_filter, CSS, favicon)
- **Depends on** : Entra ID OIDC provider
- **Used by** : Tous les services aval (via X-OIDC-Claims header)
- **Branding GEREP** : `nginx/branding/gerep-theme.css` + `nginx/branding/favicon.svg` injectés via `sub_filter` dans `</head>` ; `<title>Onyx</title>` → `<title>GEREP — Assistant Client 360</title>`

**Couche Access Gateway (ONIX):**
- **Purpose** : RBAC enforcement (groupes → Document Sets) ; ACL par-doc (SharePoint + **Fabric**) ; cache RBAC-safe ; guardrails post-filtre ; observabilité ; streaming SSE
- **Location** : `access-gateway/` (microservice FastAPI)
- **Contains** : Identity resolution, group mapping, cache logic, post-filters, metrics, Fabric integration
- **Depends on** : Onyx upstream, Redis (optional cache), Microsoft Graph (optional SharePoint ACL), **Microsoft Fabric API** (new, optional Fabric ACL), Ollama (optional semantic embeddings)
- **Used by** : Toutes les requêtes `/api/chat/*` (proxifiées depuis l'ingress)
- **Stateless** : Réplication horizontale possible (état → Redis/DB)
- **Fabric path** : `fabric_client.py` (lecture seule, fail-closed) + `fabric_acl.py` (décision RBAC, périmètre GOLD)

**Couche Onyx FOSS:**
- **Purpose** : Orchestration RAG (chat, recherche, indexation, connecteurs)
- **Location** : `api_server`, `background` (onyxdotapp/onyx-backend:4.1.1)
- **Contains** : Endpoints FastAPI, workers Celery, migrations Alembic
- **Depends on** : Postgres, OpenSearch, Redis, model-server, MinIO
- **Used by** : Gateway proxy, UI frontend
- **Stateless** : Aucun état local (tout → data tier)

**Couche Model Inference:**
- **Purpose** : Embeddings + reranking (déterministe, rapide)
- **Location** : `inference_model_server` (onyxdotapp/onyx-model-server:4.1.1)
- **Contains** : Sentence transformers, rerank models
- **Depends on** : HuggingFace cache
- **Used by** : Background indexing, retrieval pipeline, optional semantic cache (gateway)

**Couche LLM:**
- **Purpose** : Génération texte (local, souverain)
- **Location** : `ollama` (ollama/ollama:0.30.8)
- **Contains** : Poids modèles (via `scripts/pull-models.sh`), moteur inférence
- **Depends on** : CPU/GPU hôte, modèles sur disque
- **Used by** : Endpoint génération Onyx, cache sémantique gateway (opt-in)

**Couche Actions (onix-actions):**
- **Purpose** : Audit trail, docgen, tâches, cost tracking, admin controls, RGPD
- **Location** : `actions/` (FastAPI, invoqué comme Onyx Custom Action)
- **Contains** : Audit engine, audit log, admin state, cost tracker, task queue
- **Depends on** : SQLite/Postgres (stateful), Celery (optional queue), MinIO (docgen)
- **Used by** : Assistant Onyx (via Onyx Custom Action hook OpenAPI)

**Couche Orchéstration Ralph (Meta/Dev Process):**
- **Purpose** : Orchestration itérative de la montée au « production-ready entreprise » ; grille A1–A7 (exactitude doc↔code, tests, sécurité, observabilité, fiabilité, reproductibilité, RGPD) ; gates qualité (pytest, bandit, gitleaks, pip-audit, trivy) ; journalisation d'état ; arrêt sur `RALPH_DONE`
- **Location** : `ralph/` (shell + markdown)
  - `ralph/loop.sh` : Runner itérations (max 8 par défaut) ; gates ciblés par scope ; commit atomique vert-only
  - `ralph/scopes/<scope>.md` : Prompts spécialisés (7 scopes : access-gateway, actions, rag-prompts, deploy-ops, monitoring, security-governance)
  - `ralph/state/<scope>.md` : Journal d'état revu à chaque itération (backlog P0/P1/P2, itérations, questions bloquantes, checkpoints A1–A7)
  - `ralph/ORCHESTRATION.md` : Grille d'acceptation, contrats d'itération, matrice agent×scope×skills×MCP
- **Used by** : Agents IA + humains pilotant la montée en production
- **Surfaces disjointes** : un scope = une surface (clone isolé, branche locale) ; merge par SHA vers intégration

**Couche Data Tier (Infra):**
- **Postgres** : Métadonnées Onyx, comptes utilisateurs, configs ; admin state onix, audit log, usage tracking
- **OpenSearch** : Index vectoriel (embeddings) + recherche lexicale (BM25)
- **Redis** : Broker Celery, backend cache, session store
- **MinIO/S3** : Fichiers documents, artefacts .docx générés

**Couche Observabilité (Stack Séparée, Opt-In):**
- **Purpose** : Collecte métriques + logs ; alertes actionnables ; dashboards ; aucun overhead si off
- **Location** : `monitoring/docker-compose.monitoring.yml` + configs (Prometheus, Grafana, Loki, Promtail, exporters)
- **Dépendances** : Réseau applicatif `onix-net` (pour scraper services) + réseau interne `monitoring`
- **Services**:
  - **Prometheus** (`v3.1.0`) : Collecte métriques (15j rétention TSDB) + évaluation règles d'alerte ; jobs `onix-actions:8100/metrics`, blackbox /health
  - **Grafana** (`11.4.0`) : Dashboards (onix-gateway, onix-actions, onix-infra) ; UI uniquement à `127.0.0.1:3001` ; creds via .env
  - **Loki** (`3.3.0`) : Agrégation logs ; stockage disque local (chunks + index)
  - **Promtail** (`3.3.0`) : Pousse logs Docker vers Loki ; socket Docker :ro
  - **Exporters** : node-exporter (CPU/RAM/disque), postgres-exporter, redis-exporter, opensearch-exporter (ES compat), blackbox-exporter
- **Activation** : `make monitor-up` (après `make up`) ; `docker compose -f monitoring/docker-compose.monitoring.yml up -d`
- **Sécurité** : Aucun port hôte (sauf Grafana :127.0.0.1), cap_drop=ALL, read_only où possible, tmpfs /tmp
- **Used by** : SRE/ops pour monitoring/alerting ; pas d'appels sortants

## Data Flow

### Primary Chat Request Path

1. **User submits question** → Browser (HTTPS)
2. **Ingress → OIDC validation** → oauth2-proxy vérifie session, injecte `X-OIDC-Claims` header (`access-gateway/app/identity.py:resolve_principal`)
3. **Access Gateway identity resolution** → Parse claims OIDC ou fetch groupes Graph (`access-gateway/app/identity.py:resolve_principal`)
4. **RBAC filter** → Map groupes utilisateur → Document Sets autorisés (`access-gateway/app/main.py:_principal_and_sets`, `app/mapping.py`)
5. **Cache check (exact)** → Clé HMAC = question normalisée + Document Sets triés + locale (`access-gateway/app/cache.py:make_cache_key`)
   - **Hit** : Serve cached response, skip LLM, réapply ACL sur réponse cached
   - **Miss** : Proceed to upstream
6. **Proxy to Onyx** → POST `/v1/chat/send-message` avec enforcement `document_set_ids` périmètre utilisateur (`access-gateway/app/onyx_proxy.py:enforce_document_sets`)
7. **Onyx retrieval** → Recherche OpenSearch (top-k) + reranking model-server
8. **LLM generation** → Ollama (local, pas d'appel cloud)
9. **Streaming + post-filter** → SSE NDJSON token-par-token + filtre guardrails déterministe (citations, hallucination, injection) (`access-gateway/app/streaming.py:proxy_stream`, `app/guardrail.py:post_filter`)
10. **ACL per-document (final)** → Filtre citations/sources (SharePoint + **Fabric ACL**) (`access-gateway/app/doc_acl.py:filter_citations`, `fabric_acl.py:authorized_items`)
11. **Audit + response cache store** → Log décision accès + guardrails (HMAC) ; cache réponse si applicable
12. **Return to client** → Streaming SSE ou JSON (cached)

### Fabric ACL Resolution (New Path)

**When Fabric is enabled** (`settings.fabric_configured`):
1. **During initialization** (`_lifespan`) : `FabricClient` built (singleton httpx) ; token provider injectable
2. **Per-request (if Fabric item referenced)** :
   - `fabric_acl.can_principal_read(principal_id, workspace_id, item_id)` called
   - **Pre-checks** : id presence, Fabric enabled, gold-scope validation (`is_gold_path`)
   - **Source (a) — roleAssignments** : Fetch workspace role assignments (Fabric API) ; check if principal or their groups have read role (Viewer+)
   - **Source (b) — principalAccess OneLake** : Optional, PREVIEW API ; effective access query
   - **OR-merge** : Both sources fail-closed ; (b) only widens (a)
   - **Result** : Boolean ; if False, item hidden from response

**Fail-closed guarantees** :
- Gold-only : Non-gold items ALWAYS denied (even if role would allow)
- No SSRF : Hosts (Fabric API, OneLake, Power BI) from Settings constants
- Read-only : `FabricClient` GET-only, no POST/PUT/PATCH/DELETE
- Network errors → access denied (never granted on uncertainty)

### Orchestration Ralph Iteration Loop

1. **Sync** : `git pull` ; re-read `ralph/state/<scope>.md` ; pick next P0/P1/P2 item
2. **Plan** : Describe minimal fix (3–6 lines) + acceptance criterion (A1–A7)
3. **Implement** : Minimal change ; stdlib-first ; French comments ; respect neighboring code ; add tests
4. **Prove** : Run scope-specific gates (pytest, compose-validate, etc.) ; fix if red ; never commit red
5. **Reconcile docs** : Update scope doc + `docs/audit-reality/<scope>.md` (mark item ✅ with file:line proof)
6. **Journal** : Update `ralph/state/<scope>.md` (done/in-progress/remain, iteration#, commit SHA)
7. **Commit** : Atomic, conventional French message, gates green

**Sentinelle** : When all A1–A7 proven → write `RALPH_DONE` at top of `ralph/state/<scope>.md` → loop exits for scope

## Key Abstractions

**RBAC Abstraction (Groups → Document Sets):**
- **Purpose** : Map Entra ID groups → Onyx Document Set visibility ; deny-by-default
- **Files** : `access-gateway/app/mapping.py` (static JSON), `app/identity.py` (group resolution)
- **Pattern** : GroupMap loaded once at startup ; cached per-principal (TTL) ; no runtime mutation

**ACL Composition (SharePoint + Fabric):**
- **Purpose** : Combine multiple authorization sources in fail-closed OR
- **Files** : `access-gateway/app/doc_acl.py` (base + static), `app/graph_acl.py` (SharePoint), `app/fabric_acl.py` (Fabric), `app/main.py:_build_doc_acl` (composition)
- **Pattern** : `CompositeDocACL` merges sources ; each source independent ; any source error = omit (never crash gateway)

**Cache HMAC-Key (Exact + Semantic):**
- **Purpose** : Reuse answers across same perimeter + identical/similar questions
- **Files** : `access-gateway/app/cache.py`
- **Pattern** : Key = HMAC(secret, question_normalized + sorted_document_sets + locale) ; dedup within perimeter ; reapply ACL on hit/miss (paranoia filter)

**Streaming Proxy (Token-by-Token):**
- **Purpose** : Stream LLM response token-by-token via SSE (low latency perception)
- **Files** : `access-gateway/app/streaming.py:proxy_stream` ; relayed to client as `application/x-ndjson`
- **Pattern** : Proxy raw stream from Onyx ; inject guardrails on each token (no buffering) ; client reads async

**Deterministic Guardrails (Out-of-LLM):**
- **Purpose** : Filter hallucinations, injections, exfiltrations — determ inistically (not via LLM)
- **Files** : `access-gateway/app/guardrail.py:post_filter`
- **Pattern** : Regex + heuristics on final answer ; applied after LLM, before ACL (3-layer defense)

**Production-Readiness Grille (A1–A7):**
- **Purpose** : Objective acceptance criteria for "enterprise grade"
- **Files** : `ralph/ORCHESTRATION.md:§0` (definitive grille)
- **Pattern** : 7 axes (exactitude, tests, sécurité, observabilité, fiabilité, reproductibilité, RGPD) ; each has measurable proof (file:line, test output, diff)

## Entry Points

**access-gateway FastAPI app:**
- **Location** : `access-gateway/app/main.py:app` (FastAPI instance)
- **Triggers** : All `/api/*` requests (proxied from nginx)
- **Responsibilities** :
  - `GET /health` : Liveness probe (no auth)
  - `GET /v1/authorized-document-sets` : Introspection endpoint (debug/UX)
  - `POST /v1/chat/send-message` : Proxy + RBAC enforce + cache + guardrails + ACL per-doc
  - `GET /metrics` : Prometheus metrics (if `GATEWAY_METRICS_ENABLED`, default true)
  - `POST /v1/feedback` : Feedback ingestion (audit)

**onyx-actions Custom Action entry:**
- **Trigger** : Onyx assistant invokes custom action (OCR, docgen, notify, etc.)
- **Location** : `actions/app/main.py:app` (FastAPI)
- **Endpoints** : Onyx calls via OpenAPI schema (custom action hook)

**Ralph orchestration entry:**
- **Trigger** : User runs `./ralph/loop.sh <scope> [max_iter]`
- **Location** : `ralph/loop.sh` (bash runner)
- **Process** : Reads `ralph/scopes/<scope>.md` prompt → spawns `claude` CLI (headless) → runs gates → commits (if green)

**Observability stack entry:**
- **Trigger** : Operator runs `make monitor-up` or `docker compose -f monitoring/docker-compose.monitoring.yml up -d`
- **Location** : `monitoring/docker-compose.monitoring.yml` (orchestration)
- **Entry UI** : Grafana @ http://127.0.0.1:3001 (creds from .env)

## Architectural Constraints

- **Threading** : Event-loop async (FastAPI/httpx/aioredis) ; no threads except background Celery workers (Onyx) ; Ollama single-threaded LLM inference
- **Global state** : `app.state` in FastAPI (group map, http client, cache, doc_acl, acl_refresher task) — all initialized in `_lifespan`
- **Circular imports** : None known ; module import order: settings → clients → ACL → audit → main
- **Network** : All inter-service over internal `onix-net` Docker network ; ingress only via nginx:80 (localhost) ; no service publishes on host
- **Secrets** : Via `.env` file (gitignoré) ; generated by `scripts/gen-secrets.sh` ; never in repo ; ENCRYPTION_KEY_SECRET mandatory (`:?` in compose)
- **Database migrations** : Alembic (api_server container, pre-startup via `alembic upgrade head`) ; never inline (no race condition)
- **Session/tokens** : OIDC session managed by oauth2-proxy (Redis if HA) ; Onyx sessions in Postgres ; gateway stateless

## Anti-Patterns

### Accepting Unverified Claims

**What happens** : If X-OIDC-Claims header is accepted without OIDC verification upstream, attacker can forge identity
**Why it's wrong** : Breaks RBAC entirely ; no ACL enforcement ; complete information disclosure
**Do this instead** : Verify OIDC signature at ingress (oauth2-proxy, Caddy) ; gateway trusts header only if ingress is trusted proxy ; enforce `X-Forwarded-For` / `X-Real-IP` chain

### Silently Failing ACL

**What happens** : If `doc_acl` load fails at startup, gateway continues with disabled ACL (documents become visible to all)
**Why it's wrong** : Violates fail-closed ; unintended disclosure
**Do this instead** : Log CRITICAL + disable cache (fail-safe) ; continue with partial ACL if applicable ; never silently allow on ACL error during request ; test ACL composition (`_build_doc_acl`) on every startup

### Caching Without Re-Applying ACL

**What happens** : Cache hit returns stale response without re-checking ACL (e.g., user lost group membership)
**Why it's wrong** : Stale authorization ; user sees docs they should no longer see
**Do this instead** : Re-apply ACL filter (citations, document visibility) on every cache hit + miss (`access-gateway/app/main.py:496-502`) ; cache stores only answer text, not authorization state

### Streaming Without Guardrails

**What happens** : SSE streaming bypasses post-filter (guardrails applied only at final answer, not per-token)
**Why it's wrong** : Token stream could expose hallucination, injection, or exfiltration before filter catches it
**Do this instead** : Apply guardrails in-stream (lightweight per-token checks) ; buffer & filter before sending token if needed (`access-gateway/app/streaming.py:proxy_stream`)

### Fabric API Without Gold-Scope Guard

**What happens** : `FabricClient.onelake_read_file()` called without `is_gold_path()` check → reads arbitrary files (silver/bronze, out-of-scope)
**Why it's wrong** : Violates read-only-gold contract ; data leakage outside intended layer
**Do this instead** : Always gate calls with `is_gold_path()` (cf. `fabric_client.py:254-306`, `fabric_acl.py:66-73`) ; fail-closed if path not in gold; tests verify guard (`access-gateway/tests/test_fabric_client.py`)

### Mixing Ralph Scope Boundaries

**What happens** : Two agents edit same files (e.g., `Makefile`) in parallel → git merge conflicts + gates fail
**Why it's wrong** : Non-deterministic; breaks reproducibility; delays release
**Do this instead** : Surfaces disjointes (§5 AGENTS.md) ; one scope = one surface (clone/branch) ; shared files (Makefile, DOCS_INDEX, compose root) touched by one scope only or via sequential PR merge

## Error Handling

**Strategy** : Fail-closed + structured logging (no secret leakage)

**Patterns:**
- **Identity missing** → HTTP 401 Unauthorized (no guessing)
- **ACL error** → HTTP 403 Forbidden (deny by default, log reason for audit)
- **Upstream unavailable** → HTTP 503 Service Unavailable (after retry + timeout)
- **Guardrail triggered** → Redact segment + log (`access-gateway/app/guardrail.py:post_filter` + audit log)
- **Cache malformed** → Disable cache + log CRITICAL (fail-safe; no silent bypass)
- **Ralph gate red** → Stop iteration; log to `ralph/state/<scope>.md`; await human intervention
- **Fabric API error** → Log debug/warning (expected for PREVIEW APIs); continue with other sources (OR-merge)

No exception escapes without logging ; no stacktrace leaks to client ; no secret/jeton/claim visible in logs

## Cross-Cutting Concerns

**Logging:**
- **Tool** : Python stdlib `logging` module (locale: all messages en français pour les logs onix; anglais pour dépendances)
- **Format** : `"%(levelname)s:%(name)s:%(message)s"` (simple, JSON-friendly for Promtail)
- **Levels** : DEBUG (dev), INFO (startup/info), WARNING (degradation), ERROR (operational failure), CRITICAL (unrecoverable)
- **Pattern** : Log identity decisions (anonymized HMAC), ACL checks, cache hits, guardrails, upstream errors; never log jeton/secret/claim value

**Validation:**
- **Identity** : Mandatory `principal_id`, fail-closed if missing ; groups optional but checked if present
- **ACL** : All ids (document_set_id, principal_id, workspace_id, item_id) non-empty ; fail-closed on parse error
- **Cache key** : HMAC secret required (fail-safe disable if missing) ; normalized question length bounded
- **Fabric paths** : Gold-scope validation before any network call ; GUID detection for OneLake addressing
- **Post-filter** : Regex compiled once at startup ; bounded pattern size

**Authentication (Upstream):**
- **OIDC** : oauth2-proxy validates token signature + expiry at ingress ; gateway trusts header only if behind verified reverse proxy
- **Custom Actions** : Onyx invokes via OpenAPI with secret (Onyx API key) ; gateway relays to actions (actions validates Onyx origin)
- **Pattern** : Never re-validate OIDC in gateway (ingress is authority) ; never issue/sign tokens in gateway ; always check authorization ⊂ authentication

**Observabilité (Metrics + Logs):**
- **Metrics** : Prometheus on `/metrics` (gateway: requests, cache hits, guardrails, latency; actions: audit ops) ; SLO/SLI recording rules in `monitoring/prometheus/rules/onix-slo.yml`
- **Logs** : Loki + Promtail (all container logs aggregated) ; searchable by service, level, component
- **Alerts** : `monitoring/prometheus/rules/onix-alerts.yml` (e.g., upstream unavailable, cache disabled, Fabric API errors)
- **Dashboards** : Grafana (onix-gateway, onix-actions, onix-infra per `monitoring/grafana/dashboards/`)

---

*Architecture analysis: 2026-06-19*
