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

## Gate nightly LIVE (anti-régression) — `.github/workflows/ragas-nightly.yml`

> **Le trou que ça bouche.** La CI par PR (`ci.yml`) ne lance que les tests RAGAS
> **offline** (juge mocké) : elle ne fait **jamais** tourner un vrai modèle, donc
> elle ne mesure **pas** la qualité réelle. Une **dérive** (le RAG se met à
> halluciner, le retrieval se dégrade) pouvait donc atteindre le client **sans
> être vue**. Le workflow **nightly** ferme ce trou : chaque nuit, il fait tourner
> le **LLM-juge souverain** (Ollama LOCAL) sur le golden set et **échoue** si la
> qualité casse ou **régresse**.

**Nightly ≠ per-PR.** Ce workflow est `schedule` (cron) + `workflow_dispatch`
**uniquement** — jamais `pull_request`. Il **ne bloque pas les PR** (l'inférence
LLM est trop lente/instable pour un gate bloquant par PR). Le gate par PR reste
le test **offline** dans `ci.yml`.

### Deux garde-fous COMPLÉMENTAIRES : seuils (absolu) vs tolérance (relatif)

| | **Gate absolu** (seuils) | **Anti-régression** (tolérance) |
|---|---|---|
| Question | « Est-ce **assez bon dans l'absolu** ? » | « A-t-on **régressé** vs hier ? » |
| Mécanisme | chaque agrégat ≥ seuil `ONIX_RAGAS_MIN_*` | chaque agrégat ≥ baseline − tolérance |
| Attrape | un **effondrement** brutal | une **dérive lente** (0.97 → 0.91…) |
| Réglage | `runner.py` (env-surchargeable) | `compare_scores.py --tolerance` (déf. **0.05**) |
| Aveugle à | la dérive lente sous le seuil | un niveau bas mais **stable** |

Les deux tournent dans la cible `make rag-eval-ci` (runner `--json scores.json`
→ `compare_scores.py` vs baseline). **Le job échoue si l'un OU l'autre casse.**

> **Pourquoi une tolérance et pas l'égalité ?** Le juge est un **LLM** : ses
> verdicts **varient** d'un run à l'autre (échantillonnage, formulation), même à
> température 0, et **d'autant plus avec un petit modèle**. Exiger l'égalité
> rendrait le job **rouge en permanence** pour du bruit. La tolérance ne laisse
> échouer que les **vraies** dégradations. Une **hausse** ne pénalise jamais.

### Seuils calibrés pour le golden set COMPLET (important)

`golden_fr.json` contient **2 items volontairement dégradés** (G07 halluciné,
G08 hors-sujet) qui tirent `faithfulness`/`context_precision` vers le bas **par
construction**. Les seuils **par défaut** du runner (0.90 / 0.70 / 0.85) supposent
un set **propre** : sur le set complet ils **échoueraient toujours** (gate
décoratif). Le workflow pose donc des **planchers réalistes** pour le set complet
+ juge `1b` bruité (cf. `env:` du workflow : faithfulness 0.55, context_precision
0.55, answer_relevancy 0.70) — le gate reste un **vrai signal** (passe si sain,
casse si effondrement), la dérive fine étant attrapée par l'anti-régression.

### La baseline : lire et rafraîchir

- **Fichier** : `tests/rag/ragas_eval/baseline_scores.json` (committé). Même schéma
  que la sortie `runner --json` (clé `aggregates`) → les deux sont interchangeables.
- **Valeurs livrées** : ce sont une **graine de référence DÉTERMINISTE** produite
  par le juge **scripté** (reproductible, documentée). ⚠️ Un vrai juge `1b` score
  **différemment** : après le **premier run nightly sain**, **rafraîchir** la
  baseline depuis un run réel.
- **Rafraîchir** (toujours après **revue du diff** — une baseline doit venir d'un
  run **sain**) :

  ```bash
  cd tests/rag
  python -m ragas_eval.compare_scores scores.json \
      --baseline ragas_eval/baseline_scores.json --update
  ```

  En CI : le `workflow_dispatch` a un input **`update_baseline`** qui produit la
  baseline rafraîchie **en artefact** (à committer à la main après revue — le job
  est en `contents: read`, **aucun push automatique**).
- **Lire un échec** : le comparateur imprime un tableau `baseline | courant |
  delta | verdict` ; la ligne fautive est marquée `RÉGRESSION`. On télécharge
  l'artefact `ragas-scores` (le `scores.json` du run) pour analyser.

### Lancer la même chose en local

```bash
export ONIX_LIVE_OLLAMA=1 ONIX_LIVE_MODEL=qwen2.5:7b-instruct
make rag-eval-ci            # runner --json scores.json + compare vs baseline
# Variables : SCORES=… BASELINE=… TOL=0.05
```

### ⚠️ Honnêteté sur la fiabilité du runner

- **Un runner hébergé GitHub** (2 vCPU, ~7 Go RAM, **sans GPU**) fait tourner un
  **petit** modèle (`llama3.2:1b`) en **CPU** : c'est **lent** et **bruyant**. Les
  scores d'un `1b` sont **moins fiables** qu'un ≥ 7B (plus de JSON mal formé compté
  en anomalie, verdicts plus variables) — d'où **tolérance, pas égalité**.
- **Pour une mesure stable, préférer un *runner self-hosted*** (idéalement GPU) et
  un **modèle ≥ 7B** (`qwen2.5:7b-instruct`). Remplacer `runs-on: ubuntu-latest`
  par votre label self-hosted et l'input `model`. La baseline doit alors être
  rafraîchie **sur ce runner-là** (les scores dépendent du modèle).
- **Le job PEUT légitimement échouer** (gate ou régression) : c'est le but. S'il
  « flapote » par pur bruit sur un runner faible, **élargir la tolérance** ou
  **passer en self-hosted** plutôt que de désactiver le gate.

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
