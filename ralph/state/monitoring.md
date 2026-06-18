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

## Journal
| Itér. | Date | Item | Correctif | Gates | SHA |
|---|---|---|---|---|---|
| 1 | 2026-06-18 | M1 (P0 doc-faux) | Corrigé les claims « /metrics dépend de WS2 / n'existe pas » dans 6 fichiers (OBSERVABILITY.md, prometheus.yml, onix-alerts.yml, blackbox.yml, compose monitoring, env.template) | vert (YAML+compose config) | (ce commit) |
| 2 | 2026-06-18 | M2+M3+M4 (P1 obs. gateway + SLO + garde-fou) | Dashboard `onix-gateway.json` (15 panneaux, flux NDJSON) ; groupe d'alertes `onix-gateway-app` (erreurs amont, p95, avortement NDJSON) ; `onix-slo.yml` (recording rules SLI + 2 alertes SLO) ; durcissement défaut Grafana + garde-fou bloquant `monitor-up` (refus sans mdp fort) ; correction SSE→NDJSON OBSERVABILITY.md §5b | vert (JSON OK, YAML OK, structure rules OK, `compose config -q` OK, yamllint `monitoring/` OK ; promtool absent → ignoré) | (cf. SHAs commits locaux) |
| 3 | 2026-06-18 | M5+M6 (P2 durcissement + viz OpenSearch) | Durcissement stack : `no-new-privileges`+`cap_drop:[ALL]` sur les 10 services ; `read_only`+`tmpfs:/tmp` sur promtail+4 exporters (prudence : prometheus/alertmanager/grafana/loki/node-exporter NON read_only) ; 3 panneaux OpenSearch (santé/heap/docs) `onix-infra.json` + groupe d'alertes `onix-opensearch` (RED, exporter down, heap>90%) `onix-alerts.yml` ; limite `onix_up` figé documentée `OBSERVABILITY.md` §2 (préférer `up`) | vert (compose config -q OK, JSON OK, YAML OK, yamllint OK ; promtool absent → ignoré) | (cf. SHAs commits locaux) |

## Questions bloquantes
- (aucune) — coordination `docker-compose.monitoring.yml` partagé : modifié
  uniquement le bloc `grafana` (défaut creds) ; pas de collision avec d'autres
  scopes. `Makefile` : seul le bloc `monitor-up` touché (autorisé).

## Reste
- (rétention Loki/Prometheus à documenter dans OBSERVABILITY.md — dette mineure
  non bloquante 🔇 ; correctif **code** `onix_up` = côté `actions/`, hors scope).

## Critères de sortie A1–A7
- [x] A1 (écarts ❌/🕳️ = 0 ; ⚠️ résiduels documentés ou hors-scope) - [ ] A2(n/a) - [x] A3 (garde-fou creds) - [x] A4 (dashboards+alertes+SLO gateway + OpenSearch) - [x] A5 (durcissement stack : no-new-privileges/cap_drop/read_only) - [ ] A6 (gates ops verts ; promtool absent localement) - [ ] A7(n/a)
