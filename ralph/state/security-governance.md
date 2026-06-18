<!-- Pour clore le scope, écrire `RALPH_DONE` comme TOUTE PREMIÈRE ligne. -->
# État Ralph — security-governance

## Backlog (source : docs/audit-reality/security-governance.md)
| ID | Prio | Écart | Axe | Statut |
|---|---|---|---|---|
| S1 | P1 | `ENCRYPTION_KEY_SECRET` jamais posé (0 dans compose/values/templates) | A3 | ⬜ |
| S2 | P1 | Hook gitleaks pre-commit annoncé mais inexistant (`docs/SECURITY.md:67`) | A3/A6 | ⬜ |
| S3 | P1 | `docs/RGPD.md` périmé sous-vend la conformité (effacement/rétention) | A1/A7 | ⬜ |
| S4 | P1 | `securityContext` absent du Deployment Helm `actions` (PSS restricted) | A3/A5 | ⬜ |
| S5 | P2 | `audit-onyx/*` non re-vérifiable (Onyx non vendoré) → avertissement provenance | A1 | ⬜ |
| S6 | P2 | DPIA & registre = templates non remplis ; base légale absente | A7 | ⬜ |

## Journal
| Itér. | Date | Item | Correctif | Gates | SHA |
|---|---|---|---|---|---|

## Questions bloquantes
- (aucune) — coordination Helm avec `deploy-ops`/`actions`.

## Critères de sortie A1–A7
- [ ] A1 - [ ] A2 - [ ] A3 - [ ] A4 - [ ] A5 - [ ] A6 - [ ] A7
