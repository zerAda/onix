<!-- Pour clore le scope, écrire `RALPH_DONE` comme TOUTE PREMIÈRE ligne. -->
# État Ralph — actions

## Backlog (source : docs/audit-reality/actions.md)
| ID | Prio | Écart | Axe | Statut |
|---|---|---|---|---|
| A1 | **P0** | Secrets WS2 non injectés par le chart Helm → `/admin/*` 403, audit→SHA-256 | A3/A7 | ⬜ |
| A2 | **P0** | `ONIX_OBJECT_STORE=s3` non câblé (`configmap.yaml:23-35`) → download casse en HA | A5 | ⬜ |
| A3 | **P0** | Erase RGPD S3 incomplet (`objstore.delete_job` jamais appelé) → art.17 | A7 | ⬜ |
| A4 | P1 | `openapi.json` périmé (manque endpoints + scheme `X-Admin-Key`) | A1 | ⬜ |
| A5 | P1 | Rate-limit `slowapi` par-process en HA (quota N×réplicas) | A5 | ⬜ |
| A6 | P2 | Compteurs de tests faux (58/71 → 86 réels) | A1 | ⬜ |

## Journal
| Itér. | Date | Item | Correctif | Gates | SHA |
|---|---|---|---|---|---|

## Questions bloquantes
- (aucune) — coordination Helm avec scope `deploy-ops`.

## Critères de sortie A1–A7
- [ ] A1 - [ ] A2 - [ ] A3 - [ ] A4 - [ ] A5 - [ ] A6 - [ ] A7
