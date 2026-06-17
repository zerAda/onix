# Optimisation RAG & Ollama — audit de consultant (onix entreprise)

> Audit en profondeur (4 angles : retrieval/embeddings, génération/`num_ctx`/reranker/éval,
> inférence Ollama **mesurée**, scale/observabilité). Vérité-terrain : tag Onyx **v4.1.1**
> (valeurs citées du code) + **mesures réelles** sur 4 vCPU / 15,7 Go / sans GPU.
> Objectif : maximiser la **qualité RAG** sur corpus **français** et la **capacité** entreprise.

## Verdict
Infra/sécurité/HA = excellentes. Le **RAG tourne avec les défauts d'Onyx, non adaptés au FR**, et un `num_ctx` non maîtrisé. **~80 % du gain = réglages** (Onyx Search Settings + provider LLM) — pas du code.

## État des lieux (code v4.1.1)
| Sujet | Défaut réel (preuve) | Conséquence FR |
|---|---|---|
| Embedding | `nomic-ai/nomic-embed-text-v1` 768d **anglo**, dans le **model-server Onyx** (`model_configs.py:15`) | recall FR dégradé |
| `nomic-embed-text` tiré dans Ollama | **IGNORÉ** par le retrieval (Ollama = chat only ; issue #6189 *not planned*) | dépendance inutile |
| `num_ctx` | non défini → défaut Ollama **4096** (trop court). Mesuré : prompt 3000 tok → **2035 ingérés** (défaut + `NUM_PARALLEL=2`) | **troncature silencieuse** |
| Reranker | **OFF** (`DEFAULT_CROSS_ENCODER_MODEL_NAME=None`) | précision sous-optimale |
| Analyseur BM25 | **global `english`** (`OPENSEARCH_TEXT_ANALYZER`) | pas de stemming FR |
| Chunks → LLM | `MAX_CHUNKS_FED_TO_CHAT=25`, chunk 512/overlap 0, multipass OFF en CPU | 25×512≈12,8k tok : sature `num_ctx` |
| Hybride | α=0.5 ; `NUM_RETURNED_HITS=50` | OK, à calibrer |

## Deux interactions critiques (pièges)
1. **`num_ctx` × `MAX_CHUNKS_FED_TO_CHAT`** : prompt 6-7k + 25×512≈12,8k ≈ **19k tok** → intenable en CPU. Solution : **reranker → `MAX_CHUNKS_FED_TO_CHAT=8`** (≈4k) **+ `num_ctx=12288`**.
2. **`num_ctx` × `NUM_PARALLEL`** : fixer **`OLLAMA_CONTEXT_LENGTH` explicitement** (défaut 4096 = troncature). La **RAM KV croît avec `OLLAMA_CONTEXT_LENGTH × NUM_PARALLEL`** ([Ollama FAQ](https://docs.ollama.com/faq) : « NUM_PARALLEL scales RAM requirements » ; `q8_0` la ~divise par 2). Mono-utilisateur : **NP=1** + contexte large ; multi-utilisateur : prévoir la RAM.

## Plan priorisé

### P0 — Quick-wins (réglages Onyx/`.env`, ~1-2 h)
| # | Action | Valeur exacte |
|---|---|---|
| 1 | Fixer `OLLAMA_CONTEXT_LENGTH` (serveur) + Modelfile `PARAMETER num_ctx` — **✅ CÂBLÉ** (`make tune`, `pull-models.sh`, compose, Helm) | **12288** (7-8B) / **8192** (3b) / **16384** (GPU) ; RAM KV ∝ ctx × NP |
| 2 | `MAX_CHUNKS_FED_TO_CHAT` ↓ | **8** (avec reranker) |
| 3 | Embedding FR multilingue (Search Settings → Self-hosted ; **re-index complet**) | `intfloat/multilingual-e5-large` (1024d) ou `BAAI/bge-m3` |
| 4 | Activer reranker (Search Settings) | `BAAI/bge-reranker-v2-m3` (ou `mxbai-rerank-xsmall` si 16 Go) ; retrieve 30-50 → top-n 8 |
| 5 | Analyseur BM25 FR (**re-index**) | `OPENSEARCH_TEXT_ANALYZER=french` |
| 6 | Modèle + température | `qwen2.5:7b-instruct` (Q4_K_M CPU / **Q5_K_M** GPU) ; `temperature 0.2` |
| 7 | Clarifier/retirer `nomic` Ollama | non utilisé par le retrieval ; garder seulement comme juge RAGAS |

> ⚠️ Embedder + analyseur changent → **2 ré-index** : les faire **ensemble**. Passer hors-`nomic` **désactive les large-chunks** d'Onyx → compenser par le reranker.

### P1 — Structurel (jours)
8. **Éval RAGAS local** (juge Ollama + golden set FR) : `make rag-eval`, faithfulness ≥0.90 / context-precision ≥0.70 / answer-relevancy ≥0.85 — **✅ LIVRÉ** (`tests/rag/ragas_eval/`, `docs/RAG_EVAL.md`).
9. **Observabilité qualité** sur `access-gateway` (`/metrics` : no-context, citation, garde-fous, **P95 e2e**, feedback pouce) — **✅ LIVRÉ** (`docs/OBSERVABILITY.md` §5b).
10. **Tuning Ollama en K8s** injecté dans `deploy/k8s/.../values.yaml: ollama.tuning` + note `replicaCount:1` = SPOF de DÉBIT — **✅ CÂBLÉ**.
11. **Fraîcheur** : Prune SharePoint **30 j → 1-3 j** (runtime Onyx — cf. `PLAYBOOK_ONYX_RAG.md`).
12. **OpenSearch k-NN** : RAM hors-heap ≈ **18 Go/shard** pour 5 M vecteurs 768d (dimensionnement).

### État d'implémentation
**✅ Câblé repo-side (cet itéré)** : `num_ctx`/`OLLAMA_CONTEXT_LENGTH` (compose + Helm + `make tune` + Modelfile via `pull-models.sh`), température 0.2 gravée, tuning Ollama K8s, éval RAGAS (`make rag-eval`), `/metrics` qualité gateway, clarification `nomic`. **Reste runtime/Onyx-admin** (changements de config + **ré-index unique**, pas de code) : embedder FR multilingue, reranker, analyseur BM25 `french`, `MAX_CHUNKS_FED_TO_CHAT=8`, prune. → procédure pas-à-pas : [`PLAYBOOK_ONYX_RAG.md`](PLAYBOOK_ONYX_RAG.md).

## Capacité MESURÉE (4 vCPU, sans GPU)
| Modèle | tok/s mono | agrégé (NP=2) | Réponse ~300 tok | Users interactifs |
|---|---|---|---|---|
| `qwen2.5:7b` Q4_K_M | **5,8** | ~7,4 | ~52 s (mono) / ~80 s (2) | **1** (2-3 sporadiques) |
| `llama3.2:3b` | ~12-14 | ~12-14 | ~25 s | 2-3 |
| `llama3.2:1b` | ~15,5 | ~26 | ~12 s | 4-5 |

**Sur CPU, `num_ctx` n'ajoute pas de latence (mémoire only).** `NUM_PARALLEL ≈ vCPU/2` (max utile 2 à 4 vCPU). **Au-delà de ~3 users sur 7B : seul un GPU débloque** (→ qwen2.5:14b, NP=4, 30-60+ tok/s).

## Quantification (réf. 7B)
| Quant | Perte PPL | Reco |
|---|---|---|
| Q4_K_M | +1,68 % | **CPU** (vitesse prime) |
| Q5_K_M | +0,39 % | **sweet spot GPU/≥32 Go** |
| Q6_K/Q8_0 | +0,13 / +0,03 % | GPU avec VRAM |

## Sources
Onyx : `model_configs.py`, `chat_configs.py`, `shared_configs/configs.py`, `search_nlp_models.py`, issues #6189/#9364, docs/admins/advanced_configs/search_configs · Ollama : FAQ, PR #14120 (num_ctx ÷ parallel), docs/context-length · FR : MTEB-French (2405.20468), Gaperon (2510.25771) · quant : llama.cpp eval (2601.14277), discussion #2094 · RAGAS : Langfuse×Ragas.
