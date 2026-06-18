<!-- Pour clore le scope, écrire `RALPH_DONE` comme TOUTE PREMIÈRE ligne. -->
# État Ralph — actions

## Backlog (source : docs/audit-reality/actions.md)
| ID | Prio | Écart | Axe | Statut |
|---|---|---|---|---|
| A1 | **P0** | Secrets WS2 non injectés par le chart Helm → `/admin/*` 403, audit→SHA-256 | A3/A7 | ✅ |
| A2 | **P0** | `ONIX_OBJECT_STORE=s3` non câblé (`configmap.yaml:23-35`) → download casse en HA | A5 | ✅ |
| A3 | **P0** | Erase RGPD S3 incomplet (`objstore.delete_job` jamais appelé) → art.17 | A7 | ✅ |
| A4 | P1 | `openapi.json` périmé (manque endpoints + scheme `X-Admin-Key`) | A1 | ⬜ |
| A5 | P1 | Rate-limit `slowapi` par-process en HA (quota N×réplicas) | A5 | ⬜ |
| A6 | P2 | Compteurs de tests faux (58/71 → 86/90 réels) | A1 | ⬜ |

## Journal
| Itér. | Date | Item | Correctif | Gates | SHA |
|---|---|---|---|---|---|
| 1 | 2026-06-18 | A1 (P0) | Helper `onix.actionsSecretEnv` + secretKeyRef des 3 clés WS2 dans actions.yaml & actions-queue.yaml ; doc secret.yaml/values.yaml ; placeholders CI smoke | `helm lint` OK ; `helm template` default/azure/smoke OK (2 secretKeyRef/clé) | `0fc8893` |
| 1 | 2026-06-18 | A2 (P0) | ConfigMap : `ONIX_OBJECT_STORE`/`ONIX_S3_BUCKET`/`POSTGRES_DB` (+ dbUrl/dbSchema cond.) | `helm lint` OK ; `helm template` rend `ONIX_OBJECT_STORE "s3"` | `b205d31` |
| 1 | 2026-06-18 | A3 (P0) | objstore `delete_subject_docx`/`delete_jobs_older_than` branchés dans `retention.erase_subject`/`purge_by_age` (fail-safe local) + 4 tests (client S3 mocké) | `pytest actions/tests` → 85 passed / 5 skipped | `bc7f9a6` |

## Questions bloquantes
- (aucune) — coordination Helm avec scope `deploy-ops`.

## Notes itération 1 (2026-06-18)
- 3 P0 HA fermés. Gates : `helm lint` vert, `helm template` (default/azure/smoke)
  vert, `pytest actions/tests` 85✅/5⏭. `bandit` actions = 0 medium+ (4 Low B110
  try/except/pass, fail-safe délibéré aligné sur le code voisin, +1 vs baseline).
  `make compose-validate` rouge AVANT et APRÈS (manque `.env` dans cet env, hors
  scope — non touché au compose). `pip-audit`/`trivy`/`gitleaks` non rejoués
  (pas de nouvelle dépendance ; aucun secret réel ajouté).
- Limite assumée (effacement S3 art. 17) : rapprochement sujet→.docx par NOM de
  fichier sanitisé (best-effort, identique local & S3). Pour un effacement
  exhaustif au-delà du nom, prévoir un index sujet→jobs (backlog futur, documenté).
- RESTE pour `RALPH_DONE` : A4 (openapi.json à régénérer + scheme X-Admin-Key),
  A5 (rate-limit Redis OU documenter la limite N×réplicas), A6 (réconcilier les
  compteurs de tests dans la doc : 86/90 réels). Donc A1 (audit doc↔code) PAS
  encore 0 écart, A5 (fiabilité) PAS encore couvert → scope NON clos.

## Critères de sortie A1–A7
- [ ] A1 (reste openapi.json P1 + compteurs tests P2) - [x] A2 - [x] A3
- [ ] A4 - [ ] A5 (rate-limit HA P1) - [ ] A6 - [x] A7 (erase S3 art.17)
