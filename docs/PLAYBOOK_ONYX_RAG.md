# Playbook Onyx — appliquer l'optimisation RAG (corpus français)

> **But** : transformer les recommandations de [`RAG_OPTIMIZATION.md`](RAG_OPTIMIZATION.md)
> en **gestes concrets** sur votre déploiement. Ce qui est **déjà câblé repo-side**
> (contexte `num_ctx`, température, tuning Ollama) est rappelé ; ce qui reste
> **runtime/Onyx-admin** (embedder FR, reranker, analyseur BM25, `MAX_CHUNKS`)
> est détaillé pas-à-pas, avec une **procédure de RÉ-INDEX UNIQUE**.
>
> ⏱️ ~30 min d'actions + le temps de ré-indexation (selon volume). **Zéro code.**

---

## 0. Avant de commencer

1. **Sauvegardez** : `make backup` (Postgres + index + MinIO).
2. **Mesurez l'état initial** (pour comparer après) :
   - `make rag-eval` (harnais RAGAS local, cf. [`RAG_EVAL.md`](RAG_EVAL.md)) → note faithfulness / context-precision / answer-relevancy.
   - Si la passerelle tourne : relevez `onix_gateway_answer_without_citation_total` et le P95 sur `/metrics` (cf. [`OBSERVABILITY.md`](OBSERVABILITY.md)).
3. **La règle d'or** : changer **l'embedder** *ou* **l'analyseur BM25** impose un
   **ré-index**. On les change donc **ENSEMBLE, une seule fois** (§4).

---

## 1. Contexte LLM (`num_ctx`) — ✅ déjà câblé, à vérifier

Le défaut Ollama (4096) tronque le contexte ; la stack le corrige déjà :
- `make tune` écrit `OLLAMA_CONTEXT_LENGTH` dans `.env` (8192 ≤3B / 12288 7-8B / 16384 GPU) ;
- `make models` grave `num_ctx` + `temperature 0.2` dans chaque modèle de chat (Modelfile) ;
- en Helm : `deploy/k8s/onix-ha/values.yaml → ollama.tuning.contextLength`.

**À faire** : juste vérifier.
```bash
grep OLLAMA_CONTEXT_LENGTH .env           # doit être >= 8192
docker compose exec ollama ollama show qwen2.5:7b-instruct | grep -i context
```
Pour pousser un 7B à **12288** sur une machine à RAM confortable : mettez
`OLLAMA_CONTEXT_LENGTH=12288` dans `.env`, `make up` (redémarre Ollama).
Rappel mémoire : **KV ∝ `OLLAMA_CONTEXT_LENGTH × OLLAMA_NUM_PARALLEL`** (q8_0 ~/2).
Mono-utilisateur = gardez `OLLAMA_NUM_PARALLEL=1` pour le contexte maximal par requête.

---

## 2. `MAX_CHUNKS_FED_TO_CHAT` — limiter les chunks injectés (sans ré-index)

Onyx injecte par défaut **25** chunks × 512 tok ≈ **12,8k tok** → sature le
contexte. Avec le reranker (§5), **8 suffisent** et la qualité monte.

Dans `.env` (transmis à Onyx via `env_file`) :
```dotenv
MAX_CHUNKS_FED_TO_CHAT=8
```
Puis : `docker compose up -d api_server background` (prise en compte au redémarrage).
**Pas de ré-index.**

---

## 3. Reranker (cross-encoder) — précision (sans ré-index)

Récupérer large (lexical+vectoriel) puis **re-classer finement** = le meilleur
rapport qualité/effort. Le reranker est **OFF** par défaut dans Onyx.

**Onyx → Admin Panel → Search Settings** :
1. Activez le **Reranking**.
2. Modèle : `BAAI/bge-reranker-v2-m3` (multilingue, FR fort). Sur poste 16 Go
   contraint : `mxbai-rerank-xsmall-v1` (plus léger).
3. Récupération : **30–50** candidats → **top-n = 8** renvoyés (cohérent avec §2).

Le reranker tourne sur le **model-server Onyx** (pas Ollama). **Pas de ré-index** :
effet immédiat sur les nouvelles requêtes. Coût : un peu de latence/CPU au requêtage.

---

## 4. Embedder FR + analyseur BM25 — LE ré-index unique

C'est l'étape à fort impact pour un corpus **français** (l'embedder par défaut
`nomic-embed-text-v1` est **anglophone**, et l'analyseur lexical est **`english`**).
On change les **deux ensemble** pour ne ré-indexer **qu'une fois**.

### 4.a — Analyseur BM25 en français (env, AVANT le ré-index)
Dans `.env` :
```dotenv
OPENSEARCH_TEXT_ANALYZER=french
```
Puis redémarrez l'indexation pour qu'elle prenne l'analyseur :
`docker compose up -d background api_server`.

### 4.b — Embedder FR multilingue (déclenche le ré-index)
**Onyx → Admin Panel → Search Settings → Embedding Model** :
1. Choisissez un modèle **self-hosted** (souverain, tourne sur le model-server) :
   - `intfloat/multilingual-e5-large` (1024 dim) — robuste, multilingue ; **ou**
   - `BAAI/bge-m3` (1024 dim) — excellent FR, hybride dense+sparse.
2. Validez. Onyx construit un **index secondaire** en arrière-plan (ré-embedding
   de tout le corpus) **puis bascule** dessus → **zéro coupure** côté utilisateurs.
   Ce nouvel index hérite de l'analyseur `french` posé en 4.a.

### 4.c — Suivre le ré-index
**Admin Panel → Indexing / Document Sets** : la ré-indexation passe à 100 %. Volume
indicatif : ~quelques minutes / millier de documents (dépend du model-server et du
CPU/GPU). Tant qu'il tourne, l'ancien index sert les requêtes (continuité).

> ⚠️ **Embedding via Ollama ?** Non : Onyx **n'utilise pas** Ollama pour les
> embeddings (model-server dédié). Inutile d'y configurer un modèle d'embedding.
> `nomic-embed-text` côté Ollama ne sert qu'aux usages natifs (juge RAGAS, outils).

---

## 5. Modèle de génération + température — ✅ déjà câblé

`make tune` retient `qwen2.5:7b-instruct` (FR fort) selon la RAM ; `make models`
grave `temperature 0.2` (factuel/stable pour du RAG sourcé). En GPU/≥32 Go,
préférez la quantification **Q5_K_M** (cf. [`PERFORMANCE.md`](PERFORMANCE.md) §2bis).
Réglage du LLM dans **Onyx → Admin → LLM** (le provider Ollama est déjà câblé,
cf. [`RUNBOOK.md`](RUNBOOK.md)).

---

## 6. Fraîcheur du connecteur (SharePoint)

Pour un portefeuille qui bouge, réduisez l'intervalle de **prune/re-sync** du
connecteur (Onyx → Admin → Connectors) de **30 j** vers **1–3 j** afin que les
documents retirés/déplacés disparaissent vite de l'index (cf.
[`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md)).

---

## 7. Valider le gain (avant/après)

1. `make rag-eval` → les 3 métriques doivent **monter** (cibles : faithfulness
   ≥ 0.90, context-precision ≥ 0.70, answer-relevancy ≥ 0.85).
2. Sur `/metrics` (passerelle) : `onix_gateway_answer_with_citation_total` en
   hausse, `…_no_context_total` en baisse, P95 stable.
3. Test métier : quelques questions FR réelles → réponses **plus précises, mieux
   sourcées**, sans troncature.

---

## 8. Récapitulatif (ordre conseillé)

| Étape | Action | Ré-index ? | Où |
|---|---|---|---|
| 1 | Vérifier `OLLAMA_CONTEXT_LENGTH` (déjà câblé) | non | `.env` / `ollama show` |
| 2 | `MAX_CHUNKS_FED_TO_CHAT=8` | non | `.env` |
| 3 | Activer reranker `bge-reranker-v2-m3`, top-n 8 | non | Onyx Search Settings |
| 4a | `OPENSEARCH_TEXT_ANALYZER=french` | (préparation) | `.env` |
| 4b | Embedder `multilingual-e5-large`/`bge-m3` | **OUI (unique)** | Onyx Search Settings |
| 5 | Modèle/température (déjà câblé) | non | `make tune`/`make models` |
| 6 | Prune connecteur 1–3 j | non | Onyx Connectors |

> **Rollback** : Onyx conserve l'index précédent jusqu'à bascule réussie ; en cas
> de souci, re-sélectionnez l'ancien embedder (nouveau ré-index) et retirez les
> variables `.env` ajoutées. Sauvegarde `make backup` disponible (§0).
