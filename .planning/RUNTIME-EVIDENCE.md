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
*Preuves collectées sur VM jetable (az run-command). VM **désallouée** après lecture (modèles gemma3+embeddinggemma conservés sur disque ; `az vm start` pour re-tester, `az group delete -n onix-test-rg` pour détruire).*
