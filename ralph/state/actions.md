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
| HARD-03 | **P0** | Sans clé HMAC d'audit, le service démarrait quand même → journal keyless **forgeable** présenté comme inviolable | A3 | ✅ |

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
| 4 | 2026-06-22 | HARD-03 (P0) | Préflight fail-closed `preflight_audit_key()` appelé dans `main.py:_lifespan` : refus de démarrer sans `ONIX_ACTIONS_AUDIT_HMAC_KEY` (override DEV `ONIX_ACTIONS_AUDIT_KEY_OPTIONAL=true`) ; fixture autouse conftest pour les tests + 2 tests dédiés (refus sans clé / OK avec clé) | TDD ; `pytest actions/tests` **92✅/5⏭** ; `bandit` 0 | (branche prod) |
| 5 | 2026-06-22 | POC-RECONCILIATION | **Réconciliation contrat ↔ SI Fabric** (parité AC360) : `app/fabric_reference.py` (`fetch_client_reference` lit la référence client dans le SI Fabric OneLake, fail-closed, lecteur injectable, HTTPS-only) + endpoint `POST /audit/reconcile/file` (OCR doc → champs → réf Fabric par `client_key` → `audit_engine.audit` → verdict CONFORME/ECART/INCERTAIN/CLIENT_NON_TROUVE). Le moteur de compa (`audit_engine`) existait déjà ; seul le maillon « référence depuis le SI » manquait. **+ champ `cotisation_annuelle`** (amount) ajouté à `audit_engine` (`_FIELD_ALIASES`+`_AUDIT_FIELDS`) et à `fabric_reference.REFERENCE_FIELDS` → capte un écart de **cotisation** contrat↔SI (cas métier GEREP : contrat 12 500 €/an vs SI 13 000 € ⇒ ECART). **Auth OneLake** : `_storage_token()` mint un jeton storage via **service principal** (`ONIX_FABRIC_SP_*`, client_credentials, auto-rafraîchi + cache) ou jeton statique `ONIX_FABRIC_TOKEN` — secrets par env, jamais en repo. **+ robustesse extraction** : `extract_canonical_fields` préfère l'étiquette exacte/courte (« Client » bat la phrase parasite « …Assistant Client 360 ») et alias « numero dossier » → sur le vrai contrat POC, verdict **ECART** (cotisation) au lieu de CLIENT_NON_TROUVE. | TDD `tests/test_fabric_reference.py` (12 tests) ; `pytest actions/tests` **104✅/5⏭** ; bandit 0 ; **prouvé LIVE** (contrat SharePoint OCR → réf OneLake via SP → verdict) | (branche prod) |
| 6 | 2026-06-23 | RECON-GARANTIE (consolidation) | **+ champ `garantie`** (risque couvert) ajouté à `audit_engine` (`_FIELD_ALIASES` : garantie/risque couvert/couverture… + `_AUDIT_FIELDS` comparaison `name`/fuzzy) et à `fabric_reference.REFERENCE_FIELDS` → la réconciliation vérifie aussi la **cohérence de couverture** contrat↔SI (ex. contrat « Prévoyance collective » vs SI « Santé collective » ⇒ ECART). Valeur métier : un dossier dont la garantie diverge du SI est signalé. | TDD `tests/test_fabric_reference.py` (+2 tests garantie : alias, MATCH/MISMATCH→ECART, projection SI) ; `pytest actions/tests` **106✅/5⏭** ; bandit 0 | (branche prod) |
| 7 | 2026-06-23 | RECON-FICHE-REVUE (consolidation) | **+ `build_review_fiche(audit_result)`** (`audit_engine`, pure, fail-safe) : synthétise une **fiche de revue humaine** (verdict + liste des écarts contrat-vs-SI + `a_revoir` + recommandation) sur les verdicts ECART/INCERTAIN/CLIENT_NON_TROUVE (parité AC360 `_FIC_VERDICTS`). Câblée dans `POST /audit/reconcile/file` (champ `fiche_revue`). Valeur métier : le gestionnaire reçoit un dossier prêt à arbitrer ; **lecture seule** (le contrat n'est jamais modifié). | TDD (+3 tests : ECART liste écarts, CONFORME non-à-revoir, fail-safe entrée None) ; `pytest actions/tests` **109✅/5⏭** ; bandit 0 | (branche prod) |
| 8 | 2026-06-23 | RAG-LOCAL (consolidation) | **`app/rag_local.py`** — RAG **non-agentique** souverain promu de script bash → **module testé** : `retrieve` (score recouvrement mots-clés) + `build_rag_prompt` (grounded) + `answer` (récup→génère, lecteur/générateur **injectables**, fail-closed : question vide/aucune source/génération KO ⇒ `grounded=False`, pas d'invention). Bonne archi pour modèle local (contourne #12). | TDD `tests/test_rag_local.py` (**7 tests** : récup bon dossier, vide, prompt, grounded via générateur injecté, 3× fail-closed) ; `pytest actions/tests` **116✅/5⏭** ; bandit 0 | (branche prod) |
| 9 | 2026-06-23 | RECON-RESILIENCE (durcissement) | **`fetch_client_reference` résiliente** : retries + backoff sur blip réseau OneLake (env `ONIX_FABRIC_READ_ATTEMPTS` défaut 2, borné [1,5]) + timeout configurable (`ONIX_FABRIC_READ_TIMEOUT` défaut 15 s, borné [3,60]) ; `_sleep` isolé (neutralisable en test). Sémantique préservée : `None` (client absent) ≠ exception (retentée), toutes tentatives KO ⇒ fail-closed. Bornes anti-abus. | TDD (+3 tests : retry après blip, fail-closed après N échecs, bornage env) ; `pytest actions/tests` **119✅/5⏭** ; bandit 0 | (branche prod) |
| 10 | 2026-06-23 | DATES-FR (durcissement métier) | `normalize_date` gère désormais les **dates FR en toutes lettres** (« 1er janvier 2026 », « 15 mars 2025 », « 1 août 2026 », « 3 janv. 2025 ») via `_FRENCH_MONTHS` + `_parse_french_text_date` (sans accents, abréviations, « 1er »), **fail-closed** sur date impossible (31 février) ou non-pure. Formats numériques inchangés. Évite un **faux ECART** quand le contrat date en lettres et le SI en ISO. | TDD (+3 tests : dates FR, fail-closed, réconciliation date FR↔ISO=MATCH) ; `pytest actions/tests` **121✅/5⏭** ; bandit 0 | (branche prod) |
| 11 | 2026-06-23 | RECON-ENDPOINT-TESTS (couverture) | L'endpoint `POST /audit/reconcile/file` (cœur réconciliation) est désormais **testé au niveau HTTP** (TestClient, OCR + lecture SI Fabric mockés) : flux complet OCR→champs→réf Fabric→audit→verdict **+ fiche de revue**. 3 cas : ECART (cotisation, `_reference_source=fabric_si`, `fiche_revue.a_revoir=true`), CONFORME, CLIENT_NON_TROUVE (réf None ⇒ fail-closed). | `tests/test_api.py` (+3 tests endpoint) ; `pytest actions/tests` **124✅/5⏭** ; bandit 0 (code inchangé) | (branche prod) |
| 12 | 2026-06-23 | RAG-ENDPOINT (valeur métier) | **Endpoint `POST /rag/ask`** : le RAG non-agentique `rag_local` devient une API usable. Récupère le(s) doc(s) pertinent(s) du corpus fourni puis génère une réponse **grounded** en local (générateur Ollama par défaut `rag_local.ollama_generator`, stdlib, anti-SSRF schéma maîtrisé, injectable). Fail-closed : aucune source ⇒ refus explicite ; génération KO ⇒ grounded=False. Événements FinOps `rag_ask_*` enregistrés. | TDD `tests/test_api.py` (+2 : grounded avec bonnes sources, refus sans source) ; `pytest actions/tests` **126✅/5⏭** ; bandit 0 | (branche prod) |
| 13 | 2026-06-23 | AUDIT-TAMPER (couverture sécurité) | Couverture des propriétés **tamper-evidence de base** de `verify_chain` (jusque-là seul l'anti-downgrade M1 était testé) : +3 tests — record **altéré au milieu** (entry_hash recalculé≠stocké → `broken_at`), maillon **supprimé** (seq non contiguë), **chaînage rompu** (prev_hash altéré). Verrouille les garanties d'intégrité du trail d'audit. | `tests/test_audit_log.py` (+3) ; `pytest actions/tests` **129✅/5⏭** ; bandit 0 (code inchangé) | (branche prod) |

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
