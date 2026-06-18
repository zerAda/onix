<!-- Pour clore le scope, écrire `RALPH_DONE` comme TOUTE PREMIÈRE ligne. -->
# État Ralph — monitoring

## Backlog (source : docs/audit-reality/monitoring.md)
| ID | Prio | Écart | Axe | Statut |
|---|---|---|---|---|
| M1 | **P0** | Doc FAUSSE : `/metrics` actions « n'existe pas » alors qu'implémenté (`main.py:359-372`) | A1 | ✅ |
| M2 | P1 | 18 métriques `onix_gateway_*` émises mais aucun dashboard/alerte | A4 | ✅ |
| M3 | P1 | Aucun SLO/SLI ni recording rule | A4 | ✅ |
| M4 | P1 | Grafana `admin/admin` si `.env` absent (garde-fou `monitor-up`) | A3 | ✅ |
| M5 | P2 | Stack non durcie (`no-new-privileges`/`cap_drop`/`read_only`), `onix_up` figé | A5 | ⬜ |

## Journal
| Itér. | Date | Item | Correctif | Gates | SHA |
|---|---|---|---|---|---|
| 1 | 2026-06-18 | M1 (P0 doc-faux) | Corrigé les claims « /metrics dépend de WS2 / n'existe pas » dans 6 fichiers (OBSERVABILITY.md, prometheus.yml, onix-alerts.yml, blackbox.yml, compose monitoring, env.template) | vert (YAML+compose config) | (ce commit) |
| 2 | 2026-06-18 | M2+M3+M4 (P1 obs. gateway + SLO + garde-fou) | Dashboard `onix-gateway.json` (15 panneaux, flux NDJSON) ; groupe d'alertes `onix-gateway-app` (erreurs amont, p95, avortement NDJSON) ; `onix-slo.yml` (recording rules SLI + 2 alertes SLO) ; durcissement défaut Grafana + garde-fou bloquant `monitor-up` (refus sans mdp fort) ; correction SSE→NDJSON OBSERVABILITY.md §5b | vert (JSON OK, YAML OK, structure rules OK, `compose config -q` OK, yamllint `monitoring/` OK ; promtool absent → ignoré) | (cf. SHAs commits locaux) |

## Questions bloquantes
- (aucune) — coordination `docker-compose.monitoring.yml` partagé : modifié
  uniquement le bloc `grafana` (défaut creds) ; pas de collision avec d'autres
  scopes. `Makefile` : seul le bloc `monitor-up` touché (autorisé).

## Reste
- M5 (P2) : durcir la stack monitoring (`no-new-privileges`/`cap_drop`/`read_only`),
  `onix_up` figé (côté actions = hors propriété de ce scope, à signaler).
- P2 résiduels : visualisation/alerte OpenSearch ; rétention Loki/Prometheus à
  documenter dans OBSERVABILITY.md.

## Critères de sortie A1–A7
- [ ] A1 (P2 résiduels) - [ ] A2(n/a) - [x] A3 (garde-fou creds) - [x] A4 (dashboard+alertes+SLO gateway ; reste OpenSearch) - [ ] A5 (durcissement stack) - [ ] A6 - [ ] A7(n/a)
