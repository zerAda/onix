# Missions — programme d'amélioration (Cycle 1)

**Généré :** 2026-06-21 par la boucle chief-orchestrator (scout → verify adversarial → spec).
**Cycle 1 :** 6 scouts → 24 candidats → 15 vérifiés → **14 confirmés** (+ 9 non-vérifiés, 1 rejeté).
**✅ Livré sur `main` (2026-06-21, CI verte, commit `3298729`)** : **M12** (toutes les actions SHA-pinnées + Dependabot + faux « SBOM attached to release » corrigé), **M5a** (mensonge doc pg_dump corrigé), **M14** (fausses « ✅ conforme » RAGAS corrigées). **M7** vérifié → reclassé P1 défense-en-profondeur. Reste : M1, M2, M3, M4, M6, M8–M11, M13, M15, M16.
**Routing :** `SAFE_INLINE` = je peux le faire ici (hors-scope Ralph, pas de Docker) · `PR_BRANCH` = livrer en PR relue · `ROUTE_TO_RALPH` = code de scope que la boucle Ralph possède · `NEEDS_DOCKER`/`NEEDS_TENANT` = preuve impossible sur cette machine.

> **Règle.** Toute modif de code d'un scope (`access-gateway/`, `actions/`, `tests/rag/`+`prompts/`, `monitoring/`, `deploy/`+`nginx/`) déclenche le gate `docs-freshness` → MAJ `docs/scopes/<scope>.md` + `docs/audit-reality/<scope>.md` + `ralph/state/<scope>.md` via `/update-scope-docs`. `.github/`, `.devcontainer/`, `.planning/`, `ralph/` ne sont PAS des scopes → modifiables sans ce gate. `make test` doit rester vert ; honnêteté (« zéro mock présenté comme réel ») non négociable.

---

## P0 — sécurité critique

### M1 · Audit HMAC : downgrade d'algorithme contournable *(NOUVEAU)*
- **Scope** actions · **Routing** ROUTE_TO_RALPH · **Effort** S · **Value** HIGH
- **Problème** : `actions/app/audit_log.py:189-190` recalcule le hash de chaque ligne d'après sa **propre colonne `algo`**. Un attaquant ayant un accès écriture à la table `admin_audit` peut réécrire l'historique, poser `algo='sha256'` (sans clé, `:96-103`) sur chaque ligne falsifiée, re-chaîner, et `verify_chain()` renvoie `{ok:true}` **même quand `ONIX_ACTIONS_AUDIT_HMAC_KEY` est posée**. La preuve d'inviolabilité (cœur de valeur, `PARITE_ENTREPRISE.md:15`) est défaite. Distinct et plus grave que HARD-03.
- **Plan** : (1) dans `verify_chain`, quand `_audit_secret() is not None`, refuser toute ligne `algo != 'hmac-sha256'` une fois la 1re ligne hmac vue → `broken_at` + `reason='algo_downgrade'` ; tolérer `sha256` **uniquement** en préfixe historique strict avant la 1re ligne hmac (le test légitime `test_security_rgpd.py:422` doit rester vert). (2) test : falsifier une ligne, poser `algo='sha256'`, re-chaîner en keyless, asserter `verify_chain()['ok'] is False` quand une clé est posée.
- **Fichiers** : `actions/app/audit_log.py`, `actions/tests/test_security_rgpd.py`
- **Agent** : `gsd-executor` · **Skills** : `update-scope-docs` (scope actions) · **Tools** : Read, Edit, Bash(pytest)
- **Acceptance** : nouveau test rouge-avant/vert-après ; `make` suite `actions/tests` verte ; `verify_chain` rejette un chain downgradé sous clé.
- **Vérif** : offline (pytest `actions/tests`), pas de Docker. **Prompt** ci-dessous.

> **Dispatch prompt (M1)** — « Tu corriges une faille de tamper-evidence dans `actions/app/audit_log.py`. Aujourd'hui `verify_chain()` (lignes 165-195) fait confiance à la colonne `algo` par ligne (lignes 189-190), donc un attaquant qui réécrit la table SQLite `admin_audit` peut poser `algo='sha256'` (chemin keyless, lignes 96-103), re-chaîner, et obtenir `ok:true` même si `ONIX_ACTIONS_AUDIT_HMAC_KEY` est configurée. Corrige : quand `_audit_secret()` n'est pas None, refuse toute ligne dont `algo != 'hmac-sha256'` dès que la première ligne hmac est rencontrée (retourne `{ok:False, broken_at:<seq>, reason:'algo_downgrade'}`) ; n'autorise `sha256` qu'en préfixe strictement antérieur à la première ligne hmac (préserve le cas mixte légitime de `test_security_rgpd.py:422`). Ajoute un test qui falsifie une ligne, force `algo='sha256'`, recalcule le chaînage keyless complet, et asserte `verify_chain()['ok'] is False` clé posée. Contraintes : commentaires FR, `make` suites `actions/tests` vertes, pas de mock présenté comme réel. Termine par `/update-scope-docs actions`. »

### M2 · RAGAS nightly : gate qualité 100 % mort *(re-confirmé ×3)*
- **Scope** rag-prompts · **Routing** PR_BRANCH (ou Ralph) · **Effort** S · **Value** HIGH
- **Problème** : collision de nom `conftest`. `live_harness.py:38` `from conftest import read_prompt_block` → sous `python -m ragas_eval.runner`, Python charge `tests/rag/ragas_eval/conftest.py` (sans le symbole) → `ImportError` → `runner.py:282-285` retourne 2 en 0 s, **avant toute éval**. Reproduit. pytest reste vert (charge le bon conftest) → invisible en CI. Le gate qualité n'a **jamais** mesuré une réponse — violation Règle #1. (cf. RALPH-HANDOFF RAGAS-FIX)
- **Plan** : extraire `read_prompt_block`/`read_prompt_markdown`/`load_dataset` dans `tests/rag/prompt_loader.py` ; ré-exporter depuis `conftest.py` (pytest reste vert) ; `live_harness.py:38` → `from prompt_loader import read_prompt_block`. Ajouter une assertion non-pytest (`-m`) anti-régression.
- **Fichiers** : `tests/rag/prompt_loader.py` (nouveau), `tests/rag/conftest.py`, `tests/rag/live_harness.py`, `tests/rag/test_runner_plumbing.py`
- **Agent** : `gsd-executor` · **Skills** : `update-scope-docs` (rag-prompts) · **Tools** : Read, Write, Edit, Bash
- **Acceptance** : `cd tests/rag && python -m ragas_eval.runner` atteint la vérif Ollama (plus d'ImportError) ; `pytest tests/rag` reste vert.
- **Vérif** : offline (l'échec ET le fix se manifestent à l'import — pas de Docker/Ollama).

*(M7 vérifié par l'orchestrateur le 2026-06-21 → reclassé P1 défense-en-profondeur, voir plus bas.)*

---

## P1 — provabilité, fiabilité, honnêteté

### M7 · Passerelle : aucune preuve in-app que `X-OIDC-Claims` vient du proxy de confiance *(NOUVEAU — VÉRIFIÉ)*
- **Scope** access-gateway · **Routing** PR_BRANCH · **Effort** M · **Value** MEDIUM (défense-en-profondeur ; exploitabilité conditionnée à la position réseau)
- **Vérifié (2026-06-21)** : `resolve_principal` (`identity.py:140-142`) fait confiance à `X-OIDC-Claims` *verbatim* ; **aucun** secret proxy / signature de claims dans `access-gateway/app` (les seuls HMAC = pseudonymisation audit + clé cache). **CONFIRMÉ.** MAIS le scout a **surévalué** : la passerelle n'est déployée QUE dans `deploy/prod/docker-compose.prod.yml:312` (absente du compose de base — la claim « base sans oauth2-proxy » est fausse) ; `:8200` est **exposé conteneur sur `onix-net`, non publié sur l'hôte** (`:325 - "8200"`) ; Caddy (`Caddyfile:72 request_header -X-OIDC-Claims`) + nginx (`nginx.prod.conf:130-133`) **suppriment et re-posent** l'en-tête (anti-usurpation explicite, auteurs conscients `:208`).
- **Risque résiduel réel** : un attaquant **déjà sur `onix-net`** (2e conteneur compromis, SSRF, port `:8200` publié par erreur) peut forger l'en-tête et usurper n'importe qui — la frontière de confiance la plus critique n'a **aucun contrôle in-app fail-closed** qu'un auditeur puisse exhiber.
- **Plan** : ajouter un contrôle in-app — secret `GATEWAY_TRUSTED_PROXY_SECRET` (comparaison constant-time) injecté par nginx/oauth2-proxy, OU signature HMAC des claims (oid|upn|exp+freshness) au hop proxy vérifiée dans `resolve_principal`. Secret configuré mais absent/invalide → 401 (fail-closed). Default-off localhost dev. Test : `X-OIDC-Claims` forgé sans secret → 401.
- **Fichiers** : `access-gateway/app/identity.py`, `main.py`, `config.py`, `deploy/prod/nginx.prod.conf` (injecter le secret), `docs/SECURITY.md`, tests
- **Agent** : `gsd-executor` · **Skills** : `update-scope-docs` (access-gateway) · **Vérif** : offline (pytest gateway).

### M3 · ACL Fabric non câblée dans le filtre de citations *(NOUVEAU ×2)*
- **Scope** access-gateway · **Routing** ROUTE_TO_RALPH · **Effort** M · **Value** HIGH
- **Problème** : `fabric_acl.can_principal_read/authorized_items` ne sont appelées QUE par les tests/e2e — jamais par `main.py:_build_doc_acl` (`:94-131`, compose Static+Graph seulement) ni `filter_citations` (`:495-501`). Aucune `FabricDocACL`. En prod, une citation vers un doc Fabric/OneLake gold passe **non filtrée**. Le cœur de valeur (ACL par-doc prouvable SharePoint **et** Fabric) est faux côté code.
- **Plan** : ajouter `FabricDocACL(DocACL)` adaptant `fabric_acl` à l'interface **synchrone** `authorized_ids(candidate_ids, principal)`. Subtilité clé : `fabric_acl` est async + I/O réseau → **ne pas** appeler en hot-path ; pré-construire une ACL TTL en mémoire via `build_fabric_acl(...)` appelée depuis `_build_doc_acl` + `_acl_refresher`, comme `GraphDocACL`. Flag `doc_acl_fabric_enabled` (défaut False, opt-in). Fail-closed. Test offline (httpx MockTransport) : doc Fabric cité droppé pour principal non autorisé via `filter_citations`.
- **Fichiers** : `access-gateway/app/doc_acl.py`, `main.py`, `config.py`, `fabric_acl.py`, tests + docs scope
- **Agent** : `gsd-executor` · **Skills** : `update-scope-docs` (access-gateway) · **Vérif** : offline (mock httpx), pas de Docker/tenant.

### M4 · Alertes livrées dans le vide + doc « ✅ conforme » fausse *(NOUVEAU)*
- **Scope** monitoring · **Routing** ROUTE_TO_RALPH · **Effort** M · **Value** HIGH
- **Problème** : `alertmanager.yml` — receiver `default` vide (`:34`), `critical` avec `webhook_configs` **commenté** (`:36-40`). Toutes les alertes critiques (TargetDown, ActionsServiceDown, OpenSearchClusterRed…) vont dans un no-op. Pire : la procédure d'activation documentée est **cassée** (`${ALERT_WEBHOOK_URL}` non expansé par Alertmanager, aucun envsubst) **et** marquée « ✅ conforme » (`docs/audit-reality/monitoring.md:132`, `docs/OBSERVABILITY.md:148`). Un auditeur qui suit la doc obtient zéro notification.
- **Plan** : (a) rendre `alertmanager.yml` via entrypoint/Makefile `envsubst` (ou `url_file`) ; (b) décommenter `webhook_configs` + `send_resolved:true` ; (c) WARN bruyant si `ALERT_WEBHOOK_URL` absent (pas de drop silencieux) ; (d) check d'acceptation `amtool` ; (e) **corriger** la fausse « ✅ conforme » + le texte d'activation OBSERVABILITY. Documenter honnêtement le défaut local-first.
- **Fichiers** : `monitoring/alertmanager/alertmanager.yml`, `monitoring/docker-compose.monitoring.yml`, `Makefile`, `docs/OBSERVABILITY.md`, `docs/audit-reality/monitoring.md` + scope docs
- **Agent** : `gsd-executor` · **Skills** : `update-scope-docs` (monitoring) · **Vérif** : `amtool` ; rendu config Docker-gated pour la preuve E2E.

### M5 · Backup = tar à froid (pas pg_dump), non chiffré, + mensonge doc *(NOUVEAU ; doc-fix SAFE_INLINE)*
- **Scope** deploy-ops · **Routing** doc-fix **SAFE_INLINE** / code **NEEDS_DOCKER** · **Effort** M · **Value** HIGH
- **Problème** : `backup.sh:49-55` = `stop` + `tar czf` du volume `db_volume` brut (copie froide, pas `pg_dump`), archives en **clair** (pas de `BACKUP_ENCRYPTION_KEY`). Et `docs/audit-reality/deploy-ops.md:58` affirme « dump à chaud pg_dump | ✅ » — **mensonge** dans le fichier de vérité auditeur (Règle #1).
- **Plan** : **(a) maintenant, SAFE_INLINE** : corriger `deploy-ops.md:58` (✅ → ❌/⚠️, « arrêt bref + tar froid des volumes, pas de pg_dump logique »). **(b) Ralph/Docker** : remplacer le slice Postgres par `pg_dump` logique (garder les tars pour opensearch/minio/file-system), chiffrer via `openssl enc -aes-256-cbc -pbkdf2` clé `BACKUP_ENCRYPTION_KEY` (jamais à côté de l'archive), `restore.sh` apprend déchiffrer+`pg_restore`.
- **Fichiers** : `scripts/backup.sh`, `scripts/restore.sh`, `scripts/gen-secrets.sh`, `docs/audit-reality/deploy-ops.md`
- **Note** : le doc-fix (a) coûte rien et arrête le mensonge → **je peux le faire immédiatement** si tu valides.

### M6 · Restauration jamais testée, succès affirmé inconditionnellement
- **Scope** deploy-ops · **Routing** ROUTE_TO_RALPH (= BKP-01) · **Effort** M · code-preuve **NEEDS_DOCKER**
- **Problème** : `restore.sh:56-57` imprime « ✓ Restauration terminée » sans vérif santé/données ; `:48` saute silencieusement une archive absente. Aucun `make restore-drill`.
- **Plan** : (1) `restore.sh` échoue non-zéro si une archive attendue manque ; (2) `make restore-drill` (restaure dans un projet éphémère, attend `service_healthy` db+api, assertion `SELECT count`, exit non-zéro) — réutiliser les patterns de `scripts/verify.sh`. Preuve = Docker.
- **Agent** : `gsd-executor` · **Skills** : `update-scope-docs` (deploy-ops).

### M11 · Path de démarrage prod sans garde preflight/credential *(NOUVEAU, sharpened HARD-01/02)*
- **Scope** deploy-ops · **Routing** ROUTE_TO_RALPH · **Effort** M · **Value** MEDIUM (défaut sûr, mais défense-en-profondeur + preuve auditeur)
- **Problème** : `Makefile:358 up-local-prod` et `deploy/local-prod/onix.service:46` lancent `up -d` **sans aucun preflight** ; **aucun** script ne vérifie une valeur d'identifiant bannie/faible (preflight-prod ne fait que TLS/OIDC/email ; preflight-local ne teste que non-vide). Nuance honnête : le défaut ne boote PAS avec `password` (env.template vide, gen-secrets fort, `${VAR:?}` fail-closed) — le risque réel est un opérateur qui édite une valeur faible à la main.
- **Plan** : check partagé valeurs bannies + longueur (POSTGRES≠password, MinIO≠minioadmin, REDIS≥seuil, SECRET/USER_AUTH_SECRET présents) → exit non-zéro ; le câbler bloquant dans `up-local-prod` ET le unit systemd (idéalement un service preflight dans l'overlay prod-local).
- **Agent** : `gsd-executor` · **Skills** : `update-scope-docs` (deploy-ops).

### M12 · Release : actions non SHA-pinnées + faux « SBOM attaché à la release » *(NOUVEAU ; SAFE_INLINE)*
- **Scope** deploy-ops (mais fichiers `.github/` = **hors gate scope**) · **Routing** **SAFE_INLINE** · **Effort** M · **Value** MEDIUM
- **Problème** : toutes les actions tierces sont en tag mutable (pas SHA) dans `cd.yml`/`ci.yml`/`runtime-smoke.yml`/`ragas-nightly.yml` (classe d'attaque tj-actions/changed-files, avec `packages: write`). Et `cd.yml:8` prétend « SBOM attaché à la release » alors qu'aucun `gh release` n'existe (juste `upload-artifact`) — mensonge. `gitleaks` fetch `wget` sans checksum. (cosign = CICD-01, **laisser à Ralph**.)
- **Plan** : (1) épingler chaque action tierce à son SHA 40-char + commentaire `# vX.Y.Z` ; (2) corriger `cd.yml:8`+header → « archivé en artefact de workflow » ; (3) `sha256sum -c` pour le tarball gitleaks (`ci.yml:68`, `Makefile:452`) ; (4) Dependabot `github-actions`. **NE PAS** ajouter cosign (CICD-01/Ralph).
- **Fichiers** : `.github/workflows/*.yml`, `.github/dependabot.yml` (nouveau), `Makefile` · **hors docs-freshness** (`.github/` non scopé).
- **Agent** : `gsd-executor` (résoudre les SHA via API GitHub) · **Tools** : Read, Edit, WebFetch (résolution tag→SHA).

### M13 · Boucle Ralph viole ses propres invariants de sûreté *(NOUVEAU — méta-sécurité)*
- **Scope** infra (`ralph/` = **hors gate scope**) · **Routing** PR_BRANCH · **Effort** M · **Value** MEDIUM
- **Problème** : `ralph/loop.sh` contredit `ORCHESTRATION.md §4` / `README:43-44` : (1) tourne dans le **root live** sans worktree/isolation → 2 boucles parallèles se télescopent ; (2) `run_gates()` ne lance qu'un **sous-ensemble** (pytest scope ou compose-validate) — **pas** bandit/gitleaks/pip-audit/trivy — mais le commit affirme « gates verts » (`:80`) ; (3) `git add -A` stage **tout l'arbre** ; (4) **aucun circuit breaker**. Le hook `.githooks/pre-commit` ne fait que les docs → un diff non-scanné sécurité peut être commité « vert ». Violation directe du « provably secure ».
- **Plan** (par valeur) : (b) avant commit, lancer au moins `make bandit gitleaks pip-audit` en plus des tests scope, OU retirer « gates verts » du message quand seul un sous-ensemble a tourné [plus haute valeur — ferme le mensonge] ; (c) `git add` scopé (dossier scope + ses docs/state) ; (d) breakers consécutif-rouge + no-diff ; (a) `git worktree add` par scope (ou lock de concurrence). PR relue (faire la boucle se réparer elle-même = circulaire).
- **Fichiers** : `ralph/loop.sh`, `ralph/README.md`/`ORCHESTRATION.md` (réconcilier doc↔code) · **Agent** : `gsd-executor` + revue humaine.

### M14 · Docs audit-reality certifient « ✅ conforme » un gate mort / Fabric non câblé *(NOUVEAU ; SAFE_INLINE)*
- **Scope** security-governance (transverse, `code:[]` → **hors gate path**) · **Routing** **SAFE_INLINE** · **Effort** S · **Value** HIGH (honnêteté)
- **Problème** : (a) `docs/audit-reality/rag-prompts.md:49,51,52` classe le gate RAGAS ✅ CONFORME et `:27` « 0 écart majeur » — alors qu'il ne démarre pas (M2). Cause méthodo `:7` « aucune exécution… (contrainte) » : certifié en **lisant** le Makefile, jamais en l'exécutant. (b) `docs/audit-reality/access-gateway.md` n'a **aucune** entrée Fabric, masquant le gap M3 ; `FABRIC.md:5-7,163-173,228` présente l'ACL Fabric comme filtre actif « déjà en place ».
- **Plan** : downgrader les ✅ concernés en ❌/⚠️ avec la preuve (ImportError reproduit / Fabric non câblé) ; corriger les synthèses « aucun mock présenté comme réel » ; ajouter une règle de méthode (README audit-reality) : une claim d'exécution runtime ne peut être au mieux que « ❔ NON VÉRIFIABLE » sans transcript de run attaché.
- **Fichiers** : `docs/audit-reality/rag-prompts.md`, `docs/audit-reality/access-gateway.md`, `docs/connectors/FABRIC.md`, `docs/scopes/access-gateway.md`, `docs/audit-reality/README.md`
- **Note** : pur doc d'honnêteté, hors gate de scope → **je peux le faire ici** si tu valides.

### M15 · Métriques+alertes pour échecs ACL-sync & rupture d'audit (silencieux) *(= OBS-03/05 sharpened)*
- **Scope** monitoring + access-gateway + actions · **Routing** ROUTE_TO_RALPH · **Effort** M · **Value** HIGH
- **Problème** : les 2 modes d'échec les plus centraux — ACL périmée (sync Graph/**Fabric** échoué en silence) et chaîne d'audit rompue — n'émettent **aucune métrique** ni alerte. `onix_acl_sync_failures_total` absent du code ; `verify_chain()` jamais exposé en gauge.
- **Plan** : émettre `onix_acl_sync_failures_total{source=graph|group|doc_acl|fabric}` (sans PII) à chaque échec de refresh ; gauge `onix_audit_chain_ok` piloté par `verify_chain()` ; règles `increase(...[5m])>0 for 5m` (critical) + `onix_audit_chain_ok==0` ; alerte WAL/disque Postgres ciblée (custom query postgres-exporter).
- **Agent** : `gsd-executor` (multi-scope → coordonner) · **Skills** : `update-scope-docs` ×3.

### M16 · Pas de sonde liveness black-box ni d'alerte dédiée pour la passerelle
- **Scope** monitoring · **Routing** SAFE_INLINE (2 édits YAML, mais scope monitoring → doc triad) · **Effort** S · **Value** MEDIUM
- **Problème** : `prometheus.yml` blackbox sonde actions+nginx mais **pas** `access-gateway:8200/health` ; pas de `GatewayServiceDown` (alors qu'`ActionsServiceDown` existe). Le point d'enforcement RBAC a le signal le plus faible (TargetDown 2m off-/metrics).
- **Plan** : ajouter la cible blackbox gateway + alerte `GatewayServiceDown` (probe_success==0, for 1m, critical) miroir d'`ActionsServiceDown`.
- **Agent** : `gsd-executor` · **Skills** : `update-scope-docs` (monitoring).

---

## P2 — durcissement / défense-en-profondeur

### M8 · Doc-ACL fail-OUVERT pour items sans id reconnu
- **Scope** access-gateway · **Routing** ROUTE_TO_RALPH · **Value** MEDIUM (trou de conception, **pas** une fuite live sur Onyx 4.1.1)
- **Problème** : `doc_acl.py:258-261` garde tout item dont l'id n'est pas extractible (`_ID_FIELDS` = 4 clés, `:232`) — contredit le deny-by-default. Mais les formes Onyx 4.1.1 connues portent `document_id` (streaming.py:29) → branche fail-open non atteinte par les formes connues.
- **Plan** : sous `default_policy='deny'`, DROP les items sans id (audit `reason='no_doc_id'`) au moins pour citations + top_documents ; MAJ `test_doc_acl.py:195-205` (drop sous deny) ; vérifier la couverture `citation_info` 4.1.1.

### M9 · Image de base non digest-pinnée malgré commentaire « ÉPINGLÉE »
- **Scope** deploy-ops · **Routing** PR_BRANCH · **Value** MEDIUM (honnêteté + reproductibilité)
- **Problème** : `actions/Dockerfile:7` `FROM …slim-bookworm` (tag mutable) mais `:3` dit « ÉPINGLÉE » (idem access-gateway/Dockerfile, DEPLOY_PROD.md:414). SBOM = d'une base mouvante.
- **Plan** : pin `@sha256:<digest>` (les 2 Dockerfiles) + corriger les commentaires ; **documenter honnêtement** que `apt-get upgrade`/`pip --upgrade` (CVE-remédiation délibérée) réintroduisent une non-repro → ne pas prétendre repro totale. Résolution du digest = accès registre (PR).

### M10 · Fabric OneLake : principal non re-validé (fail-OUVERT preview)
- **Scope** access-gateway · **Routing** PR_BRANCH · **Value** MEDIUM
- **Problème** : `_onelake_access_grants_read` (`fabric_acl.py:132-163`) renvoie True sur tout signal read **sans** vérifier que l'enregistrement concerne bien le principal demandé (filtre `?principalId=` côté serveur d'un endpoint **preview**). Si le filtre régresse → grant principal A sur l'accès de B. Source (b) n'élargit que (gold-read-only) → impact réel conditionnel.
- **Plan** : exiger un champ `principalId/objectId` correspondant (normalisé) avant d'honorer un signal read ; sinon fail-closed. Test offline : body lié à un AUTRE principal → `can_principal_read` False. Adoucir les commentaires « filtre côté service ».

---

## Candidats non-vérifiés (la boucle a calé avant Verify) — à traiter en priorité Cycle 2
- **M7** (gateway trusted-header) — ✅ **vérifié** par l'orchestrateur (2026-06-21) → reclassé P1 défense-en-profondeur (non-P0 : exposition gated réseau, edge déjà durci).
- `obs-acl-audit-silent-failure` → fusionné dans **M15**.
- `fabric-acl-unwired-runtime` + `fabric-doc-overclaims` → fusionnés dans **M3** + **M14**.
- `ci-pin-actions-by-sha` → fusionné dans **M12** ; `ci-cosign-sign-and-attest` → = CICD-01 (Ralph).
- `auditreality-ragprompts-false-conforme` → fusionné dans **M14**.

## Rejeté (vérifié, non retenu)
- `ragas-antiregression-baseline-is-scripted-placeholder` — déjà documenté honnêtement (RAG_EVAL.md, baseline `_comment`) ; absorbé par M2.

---

## Ce que je peux exécuter ICI, sans risque (hors-scope Ralph, sans Docker)
1. **M5(a)** — corriger le mensonge `deploy-ops.md:58` (pg_dump).
2. **M12** — SHA-pin des actions + corriger le faux « SBOM attaché à la release » + checksum gitleaks + Dependabot (`.github/` hors gate scope).
3. **M14** — corriger les fausses « ✅ conforme » audit-reality (RAGAS + Fabric) + règle de méthode.

Tout le reste touche un scope Ralph (→ Ralph) ou exige Docker/tenant pour la preuve.

---
*Programme Cycle 1 — chief-orchestrator. Méta & améliorations Cycle 2 : voir `ORCHESTRATOR-LOG.md`.*
