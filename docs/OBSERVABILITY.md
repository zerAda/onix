# Observabilité & gates qualité/sécurité (WS6)

Ce document décrit la **stack d'observabilité** d'onix (Prometheus, Alertmanager,
Grafana, Loki/Promtail, exporters), les **barrières CI/CD** (qualité, sécurité,
supply-chain) et leur **intégration**. Tout est **100 % local / souverain** :
images épinglées, aucune télémétrie sortante, aucun service cloud.

> Périmètre WS6 : `.github/workflows/{ci,cd}.yml`, `monitoring/`,
> `docs/OBSERVABILITY.md`, blocs dédiés dans `Makefile` et `env.template`.
> L'endpoint `/metrics` du microservice `actions/` est **livré** (cf. §5,
> `actions/app/main.py:359-372`) ; ce document décrit la collecte associée.

---

## 1. Stack de monitoring

Fichier : [`monitoring/docker-compose.monitoring.yml`](../monitoring/docker-compose.monitoring.yml).
Stack **séparée** de l'applicative : on l'active uniquement pour superviser.

| Service | Image (épinglée) | Rôle | Port hôte |
|---|---|---|---|
| `prometheus` | `prom/prometheus:v3.1.0` | Collecte des métriques + évaluation des alertes | — (interne) |
| `alertmanager` | `prom/alertmanager:v0.27.0` | Routage / notification des alertes | — (interne) |
| `grafana` | `grafana/grafana:11.4.0` | Dashboards | **127.0.0.1:3001** |
| `loki` | `grafana/loki:3.3.0` | Agrégation de logs | — (interne) |
| `promtail` | `grafana/promtail:3.3.0` | Collecte des logs conteneurs → Loki | — (interne) |
| `node-exporter` | `prom/node-exporter:v1.8.2` | Métriques hôte (CPU/RAM/disque) | — (interne) |
| `postgres-exporter` | `prometheuscommunity/postgres-exporter:v0.16.0` | Métriques PostgreSQL | — (interne) |
| `redis-exporter` | `oliver006/redis_exporter:v1.67.0` | Métriques Redis | — (interne) |
| `opensearch-exporter` | `quay.io/prometheuscommunity/elasticsearch-exporter:v1.7.0` | Métriques OpenSearch (API ES-compatible) | — (interne) |
| `blackbox-exporter` | `prom/blackbox-exporter:v0.25.0` | Sondes HTTP « boîte noire » (santé) | — (interne) |

**Sécurité / souveraineté :**
- Seul **Grafana** publie un port, lié à **127.0.0.1 uniquement** (même posture
  que `nginx` dans la stack applicative). Prometheus/Alertmanager/Loki n'exposent
  **aucun** port hôte (accès via Grafana en proxy, ou `docker exec`).
- Promtail monte le socket Docker en **lecture seule**.
- Grafana est configuré sans inscription, sans accès anonyme, et **toutes les
  vérifications/MAJ/news/analytics sortantes sont désactivées**.

**Réseaux :** la stack rejoint le réseau applicatif **`onix-net` (externe)** pour
scraper les services par leur nom (`actions`, `relational_db`, `cache`,
`opensearch`, `nginx`), plus un réseau interne `onix-monitoring-net`.

### Démarrage

```bash
make up            # stack applicative -> crée le réseau onix-net
make monitor-up    # stack d'observabilité (Grafana sur http://localhost:3001)
make monitor-down  # arrêt
```

Identifiants Grafana : `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` (cf.
`env.template`). **Renseignez un mot de passe fort** avant `make monitor-up`.

---

## 2. Ce qui est mesuré

### Métriques d'infrastructure (disponibles immédiatement)

- **Hôte** (node-exporter) : CPU %, RAM disponible/utilisée, espace disque par
  point de montage, réseau.
- **PostgreSQL** (postgres-exporter) : `pg_up`, connexions, transactions, taille
  des bases.
- **Redis** (redis-exporter) : `redis_up`, clients connectés, mémoire, hits/miss.
- **OpenSearch** (elasticsearch-exporter) : santé du cluster, heap JVM, documents.
- **Sondes HTTP** (blackbox-exporter) : `probe_success` sur `actions:8100/health`
  et `nginx:80/nginx-health` — **disponibilité « boîte noire »**, complémentaire
  des métriques applicatives.

### Métriques applicatives `onix-actions` (livré — cf. §5)

L'endpoint `/metrics` est exposé par le microservice (`actions/app/main.py`,
port 8100) ; Prometheus collecte :

| Métrique | Type | Sens |
|---|---|---|
| `onix_http_requests_total{endpoint,method,status}` | counter | Débit + ventilation par code HTTP (→ taux d'erreur 5xx) |
| `onix_http_request_duration_seconds_bucket{endpoint,le}` | histogram | Latence (p50/p95/p99) |
| `onix_killswitch_blocked_total{feature,reason}` | counter | **403 du kill-switch** (global/feature/user) |
| `onix_budget_spent_eur` | gauge | Coût estimé cumulé (FinOps) |
| `onix_budget_limit_eur` | gauge | Budget alloué |
| `onix_budget_ratio` | gauge | Ratio consommé (0–1+) → alertes budget |
| `onix_up` | gauge | Présence du process (voir limite ci-dessous) |

> **Limite connue — `onix_up` figé à 1.** Cette jauge est posée une seule fois au
> démarrage côté `actions` (`actions/app/main.py`, `UP.set(1)`) et n'est jamais
> remise à 0 : un process mort cesse simplement d'être scrappé (série absente),
> elle ne descend donc jamais à 0. Elle **n'apporte rien de plus que la métrique
> standard `up`** générée par Prometheus pour chaque cible. **Préférez `up`**
> (et l'alerte `TargetDown` / la sonde blackbox `ActionsServiceDown`) pour la
> vivacité réelle. Correctif côté `actions` hors périmètre du scope monitoring
> (cf. `docs/audit-reality/monitoring.md` §P2-6).

### Logs (Loki / Promtail)

Tous les logs des conteneurs sont collectés et étiquetés par `container`,
`compose_service`, `compose_project`, `stream`. Le dashboard `onix-actions`
intègre un panneau **Logs** filtré sur `{compose_service="actions"}` : on voit
le mode d'extraction (LLM vs heuristique), les échecs notify, etc.

---

## 3. Dashboards Grafana

Auto-provisionnés (dossier `onix`) depuis `monitoring/grafana/dashboards/` :

1. **`onix-actions` — santé applicative & FinOps** : santé du service, cibles
   UP/total, budget consommé, taux d'erreur 5xx, débit par endpoint, latence
   p50/p95/p99, **403 kill-switch par raison/feature**, dépensé vs budget (EUR),
   logs Loki.
2. **`onix-infra` — infrastructure & services** : timeline de disponibilité
   (`up`) de toutes les cibles, CPU/RAM/disque hôte, `pg_up`/`redis_up`, clients
   Redis, **santé du cluster OpenSearch** (green/yellow/red), **heap JVM
   OpenSearch (%)** et **documents indexés** (métriques de l'elasticsearch-exporter
   `elasticsearch_cluster_health_status{color}`, `elasticsearch_jvm_memory_*_bytes`,
   `elasticsearch_indices_docs`).

Les panneaux applicatifs se peuplent dès que la stack supervise un service
`actions` en marche (endpoint `/metrics` **livré**, cf. §5) ; les panneaux infra
et la timeline de disponibilité le sont de même.

---

## 4. Alertes

Règles : [`monitoring/prometheus/rules/onix-alerts.yml`](../monitoring/prometheus/rules/onix-alerts.yml).
Routage : [`monitoring/alertmanager/alertmanager.yml`](../monitoring/alertmanager/alertmanager.yml).

| Alerte | Condition | Sévérité | Dépend de /metrics ? |
|---|---|---|---|
| `TargetDown` | `up == 0` pendant 2 min | critical | non |
| `ServiceProbeFailed` | `probe_success == 0` pendant 2 min | critical | non |
| `ActionsServiceDown` | sonde `/health` actions KO 1 min | critical | non |
| `HostHighCpu` | CPU > 90 % pendant 10 min | warning | non |
| `HostLowMemory` | RAM dispo < 10 % pendant 10 min | warning | non |
| `HostLowDisk` | disque libre < 10 % pendant 10 min | warning | non |
| `HighErrorRate` | 5xx > 5 % des requêtes (5 min) | warning | **oui** |
| `HighLatencyP95` | p95 > 2 s (5 min) | warning | **oui** |
| `KillSwitchBlockingTraffic` | 403 kill-switch ≥ 1 req/s (5 min) | info | **oui** |
| `BudgetWarning` | consommé ≥ 80 % et < 100 % du budget | warning | **oui** |
| `BudgetExceeded` | consommé ≥ 100 % du budget | critical | **oui** |
| `OpenSearchClusterRed` | cluster OpenSearch en RED 2 min | critical | non |
| `OpenSearchExporterDown` | `elasticsearch_cluster_health_up == 0` 2 min | critical | non |
| `OpenSearchHeapHigh` | heap JVM OpenSearch > 90 % pendant 10 min | warning | non |

**Notification (FAIL-CLOSED) :** la livraison des alertes est **réelle** via
webhook (`ALERT_WEBHOOK_URL`, Slack/Mattermost/Teams-compatible, même convention
que `ONIX_NOTIFY_WEBHOOK`). L'URL passe par variable d'environnement — **jamais
committée** (`.env` gitignoré, cf. `env.template`).

- **Rendu au démarrage.** Alertmanager n'expanse pas les variables d'environnement
  dans sa config. Le conteneur lit donc un **gabarit**
  [`alertmanager/alertmanager.yml.tmpl`](../monitoring/alertmanager/alertmanager.yml.tmpl)
  et le **rend** au boot via
  [`alertmanager/entrypoint.sh`](../monitoring/alertmanager/entrypoint.sh) (substitution
  de `${ALERT_WEBHOOK_URL}`). Le receiver `webhook` est **réel** (plus de
  `webhook_configs` commenté) avec `send_resolved: true`.
- **Fail-closed (non négociable).** Si `ALERT_WEBHOOK_URL` est **absent/vide**, la
  stack **REFUSE de démarrer** Alertmanager — garde *au boot* (entrypoint, log
  `CRITICAL` + `exit 1`) **et** *au lancement* (`make monitor-up` refuse). On ne
  livre **plus** les alertes « dans le vide » comme l'ancienne config (receiver
  `default` vide + `webhook_configs` commenté), qui avalait silencieusement
  **toute** alerte (budget FinOps, service down, chaîne d'audit rompue).
- **Validation hors Docker.** `make monitor-render`
  ([`scripts/check-alertmanager-config.py`](../scripts/check-alertmanager-config.py),
  inclus dans `make test`) rend le gabarit et **asserte** : (1) un `webhook_configs`
  réel pointant l'URL ; (2) le refus fail-closed sans `ALERT_WEBHOOK_URL`.

> **Souveraineté.** Aucune dépendance cloud : le webhook cible un endpoint **fourni
> par le client** (Mattermost/Slack/Teams self-hosted ou autre récepteur HTTP). La
> config par défaut n'émet rien tant qu'aucune URL n'est fournie — mais alors la
> stack monitoring **ne démarre pas** (fail-closed), elle ne tourne pas « sourde ».

---

## 5b. Métriques `onix-access-gateway` (livré)

La passerelle RBAC (`access-gateway/`) expose `GET /metrics` sur son port
**8200** (cf. `access-gateway/Dockerfile`). Activé par défaut
(`GATEWAY_METRICS_ENABLED=true`) ; désactiver → 404 et aucun compteur.

### Noms de métriques

| Métrique | Type | Labels | Sens |
|---|---|---|---|
| `onix_gateway_requests_total` | counter | `endpoint`, `decision` (allow\|deny) | Toutes requêtes traitées |
| `onix_gateway_guardrail_total` | counter | `rule`, `blocked` (true\|false) | Déclenchements du post-filtre |
| `onix_gateway_answer_no_context_total` | counter | — | Réponses 2xx sans contexte documentaire |
| `onix_gateway_answer_with_citation_total` | counter | — | Réponses finales avec citation |
| `onix_gateway_answer_without_citation_total` | counter | — | Réponses finales sans citation |
| `onix_gateway_request_latency_seconds` | histogram | — | Latence bout-en-bout (buckets LLM : 0.5 à 120 s) |
| `onix_gateway_upstream_errors_total` | counter | — | Erreurs de relais Onyx (→ 502) |
| `onix_gateway_feedback_total` | counter | `rating` (up\|down) | Retours utilisateur (endpoint `/v1/feedback`) |
| `onix_gateway_cache_hits_total` | counter | `tier` (`exact`, futur `semantic`) | Hits du cache applicatif RBAC-safe (cf. [docs/CACHE.md](CACHE.md)) |
| `onix_gateway_cache_misses_total` | counter | — | Misses du cache applicatif (entrée absente/expirée) |
| `onix_gateway_cache_bypassed_total` | counter | `reason` (`no_store`\|`write_intent`\|`streaming`\|`explicit_admin_bypass`) | Requêtes pour lesquelles le cache a été volontairement contourné |
| `onix_gateway_cache_tokens_saved_total` | counter | — | Tokens approximatifs économisés par les hits (heuristique `chars/4`) |
| `onix_gateway_cache_seconds_saved_total` | counter | — | Secondes de génération économisées par les hits (heuristique constante `GATEWAY_CACHE_SECONDS_PER_HIT`) |
| `onix_gateway_cache_errors_total` | counter | `op` (`get`\|`set`) | Erreurs du backend de cache (exception-safe : déjà traduites en miss/no-op) |
| `onix_gateway_stream_requests_total` | counter | — | Requêtes traitées en **streaming NDJSON** (`application/x-ndjson`, relais token-par-token, cf. [docs/STREAMING.md](STREAMING.md) ; transport : `access-gateway/app/main.py:391`) |
| `onix_gateway_stream_aborted_total` | counter | `reason` (`no_prompt_leak`\|`no_exfil_relay`\|`read_only`\|`guard_error`\|`doc_acl_error`\|`postfilter_error`\|`internal_error`) | Flux NDJSON **avortés** par un garde DUR incrémental ou une erreur fail-closed |
| `onix_gateway_stream_overridden_total` | counter | — | Flux NDJSON dont la réponse finale a été **remplacée** par un override d'autorité (groundedness molle a posteriori, ou « pas de source accessible ») |

> **Streaming (NDJSON)** : le transport de flux de la passerelle est NDJSON
> (`application/x-ndjson`), pas SSE.
> `onix_gateway_stream_aborted_total{reason="no_prompt_leak"}`
> (et `no_exfil_relay`/`read_only`) mesure les garde-fous DURS déclenchés **en
> cours de flux** ; les `*_error` mesurent les coupures **fail-closed**. Voir
> [docs/STREAMING.md](STREAMING.md) §8 pour le détail du contrat client.

> **Hit-rate effectif** (hors bypass volontaires), à mettre dans le dashboard :
> `rate(onix_gateway_cache_hits_total[5m]) / (rate(onix_gateway_cache_hits_total[5m]) + rate(onix_gateway_cache_misses_total[5m]))`.
> Voir [docs/CACHE.md](CACHE.md) §6 pour le détail de l'observabilité du cache.

### Job Prometheus

```yaml
- job_name: onix-access-gateway
  metrics_path: /metrics
  static_configs:
    - targets: ["access-gateway:8200"]
      labels:
        service: onix-access-gateway
```

Ajouté dans `monitoring/prometheus/prometheus.yml`. Le service doit être sur
le réseau Docker `onix-net` pour être joignable par Prometheus.

### Caveat multi-worker (uvicorn `--workers N`)

En mode multi-worker, chaque processus uvicorn dispose de son propre registre
mémoire. Pour agréger correctement les métriques de tous les workers, definir
`PROMETHEUS_MULTIPROC_DIR` vers un répertoire partagé en écriture et utiliser
`multiprocess.MultiProcessCollector` à l'exposition. En mode single-worker
(défaut en conteneur), aucune configuration supplémentaire n'est nécessaire.
Voir : https://prometheus.github.io/client_python/multiprocess/

---

## 5. Endpoint `/metrics` du microservice `onix-actions` (livré)

> **Implémenté côté `actions/`.** Le job Prometheus `onix-actions` cible
> `http://actions:8100/metrics`, endpoint exposé par le microservice
> (`actions/app/main.py:359-372`, dépendance `prometheus-client==0.21.1`
> épinglée dans `actions/requirements.txt`). Les règles `onix_*` deviennent
> actives dès que la stack supervise un service `actions` en marche.

### Implémentation (réelle)

`GET /metrics` est exposé au format texte Prometheus, **non authentifié** (réseau
interne sans port hôte). Implémentation avec
[`prometheus-client`](https://github.com/prometheus/client_python), telle que
présente dans le code (extrait illustratif) :

1. `actions/requirements.txt` épingle `prometheus-client==0.21.1`.
2. `actions/app/main.py` est instrumenté ainsi :

```python
from prometheus_client import (
    Counter, Histogram, Gauge, CONTENT_TYPE_LATEST, generate_latest,
)
from fastapi import Request, Response

REQS = Counter("onix_http_requests_total", "Requêtes HTTP",
               ["endpoint", "method", "status"])
LATENCY = Histogram("onix_http_request_duration_seconds", "Latence HTTP",
                    ["endpoint"])
KILLSWITCH = Counter("onix_killswitch_blocked_total", "403 kill-switch",
                     ["feature", "reason"])
BUDGET_SPENT = Gauge("onix_budget_spent_eur", "Coût estimé cumulé (EUR)")
BUDGET_LIMIT = Gauge("onix_budget_limit_eur", "Budget alloué (EUR)")
BUDGET_RATIO = Gauge("onix_budget_ratio", "Ratio consommé du budget")

@app.middleware("http")
async def _metrics_mw(request: Request, call_next):
    import time
    start = time.perf_counter()
    response = await call_next(request)
    path = request.scope.get("route").path if request.scope.get("route") else request.url.path
    REQS.labels(path, request.method, response.status_code).inc()
    LATENCY.labels(path).observe(time.perf_counter() - start)
    return response

@app.get("/metrics")
def metrics() -> Response:
    # Rafraîchit les jauges FinOps depuis usage_tracker/cost_tracker.
    spent = usage_tracker.summary().get("estimated_cost_eur", 0.0)
    budget = cost_tracker.check_budget(spent)
    BUDGET_SPENT.set(spent)
    if budget.get("budget_eur"):
        BUDGET_LIMIT.set(budget["budget_eur"])
        BUDGET_RATIO.set((budget.get("ratio_pct") or 0) / 100.0)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

3. Au point unique qui lève les 403 (kill-switch), le compteur est incrémenté
   avant de lever l'exception :

```python
KILLSWITCH.labels(feature, reason or "unknown").inc()
```

Les noms de métriques ci-dessus correspondent **exactement** aux requêtes des
dashboards et des règles d'alerte — contrat de noms figé côté monitoring.

> Le service expose déjà des signaux observables réutilisables : événements
> `usage_tracker` (dont `budget_warning_triggered`, `service_emergency_stopped`,
> `user_blocked`), `GET /cost` (budget), `GET /usage/summary`, `GET /admin/state`.

---

## 6. Barrières CI/CD (qualité, sécurité, supply-chain)

### CI — [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

Tous les jobs sont **bloquants** (aucun `continue-on-error`) ; à protéger par
*branch protection* (« require status checks to pass »).

| Job | Outil | Vérifie |
|---|---|---|
| `validate` | shell `-n`, `docker compose config`, `yamllint`, **gitleaks** | syntaxe scripts, **tous** les compose (base/PERF/GPU/**monitoring**), YAML, garde `.env` non suivi, **0 secret** |
| `pytest` | pytest | `actions/tests` + `tests/rag` & `access-gateway/tests` **si présents** |
| `bandit` | `bandit -r` | SAST Python (sévérité/confiance ≥ medium) — **0 finding** |
| `pip-audit` | pip-audit `--strict` | CVE des dépendances épinglées — **0 CVE** (cf. note) |
| `trivy` | trivy fs + image | vulnérabilités **CRITICAL/HIGH** (filesystem + image `onix-actions`), SARIF → Code scanning |

> **Mise à niveau supply-chain (WS6) :** `actions/requirements.txt` a été relevé
> aux dernières versions stables (fastapi 0.137.1, starlette 1.3.1,
> python-multipart 0.0.32, pdfplumber 0.11.10, pypdf 6.13.2, Pillow 12.2.0…) pour
> **purger 100 % des CVE connues** (`pip-audit --strict` = 0). Compatibilité
> **validée** : `pytest actions/tests` → 33 passed, 1 skipped. Comme `pip-audit`
> est **bloquant**, il faudra relever ces pins dès qu'un nouveau correctif paraît
> (le job échouera sinon — c'est sa valeur). `actions/app/` n'a **pas** été
> modifié (seul le manifeste de versions l'a été).

> **Résilience inter-workstreams :** `tests/rag` et `access-gateway/tests`
> (fournis par d'autres workstreams) sont exécutés **uniquement s'ils existent**.
> La CI passe au vert dès aujourd'hui avec `actions/tests`, et couvre
> automatiquement les autres suites dès leur fusion.

### CD — [`.github/workflows/cd.yml`](../.github/workflows/cd.yml)

Déclenchement : **`workflow_dispatch`** (option `push`) ou tag `v*`.

1. Build de l'image `onix-actions` (Buildx, cache GHA).
2. **Scan trivy bloquant** (CRITICAL/HIGH) **avant** tout push.
3. Push multi-tags **+ digest** vers **GHCR** (`docker/metadata-action` :
   semver, branche, `sha`, `latest` sur tag) — uniquement si le scan réussit.
4. **SBOM (syft)** SPDX-json **et** CycloneDX-json, archivés en **artefacts**
   (90 j) et générés sur l'image candidate.

### Miroir local — `make test`

`make test` lance **toutes** les barrières en local (lint, compose, pytest,
bandit, pip-audit, gitleaks, trivy), plus `make sbom` (SBOM via syft). Les outils
manquants sont installés à la volée ; `trivy`/`syft` (binaires lourds) sont
ignorés proprement en local s'ils ne sont pas installés (ils tournent en CI).

---

## 7. Pourquoi observabilité + gates passent au vert

- **Observabilité :** la stack est complète et **validée** (`docker compose
  config -q` OK). Dès `make monitor-up`, les dashboards infra/disponibilité et
  les sondes `/health` sont peuplés, et 6 alertes (down/CPU/RAM/disque) sont
  actives **sans dépendance**. Les alertes + panneaux applicatifs basés sur
  `/metrics` (livré) s'allument dès que la stack supervise un `actions` en marche
  (contrat de noms figé).
- **Gates :** CI durcie avec tests + SAST + audit de dépendances + scan
  vulnérabilités (fs & image) + garde `.env` + gitleaks, **tous bloquants** ;
  CD signe la chaîne d'appro (scan-avant-push, image taggée/digestée, SBOM).
  Soutient **Garde-fous** (kill-switch 403 observé + alerté), **Sécurité**
  (gitleaks, bandit, trivy, pip-audit, SARIF), **ALM** (CI/CD reproductible,
  artefacts, SBOM).

---

## 8. Validation effectuée vs exécution réelle requise

**Validé hors cluster (statique) :**
- `docker compose -f monitoring/docker-compose.monitoring.yml config -q` → OK.
- `yamllint` sur workflows + monitoring → OK ; JSON des dashboards bien formés.
- Parsing des workflows et des règles Prometheus.
- `gitleaks` → 0 secret.
- **`make monitor-render`** (`scripts/check-alertmanager-config.py`) → rend le
  gabarit Alertmanager et asserte le `webhook_configs` réel + le refus fail-closed
  sans `ALERT_WEBHOOK_URL`. Le rendu shell de l'entrypoint a été exécuté
  localement (cas URL absente/vide/non-http → refus `exit 1` ; cas URL valide →
  config rendue + lancement) ; le boot conteneur réel reste à confirmer en stack.

**Nécessite une vraie exécution CI / cluster (à confirmer en pipeline réel) :**
- Exécution des actions GitHub (`trivy-action`, `sbom-action`,
  `build-push-action`, upload SARIF) — réseau + runner requis.
- `make monitor-up` réel : scrape des cibles, peuplement Grafana, déclenchement
  d'alertes (y compris celles basées sur `/metrics`, désormais livré), et
  **livraison effective** d'une notification webhook vers `ALERT_WEBHOOK_URL`
  (le rendu fail-closed est testé statiquement ; l'envoi HTTP réel par Alertmanager
  se confirme en stack avec un récepteur joignable).
- Push GHCR (nécessite les permissions `packages: write` du dépôt).
