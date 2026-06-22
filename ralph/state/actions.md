<!-- Pour clore le scope, écrire `RALPH_DONE` comme TOUTE PREMIÈRE ligne. -->
# État Ralph — actions

## Backlog (source : docs/audit-reality/actions.md)
| ID | Prio | Écart | Axe | Statut |
|---|---|---|---|---|
| A1 | **P0** | Secrets WS2 non injectés par le chart Helm → `/admin/*` 403, audit→SHA-256 | A3/A7 | ✅ |
| A2 | **P0** | `ONIX_OBJECT_STORE=s3` non câblé (`configmap.yaml:23-35`) → download casse en HA | A5 | ✅ |
| A3 | **P0** | Erase RGPD S3 incomplet (`objstore.delete_job` jamais appelé) → art.17 | A7 | ✅ |
| A4 | P1 | `openapi.json` périmé (manque endpoints + scheme `X-Admin-Key`) | A1 | ✅ |
| A5 | P1 | Rate-limit `slowapi` par-process en HA (quota N×réplicas) | A5 | ✅ |
| A6 | P2 | Compteurs de tests faux (58/71 → 90 collectés / 85+5) | A1 | ✅ |
| A7 | P2 | `/metrics` (🔇) non décrit dans ACTIONS.md ; vars WS2 non listées au bloc `environment:` compose | A1/A4 | ⬜ |
| M1 | **P0** | `verify_chain()` honorait l'algo stocké par ligne → downgrade HMAC→keyless silencieux (attaquant écrit `algo='sha256'`, recalcule sans la clé) | A3 | ✅ |

## Journal
| Itér. | Date | Item | Correctif | Gates | SHA |
|---|---|---|---|---|---|
| 1 | 2026-06-18 | A1 (P0) | Helper `onix.actionsSecretEnv` + secretKeyRef des 3 clés WS2 dans actions.yaml & actions-queue.yaml ; doc secret.yaml/values.yaml ; placeholders CI smoke | `helm lint` OK ; `helm template` default/azure/smoke OK (2 secretKeyRef/clé) | `0fc8893` |
| 1 | 2026-06-18 | A2 (P0) | ConfigMap : `ONIX_OBJECT_STORE`/`ONIX_S3_BUCKET`/`POSTGRES_DB` (+ dbUrl/dbSchema cond.) | `helm lint` OK ; `helm template` rend `ONIX_OBJECT_STORE "s3"` | `b205d31` |
| 1 | 2026-06-18 | A3 (P0) | objstore `delete_subject_docx`/`delete_jobs_older_than` branchés dans `retention.erase_subject`/`purge_by_age` (fail-safe local) + 4 tests (client S3 mocké) | `pytest actions/tests` → 85 passed / 5 skipped | `bc7f9a6` |
| 2 | 2026-06-18 | A4 (P1) | `openapi.json` régénéré depuis `app.openapi()` : 20 paths (vs 13) ; endpoints WS2/stateless présents ; constat honnête = aucun `securityScheme` (X-Admin-Key décrit comme paramètre d'en-tête sur `/admin/*`) | JSON valide (`json.load` OK) ; pytest 85✅/5⏭ inchangé | `13c45d4` |
| 2 | 2026-06-18 | A5 (P1) | Limite HA rate-limit documentée (encadré ⚠️ SECURITY_RGPD §3.5 : quota effectif `RATE_LIMIT × réplicas`) ; aucun Redis ajouté ce tour | doc seule (pas de code) | `c2a0357` |
| 2 | 2026-06-18 | A6 (P2) | Compteurs de tests réconciliés : SECURITY_RGPD §11 (58→90/85/5) + STATELESS §6.1 (71/4→90/85/5) | `--collect-only` = 90 ; run = 85✅/5⏭ | `68e2cc1` |
| 3 | 2026-06-22 | M1 (P0) | `verify_chain()` fail-closed anti-downgrade : algo imposé par présence de clé (clé⇒hmac-sha256 strict ; ligne `sha256`⇒rupture « downgrade »), l'algo stocké par ligne ne pilote plus la vérif ; nouveau `tests/test_audit_log.py` (4 tests : downgrade détecté + non-régression hmac/keyless + clé disparue) ; `test_security_rgpd.py` chaîne mixte ré-aligné sur la politique (downgrade signalé) | TDD (échec→fix) ; `pytest actions/tests` 90✅/5⏭ ; `bandit` audit_log = 0 issue | (worktree) |

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
- RESTE pour `RALPH_DONE` : A4 (openapi.json), A5 (rate-limit HA), A6 (compteurs
  de tests) FERMÉS cette itération. **Encore ouvert (P2)** : le 🔇 `/metrics` non
  décrit dans `docs/ACTIONS.md` (présent dans openapi.json régénéré, mais pas dans
  la prose ACTIONS.md) + vars WS2 non listées au bloc `environment:` du compose.
  Tant qu'un 🔇 subsiste, A1 (« chaque 🔇 documenté ou supprimé ») n'est pas à 0
  → scope NON clos. PAS de `RALPH_DONE`.

## Notes itération 2 (2026-06-18)
- A4 : venv `/tmp/venv-act2` (requirements + pytest). OpenAPI régénéré via
  `cd actions && python -c "from app.main import app; app.openapi()"` (import `app.*`
  comme les tests, `actions/` racine — PAS `actions.app.*` car pas de `actions/__init__.py`).
  20 paths (vs 13). **Constat honnête** : `components.securitySchemes` = `{}` (l'app
  n'en déclare AUCUN) ; l'ancien `ApiKeyAuth` était écrit à la main → supprimé.
  `X-Admin-Key` apparaît bien (10 occurrences) comme **paramètre d'en-tête** sur les
  seules routes `/admin/*` (`/admin/control`, `/admin/state`, `/admin/audit/verify`,
  `/admin/retention/purge|erase`). Donc on NE l'ajoute pas comme scheme à la main.
- A5 : aucun code touché (doc seule). slowapi reste import-only diag (`security.py:34-40`),
  enforcement = fenêtre mémoire (`security.py:254-282`). Pas de Redis ce tour (consigne).
- A6 : `--collect-only` = 90 ; run offline minimal = 85 passed, 5 skipped (4 PG/S3
  + 1 aiosmtpd). test_security_rgpd.py = 32. ACTIONS.md/FINOPS.md n'ont aucun compteur.
- Gates : `pytest actions/tests -q` → 85✅/5⏭ ; `json.load(actions/openapi.json)` → OK.
  Aucune dépendance ni secret ajoutés.

## Critères de sortie A1–A7
- [ ] A1 (reste 🔇 /metrics non décrit dans ACTIONS.md + vars compose, P2) - [x] A2 - [x] A3
- [x] A4 - [x] A5 (limite HA documentée) - [x] A6 - [x] A7 (erase S3 art.17)
