# Observabilité & gates qualité/sécurité (WS6)

Ce document décrit la **stack d'observabilité** d'onix (Prometheus, Alertmanager,
Grafana, Loki/Promtail, exporters), les **barrières CI/CD** (qualité, sécurité,
supply-chain) et leur **intégration**. Tout est **100 % local / souverain** :
images épinglées, aucune télémétrie sortante, aucun service cloud.

> Périmètre WS6 : `.github/workflows/{ci,cd}.yml`, `monitoring/`,
> `docs/OBSERVABILITY.md`, blocs dédiés dans `Makefile` et `env.template`.
> Le microservice `actions/` n'est **pas** modifié ici : l'endpoint `/metrics`
> requis est **spécifié ci-dessous** pour intégration par WS2.

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
  et `nginx:80/nginx-health` — **disponibilité indépendante** des métriques
  applicatives (fonctionne avant même que `/metrics` existe).

### Métriques applicatives `onix-actions` (dépendent de WS2 — cf. §5)

Une fois l'endpoint `/metrics` exposé, Prometheus collecte :

| Métrique | Type | Sens |
|---|---|---|
| `onix_http_requests_total{endpoint,method,status}` | counter | Débit + ventilation par code HTTP (→ taux d'erreur 5xx) |
| `onix_http_request_duration_seconds_bucket{endpoint,le}` | histogram | Latence (p50/p95/p99) |
| `onix_killswitch_blocked_total{feature,reason}` | counter | **403 du kill-switch** (global/feature/user) |
| `onix_budget_spent_eur` | gauge | Coût estimé cumulé (FinOps) |
| `onix_budget_limit_eur` | gauge | Budget alloué |
| `onix_budget_ratio` | gauge | Ratio consommé (0–1+) → alertes budget |
| `onix_up` | gauge | Vivacité applicative (1/0) |

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
   Redis.

Les panneaux applicatifs restent vides tant que `/metrics` (WS2) n'existe pas ;
les panneaux infra et la timeline de disponibilité sont **immédiatement** peuplés.

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

**Notification :** par défaut **aucune sortie** (récepteur local — souveraineté).
Pour notifier, renseignez `ALERT_WEBHOOK_URL` (Slack/Mattermost/Teams-compatible,
même convention que `ONIX_NOTIFY_WEBHOOK`) et décommentez le `webhook_configs`
dans `alertmanager.yml`. L'URL passe par variable d'environnement — **jamais
committée**.

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

## 5. Dépendance d'intégration : endpoint `/metrics` (à ajouter par WS2)

> **Action requise côté `actions/` (hors périmètre WS6).** Le job Prometheus
> `onix-actions` cible `http://actions:8100/metrics`. Tant que cet endpoint
> n'existe pas, **seul ce job est `down`** ; toute la supervision infra +
> sondes `/health` fonctionne. Les règles `onix_*` restent inertes (aucune
> série) sans casser l'évaluation des autres groupes.

### Spécification proposée

Exposer `GET /metrics` (format texte Prometheus, **non authentifié** car réseau
interne sans port hôte — ou exempté du `require_api_key`). Implémentation
recommandée avec [`prometheus-client`](https://github.com/prometheus/client_python) :

1. Ajouter à `actions/requirements.txt` : `prometheus-client==0.21.1` (épinglé).
2. Instrumenter `actions/app/main.py` :

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

3. Dans `_gate()` (le point unique qui lève les 403), incrémenter le compteur
   avant de lever l'exception :

```python
KILLSWITCH.labels(feature, reason or "unknown").inc()
```

Les noms de métriques ci-dessus correspondent **exactement** aux requêtes des
dashboards et des règles d'alerte — aucun changement côté WS6 ne sera nécessaire.

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
  actives **sans dépendance**. Les 5 alertes + panneaux applicatifs s'allument
  automatiquement dès que WS2 ajoute `/metrics` (contrat de noms déjà figé).
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

**Nécessite une vraie exécution CI / cluster (à confirmer en pipeline réel) :**
- Exécution des actions GitHub (`trivy-action`, `sbom-action`,
  `build-push-action`, upload SARIF) — réseau + runner requis.
- `make monitor-up` réel : scrape des cibles, peuplement Grafana, déclenchement
  d'alertes (notamment celles dépendant de `/metrics` une fois WS2 livré).
- Push GHCR (nécessite les permissions `packages: write` du dépôt).
