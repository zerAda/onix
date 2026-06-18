<!-- Pour clore le scope, écrire `RALPH_DONE` comme TOUTE PREMIÈRE ligne. -->
# État Ralph — deploy-ops

## Backlog (source : docs/audit-reality/deploy-ops.md)
| ID | Prio | Écart | Axe | Statut |
|---|---|---|---|---|
| D1 | P1 | Ingress Azure chat→gateway + anti-spoofing non templatisé (`templates/ingress.yaml`) | A3/A6 | ⬜ |
| D2 | P1 | TLS Redis/PG Onyx non livrés (`values-azure.yaml`, `configmap.yaml:13-16`) | A3 | ⬜ |
| D3 | P1 | `scripts/backup.sh` ignore la surcouche prod (`-f deploy/prod/...`) | A5/A6 | ⬜ |
| D4 | P2 | Durcissement Helm partiel (non-root/seccomp seulement gateway) | A5 | ⬜ |
| D5 | P2 | RUNBOOK §7 : `inference_` vs `indexing_model_server` | A1 | ⬜ |

## Journal
| Itér. | Date | Item | Correctif | Gates | SHA |
|---|---|---|---|---|---|

## Questions bloquantes
- (aucune) — coordination Helm avec `actions`/`security-governance`.

## Critères de sortie A1–A7
- [ ] A1 - [ ] A2 - [ ] A3 - [ ] A4 - [ ] A5 - [ ] A6 - [ ] A7(n/a)
