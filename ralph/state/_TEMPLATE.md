<!-- Pour clore le scope, écrire `RALPH_DONE` comme TOUTE PREMIÈRE ligne de ce fichier. -->
# État Ralph — <scope>

> Journal d'itérations relu par l'agent au début de **chaque** passe (anti-redite).
> Mets-le à jour à l'ÉTAPE 5 de chaque itération (cf. `ralph/ORCHESTRATION.md` §1).

## Backlog (issu de `docs/audit-reality/<scope>.md`)
| ID | Priorité | Écart (résumé) | Axe DoD | Statut |
|---|---|---|---|---|
| _ex_ G1 | P0 | … | A3 Sécurité | ⬜ à faire / 🔄 en cours / ✅ fait |

## Journal
| Itér. | Date | Item traité | Correctif | Gates | Commit SHA |
|---|---|---|---|---|---|
| 1 | | | | vert/rouge | |

## Questions bloquantes (STOP — à arbitrer par un humain)
- (aucune)

## Critères de sortie (A1–A7) — coche quand prouvé
- [ ] A1 Exactitude doc↔code (0 ❌/🕳️)
- [ ] A2 Tests (chemins critiques + cas limites)
- [ ] A3 Sécurité (bandit/gitleaks/pip-audit/trivy verts, fail-closed)
- [ ] A4 Observabilité (métriques/logs/alertes/runbook)
- [ ] A5 Fiabilité (timeouts/retries/limites/non-root)
- [ ] A6 Reproductibilité (commandes/IaC valides)
- [ ] A7 RGPD/gouvernance (si applicable)
