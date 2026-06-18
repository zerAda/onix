# Éval qualité RAG — méthodologie RAGAS, **souveraine** et **offline-CI-able**

Ce paquet score la **qualité des réponses** d'un pipeline RAG sur un **jeu doré
français** (`golden_fr.json`) en utilisant un **LLM-juge sur l'Ollama LOCAL** :
aucun cloud, aucune dépendance lourde (stdlib + le harnais `tests/rag/` existant).

Il calcule les **trois métriques cœur** de la méthodologie [RAGAS](https://docs.ragas.io/) :

| Métrique | ∈ | Définition implémentée |
|---|---|---|
| **faithfulness** | [0,1] | Proportion des **affirmations atomiques** de la réponse qui sont **étayées par le contexte récupéré**. On décompose la réponse en *claims* via le LLM, puis on juge chacun *étayé / non étayé*. Score = (#étayés)/(#claims). Réponse **sans affirmation vérifiable** (refus honnête « non disponible ») → **1.0** (rien d'hallucinable). |
| **context_precision** | [0,1] | Proportion des **chunks de contexte récupérés** qui sont **pertinents** pour répondre à la question. On juge chaque chunk *pertinent / non pertinent*. Score = (#pertinents)/(#chunks). **Aucun chunk** → **0.0**. |
| **answer_relevancy** | [0,1] | À quel point la réponse **adresse directement la question** (indépendamment de sa véracité). **Note directe** 0–4 par le LLM, ramenée à [0,1] (note/4). |

> **Choix « note directe » pour answer_relevancy.** RAGAS calcule classiquement
> ce score via la **similarité d'embeddings** entre la question d'origine et des
> questions *régénérées* à partir de la réponse. Sur un déploiement **souverain**
> (Ollama local, pas d'API d'embeddings cloud, petit modèle), cette voie ajoute
> des appels et un modèle d'embeddings. On préfère une **note directe** par le
> juge : **un seul appel**, déterministe (température 0), sans dépendance
> supplémentaire. Compromis : moins « canonique » que la variante embeddings,
> mais suffisant pour un **gate** et reproductible hors-ligne.

L'**agrégat** est une **moyenne macro par item** (chaque cas du golden set pèse
pareil), puis un **gate** compare aux seuils.

## Architecture (séparation des responsabilités)

```
ragas_eval/
├── golden_fr.json      # jeu doré FR (≥ 8 items) + 2 cas DÉGRADÉS (hallucination, contexte hors-sujet)
├── judge.py            # prompts FR + décomposition LLM + extraction JSON ROBUSTE ; llm() injectable
├── metrics.py          # agrégation DÉTERMINISTE des verdicts → scores ∈ [0,1] + gate (testable sans LLM)
├── runner.py           # charge le golden set, score, rapport FR, gate, --json, --backend ; exit≠0 si FAIL
├── scripted_judge.py   # juge SCRIPTÉ déterministe (faux LLM) — oracle des tests ET de la baseline
├── gen_baseline.py     # (re)génère baseline_scores.json DÉTERMINISTEMENT (sans Ollama), --write/--check
├── baseline_scores.json# graine de référence anti-régression (produite par gen_baseline)
├── conftest.py         # active les imports en nom plat (convention tests/rag)
├── test_ragas_eval.py  # tests OFFLINE (juge scripté, AUCUN réseau)
└── README.md           # ce fichier
```

> **Provenance de la baseline (reproductible byte-level).** `baseline_scores.json`
> (`0.75` / `0.875` / `1.0`) est produit par `gen_baseline.py` en scorant
> `golden_fr.json` avec `scripted_judge.py` — **aucun modèle live**. On reproduit
> à l'octet près via `python -m ragas_eval.gen_baseline --write` ; le test
> `test_baseline_is_reproducible_from_scripted_judge` garde l'invariant. C'est une
> **graine déterministe**, pas un run d'un vrai juge ≥ 7B (à rafraîchir après le
> premier run nightly sain — cf. `docs/RAG_EVAL.md`).

La frontière clé : **`judge.py` parle au LLM**, **`metrics.py` fait les maths**.
Toute la mathématique des métriques et la logique du gate sont donc **testables
sans réseau** (on fabrique des verdicts à la main), et le juge est **mockable**
(on injecte un faux `llm(system, user) -> str`).

## Souverain par défaut vs vraie librairie `ragas` (compromis)

| | **Backend `sovereign`** (défaut) | **Backend `ragas`** (optionnel) |
|---|---|---|
| LLM-juge | **Ollama LOCAL** (`live_harness.chat`) | LLM/embeddings de RAGAS (souvent **cloud** par défaut) |
| Dépendances | **stdlib** + harnais existant | `pip install ragas` (tire `datasets`, etc.) |
| Souveraineté / offline | **Oui** (100 % local) | **Non** par défaut (sort du périmètre souverain) |
| CI offline | **Oui** (tests à juge mocké) | Non (déconseillé en CI) |
| Statut ici | **complet & testé** | **import paresseux + dégradation propre** |

Le drapeau `--backend ragas` **importe `ragas` paresseusement** et **dégrade
proprement** (message clair, code de sortie 3) si la librairie est absente — ou
présente mais non câblée. On reste **honnête** : l'intégration complète de
l'API RAGAS n'est pas branchée (son API évolue et ses LLM/embeddings ne sont pas
souverains par défaut) ; le **défaut souverain** est la voie garantie hors-ligne.
La valeur du backend `ragas` est de documenter le pont possible, pas de le
prétendre prêt.

## Comment lancer

### Tests OFFLINE (CI, aucun réseau, aucun Ollama)

```bash
pytest -q tests/rag/ragas_eval          # juge scripté injecté
# ou, inclus dans :
make rag-test                            # toute la recette tests/rag hors-LLM
make pytest                              # barrière locale miroir de la CI
```

### Éval LIVE (souveraine, nécessite Ollama ≥ 7B)

```bash
export ONIX_LIVE_OLLAMA=1
export ONIX_LIVE_MODEL=qwen2.5:7b-instruct      # idem que rag-test-live
export ONIX_OLLAMA_URL=http://127.0.0.1:11434   # défaut
make rag-eval
# ou directement :
cd tests/rag && ONIX_LIVE_OLLAMA=1 python -m ragas_eval.runner --json scores.json
```

Le runner imprime un **rapport français** (tableau par item + agrégats +
PASS/FAIL), écrit éventuellement les scores avec `--json OUT`, et **sort en code
non nul** si le gate échoue.

## Seuils du gate (surchargeables par env)

| Métrique | Seuil par défaut | Variable d'environnement |
|---|---|---|
| faithfulness | **0.90** | `ONIX_RAGAS_MIN_FAITHFULNESS` |
| context_precision | **0.70** | `ONIX_RAGAS_MIN_CONTEXT_PRECISION` |
| answer_relevancy | **0.85** | `ONIX_RAGAS_MIN_ANSWER_RELEVANCY` |

Une métrique **non scorable** (juge illisible sur tous les items) **fait échouer**
le gate : on ne valide jamais faute de mesure.

> ⚠️ Le golden set livré contient **2 items volontairement dégradés** (`G07`
> halluciné, `G08` à contexte hors-sujet) pour **prouver que les métriques
> discriminent**. Conséquence : sur le golden set **complet**, le gate **échoue
> volontairement** (les agrégats chutent). C'est attendu et testé. Pour un gate
> qui passe, évaluer un pipeline réel ou un sous-ensemble propre (cf. les tests).

## Limites honnêtes

- **Variance du juge.** Un LLM-juge n'est pas un oracle : ses verdicts varient
  selon le modèle, le tirage et la formulation. On réduit la variance avec
  **température 0** et des prompts **JSON-only**, mais la métrique reste une
  **estimation**. Pour un chiffre stable, moyenner plusieurs runs / élargir le
  golden set.
- **Taille de modèle.** Un jugement fiable demande **≥ 7B** (idéalement
  instruct). Sous 7B, le juge produit plus souvent du JSON mal formé (compté en
  anomalie) et des verdicts bruités.
- **Robustesse, pas infaillibilité.** Une réponse LLM illisible est **comptée**
  (compteur `errors`, remontée dans le rapport) et **ne crashe jamais** le
  runner — mais elle dégrade la mesure (claim/chunk illisible compté
  conservativement « non étayé / non pertinent »).
- **Pas un substitut au mode contrat ni au red-team.** Cette éval mesure la
  **qualité** des réponses ; la **sécurité** (anti-injection, mono-client,
  lecture seule) est couverte par `test_red_team.py` (contrat) et
  `test_live_ollama.py` (live) — cf. `docs/QA_GUARDRAILS.md`.
- **`golden_fr.json` est un jeu de référence**, pas une mesure du retrieval Onyx
  réel : les `retrieved_contexts` y sont fournis. Brancher le vrai retrieval
  reste une étape d'intégration E2E.
