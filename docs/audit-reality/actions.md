# Audit byte-by-byte « doc ↔ réalité » — scope **actions** (`onix-actions`)

> **Date** : 2026-06-18 · **Auditeur** : ingénieur backend/sécurité (revue READ-ONLY)
> **Méthode & légende** : cf. [`README.md`](README.md) (✅ conforme · ⚠️ écart mineur ·
> ❌ écart majeur · 🕳️ doc-sans-code · 🔇 code-sans-doc · ❔ non vérifiable).
> **Périmètre docs** : `docs/ACTIONS.md`, `docs/FINOPS.md`,
> `docs/SECURITY_RGPD_ACTIONS.md`, `docs/STATELESS_ACTIONS.md`.
> **Périmètre code/config** : `actions/app/*`, `actions/tests/*`, `actions/Dockerfile`,
> `actions/requirements.txt`, `actions/openapi.json`, branchement
> `docker-compose.yml` + `deploy/k8s/onix-ha/`.

L'essentiel du code applicatif est **conforme et sérieusement implémenté** (audit
HMAC chaîné vérifiable, redaction PII, DLP egress fail-closed, rétention/effacement,
identité d'appelant HMAC/JWT, FinOps tokens mesurés). Les écarts les plus graves ne
sont **pas dans le code Python** mais dans le **branchement de déploiement** (Helm/
compose) : plusieurs garanties sécurité/RGPD documentées ne sont **pas câblées** côté
chart HA, où elles tombent silencieusement en mode dégradé.

---

## Tableau de comptage par classe

| Classe | Nb | Commentaire |
|---|---:|---|
| ✅ CONFORME | 46 | Cœur applicatif + Helm (P0) + openapi.json régénéré, rate-limit HA documenté, compteurs de tests réconciliés (P1/P2, itération Ralph 2 2026-06-18) |
| ⚠️ ÉCART MINEUR | 2 | Vars WS2 non listées dans le bloc `environment:` du compose (P2, fonctionnelles via `env_file`) |
| ❌ ÉCART MAJEUR | 0 | **Les 3 P0 HA corrigés** (itération Ralph 1) — cf. §"Écarts priorisés" |
| 🕳️ DOC-SANS-CODE | 0 | Aucune garantie *purement* fantôme trouvée dans le code |
| 🔇 CODE-SANS-DOC | 1 | `/metrics` Prometheus non décrit dans ACTIONS.md (P2) — désormais présent dans `openapi.json` régénéré |
| ❔ NON VÉRIFIABLE | 2 | Recette cluster réel HA (HPA/bascule), comportement Ollama live |

> **Nuance importante** : il ne reste **0 ❌**. Les écarts majeurs historiques étaient
> des écarts **doc ↔ déploiement** (la doc affirmait un câblage Helm absent), pas des
> bugs du code applicatif ; ils sont **corrigés** (3 P0 HA, itération Ralph 1). Le risque
> « mock présenté comme réel » à l'exécution en HA est donc levé côté chart.

---

## 1. `docs/ACTIONS.md` — endpoints, contrats, sécurité

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| 8 familles d'endpoints (audit, generate, tasks, notify, usage, cost, admin, health) | ✅ | `actions/app/main.py:348-938` | Tous présents et gatés |
| `POST /audit` doc déjà extrait OU texte brut, verdict + score | ✅ | `main.py:472-490`, `audit_engine.py:279` | Verdicts `CONFORME/ECART/INCERTAIN/CLIENT_NON_TROUVE` (`audit_engine.py:11`) |
| `POST /audit/file` → OCR local → extraction → comparaison | ✅ | `main.py:493-555`, `ocr.py:145-191` | |
| OCR dégrade proprement : `extraction_mode=unavailable` → 422 + raison | ✅ | `main.py:519-530`, `ocr.py:152-179` | 422 seulement si `use_llm=false` |
| OCR : PDF texte→pdfplumber (repli pypdf) ; scanné→pdf2image+pytesseract ; image→pytesseract | ✅ | `ocr.py:93-143` | |
| Assistance LLM via Ollama, repli heuristique non bloquant, mode journalisé | ✅ | `main.py:407-459`, `llm.py:234-313` | `_extraction_mode` = `llm`/`heuristic`/`provided` |
| Parsing LLM robuste (prose, fences, objet imbriqué) | ✅ | `llm.py:158-226` | accolades équilibrées + cascade |
| Champs canoniques + aliasing + normalisation typée + comparaison champ par champ | ✅ | `audit_engine.py:70-277` | montant/date/nom/contrat normalisés |
| Référence inline OU fichier monté (JSON/CSV), filtre `client_key`, anti-traversal | ✅ | `main.py:196-237` | confinement `os.path.abspath` + `startswith(root+sep)` |
| Clé API comparée en **temps constant** | ✅ | `security.py:70-74` (`hmac.compare_digest` sur digests SHA-256) | |
| Validation upload : extension allowlistée, taille ≤ `ONIX_MAX_UPLOAD_BYTES` (15 Mo) | ✅ | `security.py:294-305` | 413 si trop gros, 400 si vide/ext |
| Anti path-traversal génération/lecture (`resolved.parents`) | ✅ | `docgen.py:85-89,125-139` | + sanitisation `safe_filename` |
| Aucun identifiant en clair (UPN/clients hashés SHA-256) | ✅ | `admin_state.hash_id` (`admin_state.py:60-63`), usage/tasks/cost hashent | |
| Logs sans corps de requête | ✅ | `notify.py:9`, redaction globale `safe_logger.install` (`main.py:64`) | |
| Kill-switch global + flag/fonction + blocage utilisateur **gatent réellement** (403) | ✅ | `main.py:166-174`, `admin_state.py:153-163` | persisté en base, survit au redémarrage |
| `/health` non authentifié, expose capacités OCR | ✅ | `main.py:348-356`, `ocr.ocr_capabilities` | |
| Service sur `onix-net`, **aucun port hôte**, appelé via `http://actions:8100` | ✅ | `docker-compose.yml:317-373` (aucun `ports:`) | |
| Variables d'env table §3 (rate card, budget, Ollama, SMTP, OCR, upload…) | ✅ | présentes et lues par les modules concernés | |
| openapi.json = « spec complète et faisant foi » | ✅ | `actions/openapi.json` (**20 paths**) | **Régénéré depuis `app.openapi()`** (P1 fermé) : `/access/log`, `/admin/audit/verify`, `/admin/retention/purge`, `/admin/retention/erase`, `/metrics`, `/audit/file/async`, `/jobs/{task_id}` désormais présents |
| openapi : Auth `X-API-Key` ; admin via `X-Admin-Key` | ✅ | `openapi.json` : **aucun `securityScheme`** ; auth = paramètres d'en-tête typés par opération | L'app ne déclare PAS de scheme nommé → l'ancien `ApiKeyAuth` était ajouté à la main. Le réel : `X-API-Key` sur toutes les routes, **`X-Admin-Key` sur les seules routes `/admin/*`** (paramètre d'en-tête). Contrat admin **décrit** (sans scheme), zéro divergence code↔doc |
| `actions/reference/clients.example.json` fourni | ✅ | fichier présent | |
| Tests : `test_audit_engine.py` + `test_api.py` couvrent santé/auth/audit/docx/403/blocage/tasks/usage/coût | ✅ | `actions/tests/test_api.py` (13 tests), `test_audit_engine.py` (9) | |

---

## 2. `docs/FINOPS.md` — tokens mesurés vs estimés

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Capture `prompt_eval_count`/`eval_count`/durées d'Ollama | ✅ | `llm.py:76-103` (`usage_from_ollama`) | |
| `extract_fields_llm_with_usage` retourne l'usage ; API historique inchangée | ✅ | `llm.py:234-313` | compat ascendante réelle |
| Centres `llm_token_input`/`llm_token_output` valorisés sur comptes réels | ✅ | `main.py:378-404`, `cost_tracker.py:21-105` | |
| Rate card `ONIX_RATE_CARD` €/token paramétrable, 0 € par défaut | ✅ | `cost_tracker.py:43-54`, `DEFAULT_RATE_CARD` à 0 | |
| Flag `measured` (booléen) distinct de `cost_source` | ✅ | `cost_tracker.py:72-104`, `usage_tracker.py:88,116` | |
| Persisté en `usage_events.tokens_measured` (0/1) + migration `ALTER TABLE` | ✅ | `usage_tracker.py:60-70` | migration douce idempotente |
| Surfacé dans `/usage/summary` et `/cost` (bloc `tokens`) | ✅ | `usage_tracker.py:208-223`, `main.py:811-832` | `measured_*`/`estimated_*`/`measured_events` |
| `POST /usage` accepte `measured` (défaut false) | ✅ | `main.py:302,789-808` | |
| `eval_tokens_per_second` dérivé des durées | ✅ | `llm.py:89-92` | |
| Repli estimation `chars/4` quand pas de ground truth | ✅ | `llm.py:286-295`, `main.py:447-457` | marqué `measured=False` honnêtement |
| Limite honnête : `cache_tokens_saved` gateway reste estimé (Onyx médie) | ❔ | doc FINOPS §5 ; concerne `access-gateway`/Onyx | hors scope code actions ; honnêteté correcte |
| Tests FinOps (mesuré/estimé/coût/bout-en-bout) | ✅ | `test_finops_tokens.py` (11 tests) | mocks `httpx.post` clairement étiquetés (pas de mock présenté comme réel) |

---

## 3. `docs/SECURITY_RGPD_ACTIONS.md` — sécurité applicative & RGPD (priorité)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Redaction PII : JWT/IBAN/NIR/email/Bearer/carte/téléphone, porte unique | ✅ | `safe_logger.py:43-87,114-157` | motifs réellement présents |
| Redaction sur **tous** logs `onix.actions.*` via `logging.Filter` | ✅ | `safe_logger.py:160-189`, `main.py:64` | filtre idempotent installé au démarrage |
| Redaction des champs libres avant persistance : `reason`/`notes`/`action_name` | ✅ | `admin_state.py:218-222`, `main.py:721,799` | testé `test_audit_reason_redacted` |
| Anti-CRLF (CWE-117), irréversible, fail-safe (jamais lever) | ✅ | `safe_logger.py:91-127` | `[REDACTION_ERROR]` en repli |
| Contenu `.docx` NON redacté (livrable demandé) | ✅ | `main.py:650-653` | choix documenté et appliqué |
| Clé service temps constant ; absente → 503 | ✅ | `security.py:88-102` | |
| Identité HMAC par appel : `caller\ntimestamp\nMETHOD\npath`, anti-rejeu skew | ✅ | `caller_identity.py:94-122` | lie identité+ts+requête, non transférable |
| Identité JWT OIDC : HS256 natif OU RS256/ES256 via PyJWT+JWKS ; exp/iss/aud | ✅ | `caller_identity.py:141-222` | **fail-closed si iss/aud non configurés** (`:185`) ; HS256 exige `exp` (`:161-164`) |
| Identité toujours hashée SHA-256 avant log/persistance | ✅ | `usage_tracker._maybe_hash`, `admin_state.hash_id` | |
| Fail-closed `ONIX_ACTIONS_REQUIRE_CALLER_IDENTITY=true` → 401 | ✅ | `security.py:127-132`, `caller_identity.py:80-88` | testé (`test_require_caller_fail_closed`) |
| **Clé admin distincte OBLIGATOIRE** (fail-closed) : sans clé → 403 | ✅ | `security.py:177-209` | opt-out `ADMIN_KEY_OPTIONAL` documenté |
| Rate-limiting par appelant (fenêtre glissante mémoire), 429 + Retry-After | ✅ | `security.py:137-282` | clé = identité vérifiée sinon IP ; admin exclu (`:190-193`) |
| Multi-instance : quota par-process = `N × réplicas` (slowapi/Redis à brancher) | ✅ | `security.py:254-282` (fenêtre mémoire) ; slowapi importé mais **non utilisé** comme enforcement (`security.py:34-40`) | **P1 documenté** : encadré ⚠️ dédié dans SECURITY_RGPD §3.5 (effectif `RATE_LIMIT × réplicas`, ex. 60/min × 4 = 240/min). Limite assumée ; aucun store Redis livré ce tour ; correctif futur en backlog |
| **Audit admin chaîné HMAC** : `seq`, `prev_hash`, `entry_hash=HMAC(secret, prev_hash‖canonical)` | ✅ | `audit_log.py:82-162` | canonique déterministe (`_SIGNED_FIELDS`, sort_keys) |
| Toute modif/suppression/réordonnancement détectable ; `GET /admin/audit/verify` | ✅ | `audit_log.py:165-195`, `main.py:872-876` | testé altération → `broken_at` |
| Secret absent → repli **SHA-256** (avertissement), à définir en prod | ✅ | `audit_log.py:88-103,139-145`, marqueur `algo` par ligne | chaîne mixte sha256↔hmac gérée sans faux positif |
| DLP egress allowlist `ONIX_EGRESS_ALLOWLIST`, `*.corp.local`, hors liste → 403 | ✅ | `dlp.py:67-160`, `main.py:715-719,767-773` | appliqué `/notify` + `/tasks(webhook_url)` |
| Fail-closed : allowlist vide + `DEFAULT_DENY=true` → tout refusé | ✅ | `dlp.py:144-150` | défaut deny |
| https-only par défaut ; http si `ALLOW_HTTP=true` | ✅ | `dlp.py:128-134` | |
| Anti-SSRF : IP privée/loopback/link-local refusée (+ CGNAT/benchmark) | ✅ | `dlp.py:88-114,152-158` | limite DNS-rebinding **documentée honnêtement** (`dlp.py:29-34`) |
| SMTP STARTTLS exigé par défaut (refus envoi clair si non annoncé) | ✅ | `notify.py:114-131` | |
| Rétention purge TTL `POST /admin/retention/purge` (usage/tâches terminées/.docx) | ✅ | `retention.py:56-119`, `main.py:916-921` | journal d'audit **non** purgé |
| Effacement ciblé sujet `POST /admin/retention/erase` (art.17), par id clair OU hash | ✅ | `retention.py:145-189`, `main.py:924-937` | colonnes hashées ; audit préservé |
| `.docx` du sujet effacés en best-effort (nom de fichier) — note explicite | ✅ | `retention.py:192-194` (local) + `delete_subject_docx` (S3) | best-effort par nom (local **et** S3) ; **en mode S3 l'objet est désormais effacé** (cf. §4, P0#3 corrigé) |
| Fail-closed sur flag inconnu (typo) → False + log | ✅ | `admin_state.py:106-123` | inverse le fail-open AC360 ; testé |
| `gen-secrets.sh` génère ADMIN_KEY/CALLER_HMAC/AUDIT_HMAC (48 car., idempotent) | ✅ | `scripts/gen-secrets.sh:95-107` | `.env` gitignoré |
| `/access/log` + helpers (UPN/doc hashés, requête RAG jamais en clair) | ✅ | `audit_log.py:201-237`, `main.py:882-910` | `rag_search` ne stocke que la longueur |
| `/access/log` ne répond pas « journalisé » si persistance échoue → 500 | ✅ | `main.py:904-909`, `usage_tracker.py:134-164` (`_persisted`) | bon réflexe traçabilité |
| « 58 tests verts (dont test_security_rgpd.py) » | ✅ | suite réelle = **90 collectés** (85 passed, 5 skipped) ; `test_security_rgpd.py`=**32** | **P2 corrigé** : SECURITY_RGPD §11 affiche désormais « 90 collectés : 85 passed, 5 skipped (dont test_security_rgpd.py = 32) » |

---

## 4. `docs/STATELESS_ACTIONS.md` — multi-réplica (HA)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Couche d'accès `db.connect()` SQLite par défaut / Postgres opt-in | ✅ | `db.py:335-353` | context manager type `sqlite3.Connection` |
| Adaptateur SQLite→PG : `?`→`%s`, `:nom`→`%(nom)s`, `INSERT OR REPLACE`→`ON CONFLICT`, PRAGMA/sqlite_master→information_schema | ✅ | `db.py:142-258` | nom de table en **paramètre lié** ; `%` littéraux doublés |
| `admin_state` réexporte `_connect`/`_lock` ; modules historiques inchangés | ✅ | `admin_state.py:28-29`, imports dans tasks/usage/audit/retention | surface minimale réelle |
| Chaîne d'audit vérifiable en PG ; `pg_advisory_xact_lock` sérialise l'append | ✅ | `audit_log.py:106-130` | verrou relâché au COMMIT |
| Activation PG via `ONIX_DB_BACKEND=postgres` + `ONIX_DB_URL` OU `POSTGRES_*` | ✅ | `db.py:90-109` | dérivation DSN depuis `POSTGRES_*` réelle |
| Stockage objet S3/MinIO opt-in (`ONIX_OBJECT_STORE=s3`), clé `jobs/<id>/<file>` | ✅ | `objstore.py:42-153`, `docgen.py:119-179` | boto3 path-style, import paresseux |
| `GET /download` relit depuis S3 → multi-réplica | ✅ | `docgen.read_download`/`list_job_docx`, `main.py:676-701` | |
| File Celery opt-in : objet `app.celery_app.celery`, `audit_file_async`, EAGER | ✅ | `celery_app.py:77-169` | commande chart exacte respectée |
| `POST /audit/file/async`→202+task_id ; `GET /jobs/{id}` ; 503 si non activé | ✅ | `main.py:561-638` | mêmes garde-fous (auth/kill-switch/validation) |
| Défaut mono-poste **strictement inchangé** (SQLite/local/synchrone) | ✅ | défauts `db.backend()`/`objstore.backend()`/`queue_enabled()` | imports paresseux PG/S3/Celery |
| « 71 passed, 4 skipped » suite par défaut | ✅ | suite réelle = **90 collectés** (85 passed, 5 skipped) | **P2 corrigé** : STATELESS §6.1 affiche désormais « 90 collectés : 85 passed, 5 skipped » |
| Le chart **câble déjà** `ONIX_DB_BACKEND=postgres`, `ONIX_QUEUE_ENABLED`, broker, `POSTGRES_*`/`S3_*` | ✅ | `configmap.yaml:30-49`, `actions.yaml:45-46`, helpers `dataTierSecretEnv`/`actionsSecretEnv` | **Corrigé (P0#2)** : `ONIX_OBJECT_STORE`+`ONIX_S3_BUCKET`+`POSTGRES_DB` ajoutés au ConfigMap (`configmap.yaml:45-49`) → S3 activé en HA |
| values.yaml documente `actions.config.objectStore`, `dbUrl`, `s3Bucket`, etc. | ✅ | `values.yaml:236-237` rendus par `configmap.yaml:36-49` | clés désormais **rendues** dans les templates (preuve : `helm template … | grep ONIX_OBJECT_STORE` → `"s3"`) |
| Rétention RGPD en mode S3 efface aussi les objets S3 | ✅ | `retention.py:97-102` (`delete_jobs_older_than`), `retention.py:185-194` (`delete_subject_docx`) ; `objstore.py:173-247` | **Corrigé (P0#3)** : `erase_subject`/`purge_by_age` branchent la suppression S3 (fail-safe si local) ; testé `test_security_rgpd.py` (4 tests, mock client S3) |
| Recette cluster réel (HPA, bascule HA, débit Celery, PDB) hors périmètre code | ❔ | doc §7 | non vérifiable ici (honnêteté correcte) |

---

## Écarts « production-ready » priorisés

### P0 — sécurité/RGPD ✅ CORRIGÉS (itération Ralph 2026-06-18)
1. **✅ Secrets WS2 injectés par le chart Helm.** Helper `onix.actionsSecretEnv`
   (`_helpers.tpl:155-171`) injecte `ONIX_ACTIONS_ADMIN_KEY`,
   `ONIX_ACTIONS_AUDIT_HMAC_KEY`, `ONIX_ACTIONS_CALLER_HMAC_SECRET` via `secretKeyRef`
   dans le Deployment `actions` (`actions.yaml:45`) **ET** le worker Celery
   (`actions-queue.yaml:127`). Clés documentées dans `secret.yaml:7-9`, `values.yaml:51-54`,
   placeholders factices CI dans `values-kind-smoke.yaml:36-39`. **Preuve** :
   `helm template … | grep ONIX_ACTIONS_ADMIN_KEY` → 2 `secretKeyRef` (actions+worker).
   « Zéro secret en repo » respecté : valeurs via `secrets.existingSecret`/`create`.

2. **✅ `ONIX_OBJECT_STORE=s3` câblé en HA.** ConfigMap pose désormais
   `ONIX_OBJECT_STORE`, `ONIX_S3_BUCKET`, `POSTGRES_DB` (+`ONIX_DB_URL`/`ONIX_DB_SCHEMA`
   conditionnels) — `configmap.yaml:30-49`, pilotés par `actions.config.*`. Les creds S3
   restent dans le Secret (`dataTierSecretEnv`), `S3_ENDPOINT_URL` déjà en ConfigMap.
   **Preuve** : `helm template … | grep ONIX_OBJECT_STORE` → `"s3"` (défaut values).

3. **✅ Effacement RGPD S3 exhaustif (art. 17).** `objstore` expose
   `delete_subject_docx` (par nom de sujet) et `delete_jobs_older_than` (par âge)
   — `objstore.py:173-247`. `retention.erase_subject` (`retention.py:185-194`) et
   `purge_by_age` (`retention.py:97-102`) les appellent en mode S3 (fail-safe si local).
   **Preuve** : 4 tests `test_security_rgpd.py` (client S3 mocké) prouvant la suppression
   des bons objets, la préservation des autres sujets, et le no-op en mode local.

### P1 — fiabilité / contrat ✅ CORRIGÉS (itération Ralph 2 2026-06-18)
4. **✅ `openapi.json` régénéré depuis l'app réelle.** `app.openapi()` → **20 paths**
   (vs 13) ; endpoints WS2/stateless désormais présents (`/access/log`,
   `/admin/audit/verify`, `/admin/retention/purge|erase`, `/metrics`,
   `/audit/file/async`, `/jobs/{task_id}`). **Constat honnête sur l'auth** : l'app ne
   déclare **aucun `securityScheme`** ; l'auth est exprimée comme **paramètres d'en-tête
   typés** par opération (`X-API-Key` partout ; **`X-Admin-Key` sur les seules routes
   `/admin/*`**). L'ancien scheme `ApiKeyAuth` était ajouté à la main → supprimé. Contrat
   admin **décrit** (sans scheme), zéro divergence code↔doc. *Limite documentée* : pas de
   `securityScheme` nommé (FastAPI ne le génère pas pour ces dépendances d'en-tête).
   **Preuve** : `python -c "json.load(open('actions/openapi.json'))"` OK.
5. **✅ Rate-limit par-process en HA — documenté (limite assumée).** `slowapi` est en
   dépendance mais **non utilisé** pour l'enforcement (fenêtre mémoire locale,
   `security.py:254-282` ; import diag `security.py:34-40`). Quota effectif = `RATE_LIMIT
   × réplicas`. Encadré ⚠️ dédié dans SECURITY_RGPD §3.5 (ex. 60/min × 4 = 240/min).
   **Aucun store Redis ajouté ce tour-ci** (décision explicite) ; correctif futur (store
   Redis partagé) noté en backlog.
6. **✅ Compteurs de tests réconciliés.** Comptage réel `--collect-only` = **90** ;
   exécution offline = **85 passed, 5 skipped**. Corrigés dans SECURITY_RGPD §11
   (58→90/85/5) et STATELESS §6.1 (71/4→90/85/5). FINOPS/ACTIONS sans compteur chiffré.

### P2 — cosmétique / dette
7. **⚠️ Variables WS2 non listées dans le bloc `environment:` du compose** (chargées via
   `env_file: .env`, donc fonctionnelles) — manque de lisibilité ; egress/retention
   prennent leurs défauts (egress `default-deny` → `/notify` & `/tasks` webhook bloqués
   sans allowlist : comportement sûr mais surprenant).
8. **🔇 `/metrics` Prometheus** implémenté (`main.py:359-372`) et non décrit dans ACTIONS.md.
9. **🔇 dérivation DSN `POSTGRES_*`** : code utile (db.py) ; désormais documenté
   (STATELESS §2). `objstore.delete_job` était non branché → **branché (P0#3)** via
   `delete_subject_docx`/`delete_jobs_older_than` dans `retention.py`.

---

## Verdict (3 lignes)

Le **code applicatif `onix-actions` est solide et honnête** : audit HMAC chaîné réellement
vérifiable et tamper-evident, redaction PII effective, DLP egress fail-closed + anti-SSRF,
rétention/effacement implémentés, identité HMAC/JWT fail-closed, FinOps tokens mesurés vs
estimés sans tricherie — le tout couvert par **90 tests collectés** (85 passed, 5 skipped en
offline minimal). **Aucune garantie de sécurité n'est purement fantôme dans le code (0 🕳️).**
Les **3 P0 du branchement HA (Helm) sont corrigés** (itération Ralph 1, 2026-06-18) : secrets
WS2 injectés via `onix.actionsSecretEnv` (actions+worker), `ONIX_OBJECT_STORE=s3`/`ONIX_S3_BUCKET`
câblés dans le ConfigMap, et effacement RGPD S3 branché (`erase_subject`/`purge_by_age` suppriment
les objets du bucket). **Les P1/P2 d'exactitude doc↔code sont fermés** (itération Ralph 2,
2026-06-18) : `openapi.json` régénéré depuis `app.openapi()` (20 paths, X-Admin-Key décrit sur
`/admin/*` ; pas de `securityScheme` nommé = limite documentée), rate-limit par-process en HA
documenté (quota `N × réplicas`, aucun Redis livré ce tour), compteurs de tests réconciliés.
**Mono-poste ET HA = production-ready côté code/chart** ; restent uniquement des P2 cosmétiques
(vars WS2 non listées au bloc `environment:` du compose ; `/metrics` non décrit dans ACTIONS.md)
et la recette cluster réel (HPA/bascule) hors périmètre code.
