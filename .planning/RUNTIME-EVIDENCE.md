# Runtime Evidence — pile complète bootée sur Azure (2026-06-21)

**Environnement (jetable)** : VM Azure **Standard_D16as_v5** (16 vCPU / 64 Go), Ubuntu 22.04, **France Central**, sub `IA GEREP`. Pile `docker compose -f docker-compose.yml -f docker-compose.prod-local.yml` (`onyx-backend:4.1.1`), modèles `nomic-embed-text` + `qwen2.5:14b-instruct`. Pilotée par `az vm run-command` (aucun port ouvert). **Première fois qu'onix est réellement bootée de bout en bout** (impossible en local : Docker verrouillé sans admin).

## Verdicts runtime — ce que seul le live révèle

| # | Constat | Verdict | Preuve |
|---|---------|---------|--------|
| 1 | **Boot complet ordonné** | ✅ | 11/11 conteneurs `Up (healthy)` ; `make verify` = **25 OK / 0 échec** ; healthchecks + `depends_on: service_healthy` convergent dans l'ordre → **HARD-04 prouvé au runtime** (l'audit le notait « non vérifié ») |
| 2 | **Backup → restore round-trip** | ✅ | `make backup` (31 s, stop→tar→restart) ; `make restore` → Postgres **3 bases intactes**, `verify` vert post-restore. **Réfute le pire de BKP-02** (« cluster corrompu ») : le tar est froid mais sur une pile *arrêtée* = cohérent. BKP-01 **prouvé**. |
| 3 | **Front HTTP sous charge** | ✅ | 200/200 requêtes `HTTP 200` sur `:3000` (20 //), `/nginx-health` en **0,5 ms** |
| 4 | **RAM** | ✅ | 50 Gi libres sous charge — la 64 Go est **sur-dimensionnée RAM** ; la RAM n'est jamais la contrainte |
| 5 | **Plafond débit LLM** | ⚠️ **LIMITE #1** | `qwen2.5:14b` en CPU 16 cœurs : **~0,1 req/s**, latence **14–60 s/réponse**, la concurrence n'aide PAS (Ollama sérialise). 1→8 gens : tokens ×8 mais temps ×4. → **GPU obligatoire pour du multi-utilisateur** (sinon ~7 réponses RAG/min max) |
| 6 | **Résilience `restart: always`** | ⚠️ **TROU RÉEL** | `api_server` tué **pendant son redémarrage** (juste après le backup) : resté `exited` (code 137, **restarts=0**) — `restart: always` n'a **pas** rattrapé ; reprise seulement après `docker start` manuel (→ `running/healthy` en 25 s). Fenêtre de vulnérabilité au démarrage sur le service le plus critique. |
| 7 | **Édition Onyx** | 🔍 nuance | Image **EE-capable** (`/app/ee/` présent, module EE chargé au boot) mais **non licenciée** (`LICENSE_ENFORCEMENT_ENABLED` défaut `true`, aucune clé) → fonctions payantes **license-gated**. « On déploie l'image FOSS » est **imprécis** (c'est l'image EE en mode non-licencié), mais l'effet pratique ≈ FOSS **confirme** le récit FOSS-vs-EE de l'audit (dont M20-F1). |

## Impact sur le verdict production ([PROD-READINESS.md](PROD-READINESS.md))
- **Dim 1 (Fonctionnalités up)** 🟡 → la pile **boote saine et sert** (UI 200, Ollama câblé). Reste non prouvé : une **vraie requête RAG E2E** (retrieval+réponse+citation) et la qualité (RAGAS baseline réelle).
- **Dim 4 (Fiabilité)** : backup/restore **prouvé sain** (réfute le pire) ; MAIS **résilience restart edge-case** (#6) + cold-tar/downtime/non-chiffré/no-WAL restent → toujours pas GO, mais le tableau s'éclaircit.
- **Inchangé (non testé runtime)** : sécurité M1/M3/M7, observabilité M4 (alertes no-op), compliance M20, supply-chain `pip-audit` ROUGE — ce sont des défauts de *code/config*, pas révélés par le boot.

## Limite #1 à retenir pour la prod
Le **LLM en CPU est le goulot** : `qwen2.5:14b` ≈ 0,1 req/s. Pour un go-live multi-utilisateur → **GPU** (VM N-series) ou un modèle plus petit + budget de latence assumé. La RAM/IO/HTTP ne sont PAS les limites ; le calcul d'inférence l'est.

---

## RAG E2E — investigation live de bout en bout (2026-06-21, suite)

**Méthode** : pile complète sur la VM, pilotée par `az run-command` (localhost VM uniquement, **aucun port ouvert**). User admin jetable + doc de test synthétique à **token unique** (`ZQX7731ONIXE2E`, garantie fictive « Zogary Prévoyance », risque 4242). Contrats d'API extraits du **code du conteneur** (pas de devinette). **Nettoyage systématique** (doc + user + liens FK) après chaque essai ; « zéro mock présenté comme réel ».

| # | Constat | Verdict | Preuve |
|---|---------|---------|--------|
| 8 | **Reboot → auto-start complet** | ✅ | `az vm restart` → `docker.service` + `onix-ui-forward` (socat) reviennent **enabled/active** ; 11/11 conteneurs `Up`, `make verify` **25 OK / 0 échec**. Le mode de panne prod réaliste (patching/maintenance hôte) **récupère seul** → nuance favorablement le trou #6 (kill ciblé pendant l'init = course étroite, pas le cas courant). |
| 9 | **LLM Provider Onyx NON seedé au déploiement** | 🔴 **BLOQUANT (config)** | Table `llm_provider` **vide** après `make up-local-prod` + `make models` → chat échoue **instantanément** : `ValueError: No default LLM model found` → `"Message ID is required"`. `make models` pull le modèle Ollama mais **ne crée pas** la ligne provider Onyx (faite normalement à la main dans l'UI admin → LLM). **Un déploiement neuf a le chat mort tant qu'un admin ne configure pas le provider.** Corrigé live via `PUT /admin/llm/provider` (ollama, `http://ollama:11434`) + `POST /admin/llm/default` (provider laissé en place). |
| 10 | **Ollama OOM-killé en génération (14B)** | 🔴 **BLOQUANT (tune)** → corrigé | Sur un **vrai** prompt RAG, llama-server reçoit **SIGKILL** (`signal: killed`), client = `OllamaException - "unexpected EOF"`, `/api/generate` → 500. Cause : **`OLLAMA_MEM_LIMIT=12g`** (réglé par `make tune`) < empreinte réelle qwen2.5:14b ≈ **9 Go modèle + 3,3 Go KV(q8_0) + 8 Go prompt-cache ≈ 20 Go**. Le stress-test direct (petit prompt) le **masquait**. **Fix prouvé** : `OLLAMA_MEM_LIMIT=40g` + `OLLAMA_NUM_PARALLEL=1` → génération réaliste **HTTP 200 en 28 s, eval_count 58, 0 crash**. ⚠️ `make tune` doit dimensionner `OLLAMA_MEM_LIMIT` au modèle (un 14B ≠ 12 Go). |
| 11 | **Pipeline retrieval (ingest→embed→index→search)** | ✅ **PROUVÉ** | `POST /onyx-api/ingestion` 200 → **OpenSearch direct (https+auth admin)** : index `danswer_chunk_nomic_ai_nomic_embed_text_v1` **docs.count=1**, chunk embeddé (nomic) `_id ...__512__0`, token retrouvé. **`POST /admin/search` Onyx** : **1 document** retrouvé (ONIX-E2E-TESTDOC). Le cœur RAG **fonctionne**. *(Note : mes « docs.count=0 » antérieurs = sondes `http://os:9200` non-authentifiées — OpenSearch tourne en **https+auth**, elles tapaient dans le vide.)* |
| 12 | **Chat RAG agentique avec qwen2.5:14b** | 🔴 **BLOQUANT (modèle)** | `send-chat-message` répond **200, error_msg null**, mais `top_documents:[]` et réponse = **JSON d'appel d'outil halluciné en texte brut** (`{"name":"open_url",...}` puis `{"name":"add_memory",...}` — outil même pas disponible). **Reproduit même persona réduite à `internal_search` seul** (5 autres outils retirés puis restaurés). → **qwen2.5:14b ne pilote pas le tool-calling d'Onyx** : il n'invoque jamais `internal_search`, donc la recherche n'est pas déclenchée en chat → réponses **non-sourcées**. Le pipeline marche (#11) mais le **modèle est trop faible pour le flux agentique** → il faut un modèle à function-calling natif robuste (et/ou GPU pour un modèle plus capable), ou une persona RAG non-agentique. |

### Synthèse RAG
- **Ce qui marche** : boot, retrieval E2E (embed/index/search), génération LLM (après fix OOM), infra chat (sessions, API).
- **Ce qui bloque le produit chat** : (9) provider à configurer au déploiement, (10) `make tune` sous-dimensionne la RAM Ollama pour un 14B, (12) **qwen2.5:14b inapte au tool-calling agentique d'Onyx** → pas de réponse sourcée en chat. (10) corrigé, (9) configuré ; **(12) est le vrai mur** : sans modèle plus capable / function-calling fiable (→ GPU), le chat RAG **hallucine au lieu de citer**.
- **Limite #1 confirmée et aggravée** : le LLM local n'est pas qu'un goulot de débit (#5) — sur 14B CPU il **OOM** (#10, corrigeable) **et** est **trop faible pour l'agentique RAG** (#12, non corrigeable sans changer de modèle/GPU).

### État laissé sur la VM (jetable)
Bénéfique et transparent : provider Ollama Onyx **créé** (id=1, défaut) ; `.env` patché `OLLAMA_MEM_LIMIT=40g`/`NUM_PARALLEL=1` + conteneur ollama recréé ; persona 0 **restaurée** à l'identique ; **tous** les users/docs de test **supprimés** (FK comprises). Le chat UI **génère** désormais (mais reste non-sourcé, cf. #12).

---

## Cycle 3 — #12 RÉSOLU : stopgap RAG non-agentique, **prouvé live** (2026-06-22)

**Le mur #12 (chat RAG non-sourcé sur CPU) est levé** sans GPU. Cause racine, lue dans le code Onyx 4.1.1 puis prouvée au runtime :

| Constat | Verdict | Preuve |
|---|---------|--------|
| **#12 cause** : Onyx 4.x est **agentique** (le LLM *décide* d'appeler `internal_search` via `llm_loop.py`). qwen2.5:14b CPU **rate ce choix** (hallucine un appel d'outil en texte). | 🔍 | `process_message.py` / `llm_loop.py:707-713` ; aucun flag « non-agentique » natif. |
| **Levier** : poser `forced_tool_id` + `allowed_tool_ids` = outil `internal_search` ⇒ `tool_choice=REQUIRED` ⇒ Onyx **exécute lui-même** la recherche, indépendamment du modèle. | ✅ | `llm_loop.py:709` ; pré-requis `SearchTool.is_available()` = au moins **un connecteur réel** (`check_connectors_exist`, `connector.id>0`) — **toujours vrai en prod** (SharePoint/Fabric). |
| **Modèle** : `gemma3:12b` répond **à partir du contexte récupéré** avec citations (là où qwen crachait du JSON d'outil). | ✅ **PROUVÉ LIVE** | chat `200` en 320 s (CPU) ; `top_documents=1` (score 1.0), `citation_info=1`, `error=None` ; **GROUNDED token `ZQX7731ONIXE2E` ✅ + risque `4242` ✅**. |

**Réponse réelle gemma3** : *« Selon le document 'ONIX-E2E-TESTDOC', le code de validation de bout en bout onix est **ZQX7731ONIXE2E [[1]]**. La garantie fictive Zogary Prevoyance couvre le risque imaginaire **numéro 4242 [[1]]**. »* → vraie réponse **sourcée + citée**.

### Codification (landée)
- **access-gateway** : `onyx_proxy.force_internal_search()` injecte `forced_tool_id`+`allowed_tool_ids` dans le payload chat relayé (stream + non-stream), réglage `GATEWAY_FORCE_INTERNAL_SEARCH` (défaut **ON**) + `GATEWAY_FORCE_SEARCH_TOOL_ID` (défaut 1). 4 tests offline. À désactiver quand un modèle à function-calling fiable (GPU) est déployé → agentique natif.
- **Recommandation modèle** : `gemma3:12b` (chat) + `embeddinggemma` (embeddings) — pull validé sur VM. *(Câblage `detect-hardware`/`make models` = follow-up deploy-ops ciblé, non fait ce tour pour ne pas re-toucher la logique d'empreinte #10 fraîchement landée.)*

### Findings secondaires (à tracer, hors stopgap)
- **API-compat gateway↔Onyx 4.1.1** : la gateway force le périmètre via `retrieval_options.filters.document_set` (ancien schéma), mais `SendMessageRequest` 4.1.1 attend `internal_search_filters` (`BaseFilters`) → le forçage de Document Set pourrait être **ignoré** par Onyx 4.1.1. **À vérifier/réconcilier** (impact RBAC réel).
- **Cosmétique** : gemma3 enveloppe sa réponse dans `{"result": "..."}` → à déballer côté gateway (non bloquant).

---

## Cycle 4 — E2E Docker **LIVE de mes correctifs onix**, par scope (2026-06-22)

**Méthode** : la branche `prod/cycle1-securite` **déployée sur la VM** (`git fetch` + checkout `00b46e7`), **image `actions` rebuildée** avec mon code (M1/HARD-03), **gateway buildée+lancée** sur `onix-net`. Chaque scope testé de bout en bout sur la **pile réelle** (gemma3 par défaut).

| Scope | Fonctionnalité | Preuve LIVE |
|---|---|---|
| **actions** | **M1** audit anti-downgrade | `verify_chain` (code déployé) : chaîne HMAC normale `ok=True` ; après attaque downgrade keyless (`UPDATE … algo='sha256'`) → **`ok=False`, reason « algo downgrade détecté »** → `M1_LIVE: PASS` |
| **actions** | **HARD-03** préflight clé d'audit | Clé vidée + recréation `actions` → **`Restarting (3)`** (crash-loop), logs `RuntimeError: … Refus de démarrer (fail-closed, HARD-03)`. Clé restaurée → `Up (healthy)`, `/health=200`. |
| **rag-prompts** | **#12** RAG sourcé+cité (gemma3) | register/connecteur/ingestion → chat **200** (313 s), `TOP_DOCUMENTS=1`, `CITATIONS=1`, **GROUNDED `ZQX7731`+`4242` = True** → réponse « …code **ZQX7731ONIXE2E [[1]]**…risque **4242 [[1]]** ». |
| **access-gateway** | **M7** anti-spoof X-OIDC | `/v1/authorized-document-sets` SANS `X-OIDC-Proxy-Secret` → **401** ; mauvais secret → **401** ; bon secret → **200**. Usurpation par accès direct **bloquée**. |
| **access-gateway** | **RBAC** groupe→Document Set | claims groupe `TESTGROUP` (mappé) → `authorized_document_sets:["clients-test"]` ; groupe non mappé → **périmètre vide** (deny-by-default). |
| **deploy-ops** | intégration (gemma3 défaut) | `make verify` = **25 OK / 0 échec** — « Stack saine » sur boot frais. |
| **monitoring** | **M4** alertes fail-closed | `alertmanager/entrypoint.sh` sans `ALERT_WEBHOOK_URL` → **exit 1** + 3 `CRITICAL` (« REFUS de démarrer… alertes sans destination… Fail-closed »). Fini le no-op. |

**Verdict** : les correctifs des 5 scopes (actions M1/HARD-03, rag #12, gateway M7/RBAC, deploy-ops, monitoring M4) sont **prouvés au runtime sur la pile réelle**, en plus des suites offline (actions 92 · gateway 350 · deploy-ops 6+3 verts).

### RAGAS — vrai run de référence avec juge ≥7B (gemma3:12b, CPU)
Première mesure RAGAS **LIVE avec un vrai juge ≥7B** (et non le seed scripté déterministe), sur `golden_fr.json`, conteneur détaché sur `onix-net` :

| Métrique | Seed scripté (committé) | **gemma3:12b (réel)** | Seuil nightly |
|---|---|---|---|
| faithfulness | 0.750 | **0.750** | 0.55 ✅ |
| context_precision | 0.875 | **0.375** | 0.55 ❌ |
| answer_relevancy | 1.000 | **0.9375** | 0.70 ✅ |

**Signal honnête** : un vrai juge strict donne **context_precision = 0.375 < 0.55** (le seuil absolu du nightly). Deux causes mêlées : (a) le golden set inclut **2 items volontairement dégradés** (G07 halluciné, G08 hors-sujet) qui tirent context_precision vers le bas par construction ; (b) gemma3 juge **plus strictement** que le seed (0.875). → À trancher : ajuster le seuil pour un juge strict, **ou** améliorer la précision de récupération (re-ranking / chunking), **ou** sortir les 2 items dégradés de l'agrégat. **Le baseline committé reste le seed scripté** (le test offline `test_baseline_is_reproducible_from_scripted_judge` exige la reproductibilité byte-level) ; pour adopter le baseline gemma3, lancer le nightly sur **runner self-hosted + gemma3** et rafraîchir baseline **ET** valeurs attendues du test offline.

### SEC-01 — ACL **Fabric / SharePoint LIVE** contre le tenant réel GEREP (2026-06-22)

Test E2E des ACL par-document de la gateway contre **Microsoft Fabric + Entra réels** (tenant GEREP `f7d2b917…`, token `az` de `a.zeriri@gerep.fr`, **read-only strict** — `FabricClient` est GET-only par conception).

| Cible | Résultat LIVE | Preuve |
|---|---|---|
| **Fabric ACL (M3/SEC-01)** | ✅ **PASS E2E** | Le **`FabricClient` de la gateway** (auth az) appelle l'**API Fabric réelle** `GET /v1/workspaces/{Test}/roleAssignments` → 2 rôles réels (Dataviz.Gerep SP + Adel ZERIRI Admin). `fabric_acl.principal_has_read_role` décide : **GRANT** Adel (Admin) ✅, **DENY** oid inconnu ✅ (deny-by-default), **GRANT** via appartenance groupe ✅. |
| **Résolution groupes Graph (identité)** | ✅ **LIVE** | `transitiveMemberOf` (Graph) résout **10 groupes Entra GEREP réels** (GRP-SEC-VPN-BST, Service IARD, Power Platform Administrator…) → le chemin d'identité de la gateway fonctionne contre l'annuaire réel. |
| **SharePoint par-document (graph_acl)** | ✅ **PASS E2E** | Après élévation admin : app dédiée **`onix-sec01-sp-test`** (`bb6bde73…`, Graph `Sites.Read.All` admin-consentie) → le code **`graph_acl.fetch_item_principals` de la gateway** appelle l'**API Graph réelle** sur le **vrai document POC** `Dossiers_Clients_POC/Client Alpha` (site `dev-assistant-client-360`) → ensemble autorisé réel (**1 user + 5 groupes**, dont `onix-test-CLIENT-ALPHA` / `onix-test-MANAGER`). Décision gateway : **GRANT** membre `onix-test-CLIENT-ALPHA` ✅, **GRANT** `onix-test-MANAGER` ✅, **GRANT** Adel (owner) ✅, **DENY** sans groupe autorisé ✅. **RBAC par-dossier-client prouvé contre le vrai SharePoint GEREP.** |

**Note app de test** : `onix-sec01-sp-test` (`bb6bde73…`) a `Sites.Read.All` **tenant-wide** (admin-consentie) + un secret client — **conservée** (présentation, peut alimenter la démo gateway). Moindre-privilège recommandé ensuite : scoper à `Sites.Selected` sur le seul site POC (le grant `POST /sites/{id}/permissions` exige `Sites.FullControl.All` = action SharePoint admin), ou supprimer l'app après la démo.

**Révocation** (grant→deny après retrait de rôle/permission) : **non jouée** — exigerait une **écriture** (modifier un roleAssignment Fabric ou une permission SharePoint réelle GEREP), proscrite par la posture read-only. À tester sur un item jetable dédié.

---
*Preuves collectées sur VM jetable (az run-command) + tenant GEREP réel (read-only). VM **désallouée** après lecture (branche `prod/cycle1-securite` + modèles gemma3 conservés sur disque ; `az vm start` pour re-tester, `az group delete -n onix-test-rg` pour détruire).*
