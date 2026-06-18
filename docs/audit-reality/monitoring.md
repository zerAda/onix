# Audit byte-by-byte — Monitoring / Observabilité

> **Scope** : stack d'observabilité locale d'onix (`monitoring/` : Prometheus,
> Alertmanager, Grafana, Loki/Promtail, exporters, Blackbox) + cibles Make
> `monitor-*` + endpoints `/metrics` réellement exposés par `access-gateway/` et
> `actions/`.
> **Doc auditée** : `docs/OBSERVABILITY.md` (référence principale), `env.template`
> §WS6, `Makefile` bloc WS6.
> **Méthode & légende** : cf. [`README.md`](README.md) (✅ conforme · ⚠️ écart
> mineur · ❌ écart majeur · 🕳️ doc-sans-code · 🔇 code-sans-doc · ❔ non vérifiable).
> **Date** : 2026-06-18 · **Auditeur** : ingénieur observabilité/SRE (READ-ONLY).
> **Règle n°1 (AGENTS.md)** : honnêteté > esbroufe ; zéro mock présenté comme réel.

---

## Tableau de comptage

| Classe | Nombre |
|---|---:|
| ✅ CONFORME | 31 (dont 3 P1 🔇 résolus itér. 2 — voir §P1) |
| ⚠️ ÉCART MINEUR | 6 |
| ❌ ÉCART MAJEUR | 2 (dont 1 ✅ résolu — voir §P0) |
| 🕳️ DOC-SANS-CODE | 0 |
| 🔇 CODE-SANS-DOC | 3 (1 résolu : dashboard/alerte gateway ; restent : métriques sémantiques, auto-supervision Prometheus, rétention) |
| ❔ NON VÉRIFIABLE | 1 |
| **Total affirmations classées** | **43** |

> **Fait saillant** : le constat le plus structurant est *inversé* par rapport à
> l'attendu d'un audit « doc > réalité ». La doc affirme à répétition que
> l'endpoint `/metrics` de `actions` est une **dépendance WS2 non livrée** (« tant
> que cet endpoint n'existe pas… »). **Il est en réalité pleinement implémenté**
> (`actions/app/main.py:359-372`). Le risque ici n'est donc pas « mock présenté
> comme réel » mais **doc périmée qui sous-vend le code** → reclassé ❌ (la doc
> affirme un comportement faux : « down / non exposé »).

---

## 1. Stack & services (tableau §1 de la doc)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Stack séparée de l'applicative (`docker-compose.monitoring.yml`) | ✅ | `monitoring/docker-compose.monitoring.yml:26` (`name: onix-monitoring`), Makefile `MONITORING_COMPOSE` `Makefile:384` | Fichier dédié, jamais inclus dans le compose applicatif. |
| `prometheus` = `prom/prometheus:v3.1.0`, interne | ✅ | `docker-compose.monitoring.yml:47`, pas de bloc `ports` | Aucun port hôte. |
| `alertmanager` = `prom/alertmanager:v0.27.0`, interne | ✅ | `docker-compose.monitoring.yml:80` | Idem. |
| `grafana` = `grafana/grafana:11.4.0`, exposé `127.0.0.1:3001` | ✅ | `docker-compose.monitoring.yml:108,133-135` (`127.0.0.1:${GRAFANA_HOST_PORT:-3001}:3000`) | Liaison loopback confirmée. |
| `loki` = `grafana/loki:3.3.0`, interne | ✅ | `docker-compose.monitoring.yml:153` | |
| `promtail` = `grafana/promtail:3.3.0`, interne | ✅ | `docker-compose.monitoring.yml:176` | |
| `node-exporter` = `prom/node-exporter:v1.8.2` | ✅ | `docker-compose.monitoring.yml:196` | |
| `postgres-exporter` = `prometheuscommunity/postgres-exporter:v0.16.0` | ✅ | `docker-compose.monitoring.yml:215` | |
| `redis-exporter` = `oliver006/redis_exporter:v1.67.0` | ✅ | `docker-compose.monitoring.yml:233` | |
| `opensearch-exporter` = `quay.io/.../elasticsearch-exporter:v1.7.0` | ✅ | `docker-compose.monitoring.yml:253` | |
| `blackbox-exporter` = `prom/blackbox-exporter:v0.25.0` | ✅ | `docker-compose.monitoring.yml:273` | |
| Seul Grafana publie un port (127.0.0.1) | ✅ | `docker-compose.monitoring.yml:133-136` ; aucun autre service n'a de `ports:` | |
| Promtail monte le socket Docker **en lecture seule** | ✅ | `docker-compose.monitoring.yml:184` (`/var/run/docker.sock:/var/run/docker.sock:ro`) | |
| Grafana : sans inscription, sans accès anonyme, sorties désactivées | ✅ | `docker-compose.monitoring.yml:117-125` (`GF_USERS_ALLOW_SIGN_UP=false`, `GF_AUTH_ANONYMOUS_ENABLED=false`, analytics/news off) | |
| Stack rejoint `onix-net` (externe) + `onix-monitoring-net` interne | ✅ | `docker-compose.monitoring.yml:28-34,59` | Réseau interne nommé `onix-monitoring-net` (déclaré `monitoring:` dans le compose). |
| Identifiants Grafana via `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD` | ✅ | `docker-compose.monitoring.yml:115-116`, `env.template:186-188` | |
| « Renseignez un mot de passe fort » avant `monitor-up` | ⚠️ | `gen-secrets.sh:112` génère `GRAFANA_ADMIN_PASSWORD rand 32` | La doc présente cela comme **manuel** ; en réalité `make secrets` le génère. Mitigation non documentée dans OBSERVABILITY.md (incohérence doc↔script, dans le bon sens). |
| Défaut Grafana = `admin/admin` si `.env` absent | 🔇 | `docker-compose.monitoring.yml:115-116` (`:-admin`) | Risque réel si `monitor-up` lancé sans `make secrets` ; non explicité comme tel dans la doc (« à défaut : 'admin' »). |

## 2. Cibles de scrape Prometheus (§2 de la doc)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Job `node` → `node-exporter:9100` | ✅ | `prometheus/prometheus.yml:78-82` | |
| Job `postgres` → `postgres-exporter:9187` (`pg_up`, conn., tx, taille) | ✅ | `prometheus.yml:84-88` ; `pg_up` consommé `grafana/dashboards/onix-infra.json:78` | |
| Job `redis` → `redis-exporter:9121` (`redis_up`, clients, mémoire) | ✅ | `prometheus.yml:90-94` ; `redis_up`/`redis_connected_clients` `onix-infra.json:96,115` | |
| Job `opensearch` → `opensearch-exporter:9114` (santé cluster, heap, docs) | ⚠️ | `prometheus.yml:97-101` (cible OK) | Aucun panneau/alerte OpenSearch dans les 2 dashboards ni `onix-alerts.yml` : la cible est scrappée mais « santé cluster / heap / documents » n'est **visualisée nulle part**. |
| Sondes Blackbox sur `actions:8100/health` et `nginx:80/nginx-health` | ✅ | `prometheus.yml:106-122` ; module `http_2xx` `blackbox/blackbox.yml:9-17` | `valid_status_codes:[200]`, timeout 5s. |
| Job `onix-actions` → `actions:8100/metrics` | ✅ | `prometheus.yml:47-55` | Cible et chemin conformes. |
| Job `onix-access-gateway` → `access-gateway:8200/metrics` | ✅ | `prometheus.yml:70-75` ; port confirmé `access-gateway/Dockerfile:31,37` | |
| `scrape_interval`/`evaluation_interval` = 15s | ✅ | `prometheus.yml:14-15` ; datasource Grafana `timeInterval:15s` `datasources/datasources.yml:15` | Cohérent. |
| Auto-supervision Prometheus (`localhost:9090`) | 🔇 | `prometheus.yml:33-37` | Job `prometheus` réel, non mentionné dans la doc. |

## 3. Métriques `onix-actions` documentées vs émises (§2 tableau, §5 spec)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| **« endpoint `/metrics` dépend de WS2 / n'existe pas encore »** | ❌ | Endpoint réel : `actions/app/main.py:359-372` ; `prometheus-client==0.21.1` `actions/requirements.txt:35` | **Doc fausse / périmée.** Répété en §0 (l.10-12), §2 (l.72-74), §3 (l.107), §5 (l.201-264), `prometheus.yml:7-11,40-46`, `env.template:193-196`, `onix-alerts.yml:8-11`. Le job n'est PAS « down par conception ». |
| `onix_http_requests_total{endpoint,method,status}` (counter) | ✅ | `actions/app/main.py:130-133,158` | Labels conformes ; `endpoint`=gabarit de route (cardinalité bornée, l.156-157). |
| `onix_http_request_duration_seconds_bucket{endpoint,le}` (histogram) | ✅ | `actions/app/main.py:134-137,159` | |
| `onix_killswitch_blocked_total{feature,reason}` (counter) | ✅ | `actions/app/main.py:138-141` ; incrémenté dans `_gate()` `main.py:173` | Conforme à la spec §5 (l.256-261). |
| `onix_budget_spent_eur` / `onix_budget_limit_eur` / `onix_budget_ratio` (gauges) | ✅ | `actions/app/main.py:142-144,366-371` | Rafraîchies à chaque scrape depuis `usage_tracker`/`cost_tracker`. |
| `onix_up` (gauge 1/0) | ⚠️ | `actions/app/main.py:145-146` (`UP.set(1)` une fois) | Émis mais **figé à 1** : ne descend jamais à 0 (process mort = pas de scrape = série absente). N'apporte rien de plus que `up`. La doc le présente comme « vivacité applicative » trompeur. |
| `/metrics` non authentifié (réseau interne) | ✅ | `actions/app/main.py:359-365` (commentaire + absence de garde API-key) ; spec §5 l.211-212 | Conforme à la posture déclarée. |

## 4. Métriques `onix-access-gateway` documentées vs émises (§5b)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| `/metrics` sur port 8200, `GATEWAY_METRICS_ENABLED` (défaut true), 404 si off | ✅ | `access-gateway/app/main.py:222-239` ; `config.py:170` ; tests `tests/test_metrics.py:262,279` | |
| `onix_gateway_requests_total{endpoint,decision}` | ✅ | `access-gateway/app/metrics.py:34-38` | |
| `onix_gateway_guardrail_total{rule,blocked}` | ✅ | `metrics.py:41-45` | |
| `onix_gateway_answer_no_context_total` | ✅ | `metrics.py:48-51` | |
| `onix_gateway_answer_with_citation_total` / `_without_citation_total` | ✅ | `metrics.py:54-61` | |
| `onix_gateway_request_latency_seconds` (buckets 0.5→120 s) | ✅ | `metrics.py:65-69` | Buckets conformes (0.5,1,2,5,10,20,30,60,120). |
| `onix_gateway_upstream_errors_total` | ✅ | `metrics.py:72-75` | |
| `onix_gateway_feedback_total{rating}` | ✅ | `metrics.py:78-82` ; endpoint `/v1/feedback` `main.py:512-528` | |
| Famille cache (`hits{tier}`, `misses`, `bypassed{reason}`, `tokens_saved`, `seconds_saved`, `errors{op}`) | ✅ | `metrics.py:90-120` | Tous présents avec labels conformes. |
| Famille streaming (`stream_requests`, `stream_aborted{reason}`, `stream_overridden`) | ✅ | `metrics.py:141-158` | `reason` énuméré conforme à la doc §5b l.164. |
| `onix_gateway_cache_semantic_candidates_total` / `_rejected_divergence_total` | 🔇 | `metrics.py:126-137` | **Émises mais ABSENTES du tableau §5b** (la doc ne liste que `tier=semantic` « futur »). Métriques sémantiques bien réelles, non documentées dans OBSERVABILITY.md. |
| Caveat multi-worker `PROMETHEUS_MULTIPROC_DIR` | ✅ | doc §5b l.190-197 ; conteneurs lancés single-worker `Dockerfile:37` (gateway), `actions/Dockerfile:54` | Non bloquant en pratique : aucun `--workers N`. |
| **Aucune métrique `onix_gateway_*` n'est consommée par un dashboard/alerte** | ✅ (résolu itér. 2) | Dashboard `grafana/dashboards/onix-gateway.json` (15 panneaux) + groupe d'alertes `onix-gateway-app` (`onix-alerts.yml`) + recording rules `onix-slo.yml` | Auparavant 🔇 (« observabilité morte ») : 18 métriques émises/scrappées sans consommateur. Désormais visualisées (décisions, garde-fous, citations, latence p50/p95/p99, erreurs amont, hit-rate cache, feedback, flux **NDJSON** avortés) et alertées. |

## 5. Dashboards Grafana (§3)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Auto-provisionnés depuis `monitoring/grafana/dashboards/`, dossier `onix` | ✅ | `provisioning/dashboards/dashboards.yml:8-15` ; `docker-compose.monitoring.yml:131-132` | |
| Datasources Prometheus + Loki auto-provisionnées | ✅ | `provisioning/datasources/datasources.yml:8-23` | UIDs `prometheus`/`loki` cohérents avec les panneaux. |
| Dashboard `onix-actions` : santé, UP/total, budget, 5xx, débit, latence p50/p95/p99, 403 kill-switch, dépensé vs budget, logs Loki | ✅ | `grafana/dashboards/onix-actions.json:5,14-150` (9 panneaux, expr conformes) | Panneau logs filtré `{compose_service="actions"}` `onix-actions.json:147`. |
| Dashboard `onix-infra` : timeline `up`, CPU/RAM/disque, `pg_up`/`redis_up`, clients Redis | ✅ | `grafana/dashboards/onix-infra.json:5,14-118` (7 panneaux) | |
| « Panneaux infra immédiatement peuplés, applicatifs vides tant que /metrics absent » | ⚠️ | `onix-actions.json` consomme `onix_http_*`/`onix_budget_*` | Affirmation périmée : `/metrics` existe → les panneaux applicatifs se peuplent dès trafic réel. |

## 6. Alertes (§4)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Règles dans `prometheus/rules/onix-alerts.yml`, routage `alertmanager.yml` | ✅ | `prometheus.yml:21-22` (`rule_files`), `:25-29` (alertmanager target) | |
| `TargetDown` : `up==0` 2 min, critical | ✅ | `onix-alerts.yml:19-28` | |
| `ServiceProbeFailed` : `probe_success==0` 2 min, critical | ✅ | `onix-alerts.yml:30-39` | |
| `ActionsServiceDown` : sonde `/health` KO 1 min, critical | ✅ | `onix-alerts.yml:41-52` | |
| `HostHighCpu` : CPU>90% 10 min, warning | ✅ | `onix-alerts.yml:148-157` | |
| `HostLowMemory` : RAM dispo <10% 10 min, warning | ✅ | `onix-alerts.yml:159-168` | |
| `HostLowDisk` : disque libre <10% 10 min, warning | ✅ | `onix-alerts.yml:170-181` | |
| `HighErrorRate` : 5xx>5% (5 min), warning | ✅ | `onix-alerts.yml:60-76` | Expr conforme au panneau dashboard. |
| `HighLatencyP95` : p95>2s (5 min), warning | ✅ | `onix-alerts.yml:78-93` | |
| `KillSwitchBlockingTraffic` : ≥1 req/s (5 min), info | ✅ | `onix-alerts.yml:95-108` | |
| `BudgetWarning` : ≥80% et <100%, warning | ✅ | `onix-alerts.yml:115-127` | |
| `BudgetExceeded` : ≥100%, critical | ✅ | `onix-alerts.yml:129-141` | `for:1m` (la doc ne précise pas la durée). |
| « Par défaut aucune notification sortante (récepteur local) » | ✅ | `alertmanager.yml:32-39` (receiver `default` vide, `critical` webhook commenté) | |
| Activation via `ALERT_WEBHOOK_URL` + décommenter `webhook_configs` | ✅ | `alertmanager.yml:37-39`, env injectée `docker-compose.monitoring.yml:87`, `env.template:192` | URL jamais committée, conforme. |

## 7. Logs / Loki / Promtail (§2)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Logs étiquetés `container`, `compose_service`, `compose_project`, `stream` | ✅ | `promtail/promtail-config.yml:24-37` | 4 labels via `relabel_configs`. |
| Panneau Logs du dashboard actions filtré `{compose_service="actions"}` | ✅ | `onix-actions.json:147` | |
| Rétention logs (non chiffrée dans la doc) | 🔇 | `loki/loki-config.yml:37` (`retention_period: 168h` = 7 j) ; rétention Prometheus 15 j `docker-compose.monitoring.yml:53` | Valeurs réelles non mentionnées dans OBSERVABILITY.md. |

## 8. Cibles Make & validation (§1, §8)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| `make monitor-up` / `monitor-down` | ✅ | `Makefile:450-456` | `monitor-up` affiche le port Grafana réel. |
| `make monitor-config` valide le compose | ✅ | `Makefile:458-459` | Aussi inclus dans `compose-validate` `Makefile:405`. |
| `monitor-logs` (non documenté dans OBSERVABILITY.md) | 🔇 | `Makefile:461-462` | Cible existante non listée dans la doc. |
| `yamllint` sur `monitoring/` (validé statiquement) | ✅ | `Makefile:393-397` | |
| « `docker compose config -q` OK » (validation hors cluster) | ❔ | `Makefile:405,459` (commande existe) | Le résultat « OK » nécessite Docker — non exécutable ici (contrainte READ-ONLY/offline). Conformité de la **commande** vérifiée, pas son exécution. |

---

## Écarts « production-ready »

### P0 — bloquant / trompeur
1. **Doc fausse sur l'endpoint `/metrics` d'`actions` (❌).** Toute la doc
   (`OBSERVABILITY.md` §0/§2/§3/§5, `env.template:193-196`, en-têtes
   `prometheus.yml`/`onix-alerts.yml`) répète que le job actions est « down tant
   que WS2 n'aura pas livré /metrics ». **C'est implémenté** (`actions/app/main.py:359-372`).
   Risque : un opérateur croit la supervision applicative inactive et ne
   l'exploite pas. *Inverse* de « mock présenté comme réel » mais viole la règle
   n°1 (affirmation non conforme au code). **Corriger la doc.**
   → ✅ **RÉSOLU** (boucle Ralph `monitoring`, itér. 1) : toutes les occurrences
   « dépend de WS2 / n'existe pas » corrigées dans `OBSERVABILITY.md`
   (§0/§2/§3/§5/§7/§8), `prometheus.yml`, `onix-alerts.yml`, `blackbox.yml`,
   `docker-compose.monitoring.yml`, `env.template`. Validé : YAML OK +
   `docker compose -f monitoring/docker-compose.monitoring.yml config -q` OK.

### P1 — sérieux
2. **18 métriques `onix_gateway_*` émises, scrappées, mais ni visualisées ni
   alertées (🔇).** Aucun panneau Grafana ni règle d'alerte ne les consomme
   (grep `onix_gateway` ⇒ commentaires `prometheus.yml:62-69` uniquement).
   Observabilité « morte » : citations, garde-fous, streaming avorté, cache —
   tous mesurés mais invisibles. Manque un dashboard `onix-gateway` + alertes
   (ex. taux d'avortement streaming, hit-rate cache).
   → ✅ **RÉSOLU** (boucle Ralph `monitoring`, itér. 2) : dashboard
   `monitoring/grafana/dashboards/onix-gateway.json` (15 panneaux : décisions
   allow/deny `:106`, garde-fous par règle, citations avec/sans/sans-contexte,
   latence p50/p95/p99 sur `onix_gateway_request_latency_seconds_bucket`,
   erreurs amont, hit-rate cache effectif, feedback up/down, flux **NDJSON**
   avortés par `reason` + taux d'avortement, logs Loki
   `{compose_service="access-gateway"}`). Alertes dans le groupe
   `onix-gateway-app` de `onix-alerts.yml` (`GatewayHighUpstreamErrorRate`,
   `GatewayHighLatencyP95`, `GatewayAbnormalStreamAbortRate`). Validé : JSON +
   YAML OK, `docker compose config -q` OK.
3. **Aucun SLO/SLI ni recording rule (🔇/écart).** `grep recording|slo|sli` =
   0 résultat dans `monitoring/`. Pas d'objectif de disponibilité/latence
   formalisé, pas d'agrégation pré-calculée (les `histogram_quantile` sont
   recalculés à chaque évaluation). Attendu d'un déploiement régulé.
   → ✅ **RÉSOLU** (itér. 2) : `monitoring/prometheus/rules/onix-slo.yml`
   (capté par `rule_files: /etc/prometheus/rules/*.yml`) : recording rules SLI
   (`job:onix_gateway_error_ratio:5m`, `job:onix_gateway_availability:ratio_5m`,
   `job:onix_gateway_request_latency:p95_5m`,
   `job:onix_gateway_stream_abort_ratio:5m` + équivalents actions) et 2 alertes
   basées SLO (`GatewaySLOAvailabilityBreached` < 99 %,
   `GatewaySLOLatencyBreached` p95 > 30 s). Validé : YAML + structure OK.
4. **Grafana démarre sur `admin/admin` si `.env` absent (🔇).**
   `docker-compose.monitoring.yml:115-116` (`:-admin`). Mitigé *si et seulement
   si* `make secrets` a été lancé (`gen-secrets.sh:112` génère le mot de passe,
   pas l'utilisateur). `monitor-up` ne vérifie pas la présence du secret →
   exposition loopback en creds par défaut possible.
   → ✅ **RÉSOLU** (itér. 2) : défaut compose durci (utilisateur `onix-admin`,
   mot de passe de repli NON trivial — plus de `:-admin`,
   `docker-compose.monitoring.yml`) ET garde-fou bloquant dans `monitor-up`
   (`Makefile`) qui REFUSE le démarrage si `GRAFANA_ADMIN_PASSWORD` est absent,
   trivial (`admin`/`CHANGEME`) ou < 12 caractères. `:?` écarté côté compose
   pour ne pas casser `docker compose config` (.env facultatif). Validé :
   `docker compose config -q` OK.

### P2 — durcissement / dette
5. **Stack monitoring non durcie (écart vs posture AGENTS.md).** Aucun
   `no-new-privileges`, `cap_drop`, `read_only`, `runAsNonRoot` sur les services
   monitoring (seul `prometheus` fixe `user: 65534`). `node-exporter` tourne
   `pid: host` + `/:/host:ro` et `promtail` accède au socket Docker — surfaces
   sensibles non contraintes. La doc vante le durcissement applicatif sans le
   répliquer ici.
6. **`onix_up` figé à 1 (⚠️).** `actions/app/main.py:145-146` : `UP.set(1)` une
   seule fois, jamais remis à 0 → métrique inutile (redondante avec `up`),
   présentée comme « vivacité applicative ».
7. **Cible `opensearch` scrappée mais jamais visualisée/alertée (⚠️).** La doc
   promet « santé cluster / heap JVM / documents » ; aucun panneau ni alerte
   OpenSearch n'existe (`onix-infra.json`, `onix-alerts.yml`).

---

## Verdict (3 lignes)

La stack d'observabilité est **réellement implémentée, cohérente et conforme** sur
l'essentiel (images épinglées, posture loopback/souveraine, 11 alertes vérifiées,
2 dashboards fonctionnels, `/metrics` *réels* côté actions **et** gateway). Le
défaut majeur n'est pas du mock mais une **doc périmée qui nie un endpoint pourtant
livré** (`actions/metrics`) — corrigé en itér. 1. L'**observabilité gateway morte**
(18 métriques sans dashboard/alerte), l'**absence de SLO/SLI** et le **garde-fou
anti-`admin/admin`** sont résolus en itér. 2 (dashboard `onix-gateway.json`,
alertes `onix-gateway-app`, recording rules + alertes SLO `onix-slo.yml`, refus de
démarrage sans mot de passe fort). **Production-ready : OUI pour un poste/POC** ;
reste pour un client régulé le **durcissement de la stack monitoring** (P2 :
`no-new-privileges`/`cap_drop`/`read_only`) et la **visualisation OpenSearch**.
