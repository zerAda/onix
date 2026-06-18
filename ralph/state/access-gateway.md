<!-- Pour clore le scope, écrire `RALPH_DONE` comme TOUTE PREMIÈRE ligne. -->
# État Ralph — access-gateway

## Backlog (source : docs/audit-reality/access-gateway.md)
| ID | Prio | Écart | Axe | Statut |
|---|---|---|---|---|
| G1 | P1 | `explicit_admin_bypass` inerte (`main.py:405` sans `is_admin`) — honnêteté | A1 | ⬜ |
| G2 | P1 | Contradiction fail-loud (CACHE.md §4) vs fail-safe silencieux (`main.py:164-167`) | A1/A3 | ⬜ |
| G3 | P1 | `_READ_ROLES` partiel (`graph_acl.py:70`) → faux refus possible | A1/A5 | ⬜ |
| G4 | P2 | Compteur « 52 tests » → 267 réels (DECISION_RBAC.md §6) | A1 | ⬜ |
| G5 | P2 | « Streaming SSE » trompeur → NDJSON réel | A1 | ⬜ |

## Journal
| Itér. | Date | Item | Correctif | Gates | SHA |
|---|---|---|---|---|---|

## Questions bloquantes
- (aucune)

## Critères de sortie A1–A7
- [ ] A1 - [ ] A2 - [ ] A3 - [ ] A4 - [ ] A5 - [ ] A6 - [ ] A7(n/a)
