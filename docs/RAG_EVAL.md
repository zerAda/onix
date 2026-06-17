# Évaluation qualité RAG — méthodologie RAGAS (souveraine, offline-CI-able)

> Harnais d'**évaluation de la qualité des réponses** du RAG d'onix, façon
> [RAGAS](https://docs.ragas.io/), **100 % local** : le LLM-juge tourne sur
> l'**Ollama LOCAL** (aucun cloud), avec **stdlib + le harnais `tests/rag/`**
> existant comme seules dépendances du chemin par défaut.
>
> Cette éval mesure la **qualité** (la réponse est-elle fidèle, bien
> contextualisée, pertinente ?). Elle **complète** — sans le remplacer — le
> dispositif **sécurité/garde-fous** (`docs/QA_GUARDRAILS.md`,
> `docs/LIVE_GUARDRAILS_RESULTS.md`).

## Métriques (les trois cœurs RAGAS)

| Métrique | ∈ | Ce que ça mesure | Calcul |
|---|---|---|---|
| **faithfulness** | [0,1] | La réponse n'**hallucine** pas hors des sources | (#affirmations étayées par le contexte) / (#affirmations) ; aucune affirmation → 1.0 |
| **context_precision** | [0,1] | Le **retrieval** a ramené du contexte **pertinent** | (#chunks pertinents) / (#chunks) ; aucun chunk → 0.0 |
| **answer_relevancy** | [0,1] | La réponse **adresse la question** posée | note directe 0–4 du juge, ÷ 4 |

Agrégat = **moyenne macro par item** ; un **gate** compare aux seuils.

## Gate (seuils par défaut, surchargeables par env)

| Métrique | Seuil | Variable |
|---|---|---|
| faithfulness | **0.90** | `ONIX_RAGAS_MIN_FAITHFULNESS` |
| context_precision | **0.70** | `ONIX_RAGAS_MIN_CONTEXT_PRECISION` |
| answer_relevancy | **0.85** | `ONIX_RAGAS_MIN_ANSWER_RELEVANCY` |

Le runner **sort en code non nul** si le gate échoue (intégrable en recette/CI).

## Lancer

```bash
# OFFLINE (CI, aucun réseau, juge mocké) — fait partie de `make pytest` / CI :
pytest -q tests/rag/ragas_eval

# LIVE souverain (Ollama ≥ 7B requis), mêmes conventions d'env que rag-test-live :
export ONIX_LIVE_OLLAMA=1 ONIX_LIVE_MODEL=qwen2.5:7b-instruct
make rag-eval
# ou : cd tests/rag && ONIX_LIVE_OLLAMA=1 python -m ragas_eval.runner --json scores.json
```

## Souverain par défaut vs vraie librairie `ragas`

- **Backend `sovereign` (défaut)** : LLM-juge = **Ollama local** ; **stdlib**
  uniquement ; **testé** et **offline-CI-able**.
- **Backend `ragas` (optionnel)** : `--backend ragas` importe la librairie
  **paresseusement** et **dégrade proprement** si absente (ou présente mais non
  câblée). RAGAS n'est **pas souverain par défaut** (LLM/embeddings souvent
  cloud, dépendances lourdes) → marqué **optionnel**, hors chemin CI.

## Où est le code

Tout est sous **`tests/rag/ragas_eval/`** (golden set, juge, métriques, runner,
tests offline). Détails, compromis et **limites honnêtes** (variance du juge,
besoin d'un ≥ 7B) : voir **`tests/rag/ragas_eval/README.md`**.

> Note : le `golden_fr.json` livré inclut **2 items volontairement dégradés**
> (hallucination, contexte hors-sujet) pour démontrer que les métriques
> **discriminent**. Sur le set complet, le gate **échoue volontairement** — c'est
> attendu et couvert par les tests.
