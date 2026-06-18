<!-- Pour clore le scope, écrire `RALPH_DONE` comme TOUTE PREMIÈRE ligne. -->
# État Ralph — monitoring

## Backlog (source : docs/audit-reality/monitoring.md)
| ID | Prio | Écart | Axe | Statut |
|---|---|---|---|---|
| M1 | **P0** | Doc FAUSSE : `/metrics` actions « n'existe pas » alors qu'implémenté (`main.py:359-372`) | A1 | ✅ |
| M2 | P1 | 18 métriques `onix_gateway_*` émises mais aucun dashboard/alerte | A4 | ⬜ |
| M3 | P1 | Aucun SLO/SLI ni recording rule | A4 | ⬜ |
| M4 | P1 | Grafana `admin/admin` si `.env` absent (garde-fou `monitor-up`) | A3 | ⬜ |
| M5 | P2 | Stack non durcie (`no-new-privileges`/`cap_drop`/`read_only`), `onix_up` figé | A5 | ⬜ |

## Journal
| Itér. | Date | Item | Correctif | Gates | SHA |
|---|---|---|---|---|---|
| 1 | 2026-06-18 | M1 (P0 doc-faux) | Corrigé les claims « /metrics dépend de WS2 / n'existe pas » dans 6 fichiers (OBSERVABILITY.md, prometheus.yml, onix-alerts.yml, blackbox.yml, compose monitoring, env.template) | vert (YAML+compose config) | (ce commit) |

## Questions bloquantes
- (aucune) — coordination `docker-compose.monitoring.yml` partagé.

## Critères de sortie A1–A7
- [ ] A1 - [ ] A2(n/a) - [ ] A3 - [ ] A4 - [ ] A5 - [ ] A6 - [ ] A7(n/a)
