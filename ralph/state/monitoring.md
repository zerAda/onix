<!-- Pour clore le scope, écrire `RALPH_DONE` comme TOUTE PREMIÈRE ligne. -->
# État Ralph — monitoring

## Backlog (source : docs/audit-reality/monitoring.md)
| ID | Prio | Écart | Axe | Statut |
|---|---|---|---|---|
| M1 | **P0** | Doc FAUSSE : `/metrics` actions « n'existe pas » alors qu'implémenté (`main.py:359-372`) | A1 | ✅ |
| M2 | P1 | 18 métriques `onix_gateway_*` émises mais aucun dashboard/alerte | A4 | ✅ |
| M3 | P1 | Aucun SLO/SLI ni recording rule | A4 | ✅ |
| M4 | P1 | Grafana `admin/admin` si `.env` absent (garde-fou `monitor-up`) | A3 | ✅ |
| M5 | P2 | Stack non durcie (`no-new-privileges`/`cap_drop`/`read_only`), `onix_up` figé | A5 | ✅ |
| M6 | P2 | Cible `opensearch` scrappée mais jamais visualisée/alertée | A4 | ✅ |
| M7 | **P0** | Alertes livrées DANS LE VIDE (`default` vide + `critical` webhook commenté) ; doc « conforme » mensongère ; `${ALERT_WEBHOOK_URL}` jamais expansé | A1/A3 | ✅ |

## Journal
| Itér. | Date | Item | Correctif | Gates | SHA |
|---|---|---|---|---|---|
| 1 | 2026-06-18 | M1 (P0 doc-faux) | Corrigé les claims « /metrics dépend de WS2 / n'existe pas » dans 6 fichiers (OBSERVABILITY.md, prometheus.yml, onix-alerts.yml, blackbox.yml, compose monitoring, env.template) | vert (YAML+compose config) | (ce commit) |
| 2 | 2026-06-18 | M2+M3+M4 (P1 obs. gateway + SLO + garde-fou) | Dashboard `onix-gateway.json` (15 panneaux, flux NDJSON) ; groupe d'alertes `onix-gateway-app` (erreurs amont, p95, avortement NDJSON) ; `onix-slo.yml` (recording rules SLI + 2 alertes SLO) ; durcissement défaut Grafana + garde-fou bloquant `monitor-up` (refus sans mdp fort) ; correction SSE→NDJSON OBSERVABILITY.md §5b | vert (JSON OK, YAML OK, structure rules OK, `compose config -q` OK, yamllint `monitoring/` OK ; promtool absent → ignoré) | (cf. SHAs commits locaux) |
| 3 | 2026-06-18 | M5+M6 (P2 durcissement + viz OpenSearch) | Durcissement stack : `no-new-privileges`+`cap_drop:[ALL]` sur les 10 services ; `read_only`+`tmpfs:/tmp` sur promtail+4 exporters (prudence : prometheus/alertmanager/grafana/loki/node-exporter NON read_only) ; 3 panneaux OpenSearch (santé/heap/docs) `onix-infra.json` + groupe d'alertes `onix-opensearch` (RED, exporter down, heap>90%) `onix-alerts.yml` ; limite `onix_up` figé documentée `OBSERVABILITY.md` §2 (préférer `up`) | vert (compose config -q OK, JSON OK, YAML OK, yamllint OK ; promtool absent → ignoré) | (cf. SHAs commits locaux) |
| 4 | 2026-06-22 | **M7 (P0 alertes dans le vide)** | Gabarit `alertmanager.yml.tmpl` (webhook RÉEL `${ALERT_WEBHOOK_URL}` + `send_resolved`) + `entrypoint.sh` (rendu `sed` au boot + **FAIL-CLOSED** : refus `exit 1` si URL absente/vide/non-http) ; compose monte gabarit+entrypoint, `tmpfs:/tmp`, ancien `alertmanager.yml` statique SUPPRIMÉ ; `make monitor-up` refuse sans `ALERT_WEBHOOK_URL` ; cible `monitor-render` + test `scripts/check-alertmanager-config.py` (incl. `make test`) ; doc « conforme » mensongère corrigée (`OBSERVABILITY.md` §4/§8, `env.template`, scope docs) | vert : `make monitor-render` OK ; entrypoint exécuté localement (4 cas : refus×3, rendu OK) ; compose+gabarit rendu parsés pyyaml OK. Docker/yamllint absents host (CI Linux). Boot conteneur + envoi HTTP réel = runtime | (ce commit) |
| 5 | 2026-06-25 | M7 (couverture tests) | `scripts/check-alertmanager-config.py` (contrôle fail-closed M4/M7) n'avait **aucun test unitaire**. Ajout `scripts/tests/test_alertmanager_config.py` (8 tests, importlib pour nom à tirets) : `_render` **refuse** URL absente/vide/whitespace/non-url/`ftp://` (fail-closed) et substitue une URL http(s) valide ; `_assert_routes_have_webhook` **détecte un receiver VIDE** (alertes dans le vide) et accepte un webhook réel. | `pytest scripts/tests/test_alertmanager_config.py` **8✅** (.venv-gw) ; bandit 0. (Suite scripts complète : 12 échecs **pré-existants** = WSL absent host, tests bash/docker — hors mon ajout, OK en CI Linux.) | (branche prod) |
| 6 | 2026-06-25 | ALERT-RULES (qualité/routage) | Aucun test ne vérifiait la QUALITÉ des règles d'alerte Prometheus (`onix-alerts.yml`, `onix-slo.yml`). Nouveau `scripts/tests/test_alert_rules.py` (pyyaml) : chaque **alerte** doit avoir un `severity` routable (info/warning/critical — sinon elle part dans le vide, personne notifié) + `summary`/`description` (sinon notification inactionnable) + `expr` ; les **recording rules** (`record:`) validées à part. 25 règles réelles validées, qualité verrouillée pour toute règle future. | `pytest scripts/tests/test_alert_rules.py` **26✅** (.venv-gw) ; bandit 0 | (branche prod) |
| 7 | 2026-06-25 | GRAFANA-DASHBOARDS (provisioning) | Aucun test ne validait les dashboards Grafana provisionnés. Nouveau `scripts/tests/test_grafana_dashboards.py` (stdlib json + pyyaml) : chaque dashboard est un JSON valide avec `uid`/`title`/`panels` ; les `uid` sont **UNIQUES** entre dashboards (un uid dupliqué = conflit de provisioning, un dashboard en écrase un autre EN SILENCE) ; chaque panel référence un datasource **provisionné** (prometheus/loki/built-in, sinon panel vide). 3 dashboards réels validés. | `pytest scripts/tests/test_grafana_dashboards.py` **6✅** (.venv-gw) ; bandit 0 | (branche prod) |
| 8 | 2026-06-25 | PROMETHEUS-CONFIG (boucle observabilité) | Mes règles d'alerte validées (it.6) ne servent QUE si Prometheus les CHARGE et ROUTE. Nouveau `scripts/tests/test_prometheus_config.py` (pyyaml) sur `prometheus.yml` réel : `rule_files` présent + dossier de règles non vide (alertes chargées) ; `alerting.alertmanagers` avec cible (alertes routées — complément M7) ; chaque scrape_config a un `job_name` UNIQUE + une cible ; jobs `onix-actions`/`onix-access-gateway` présents (sources des métriques `onix_*`). | `pytest scripts/tests/test_prometheus_config.py` **4✅** (.venv-gw) ; bandit 0 | (branche prod) |

## Questions bloquantes
- (aucune) — coordination `docker-compose.monitoring.yml` partagé : modifié
  uniquement le bloc `grafana` (défaut creds) ; pas de collision avec d'autres
  scopes. `Makefile` : seul le bloc `monitor-up` touché (autorisé).

## Reste
- (rétention Loki/Prometheus à documenter dans OBSERVABILITY.md — dette mineure
  non bloquante 🔇 ; correctif **code** `onix_up` = côté `actions/`, hors scope).

## Critères de sortie A1–A7
- [x] A1 (écarts ❌/🕳️ = 0 ; ⚠️ résiduels documentés ou hors-scope) - [ ] A2(n/a) - [x] A3 (garde-fou creds) - [x] A4 (dashboards+alertes+SLO gateway + OpenSearch) - [x] A5 (durcissement stack : no-new-privileges/cap_drop/read_only) - [ ] A6 (gates ops verts ; promtool absent localement) - [ ] A7(n/a)
