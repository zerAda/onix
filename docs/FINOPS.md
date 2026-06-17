# FinOps — tokens MESURÉS vs ESTIMÉS (onix-actions)

Objectif : rendre le suivi de coût **crédible**. Les chiffres de tokens du chemin
LLM ne sont plus une heuristique (`chars/4`) : ils proviennent du **ground truth**
renvoyé par Ollama. Quand le ground truth n'est pas disponible, on l'indique
honnêtement plutôt que de prétendre l'inverse.

## 1. D'où viennent les chiffres

Ollama renvoie déjà les **vrais comptes de tokens** dans la réponse de
`POST /api/generate` (`stream:false`) — cf.
[doc Ollama](https://github.com/ollama/ollama/blob/main/docs/api.md). On capture :

| Champ Ollama | Sens | Usage onix-actions |
|---|---|---|
| `prompt_eval_count` | tokens d'**entrée** réellement évalués | `estimated_tokens_input` (mesuré) |
| `eval_count` | tokens de **sortie** réellement générés | `estimated_tokens_output` (mesuré) |
| `eval_duration` | durée de génération (**nanosecondes**) | tokens/s = `eval_count / eval_duration` |
| `prompt_eval_duration` | durée d'évaluation du prompt (ns) | perf (optionnel) |
| `total_duration` | durée totale (ns) | perf (optionnel) |
| `done` | génération terminée | garde |

`actions/app/llm.py` parse ces champs (`usage_from_ollama` →
`LLMUsage`) et `extract_fields_llm_with_usage` les **retourne** à l'appelant.
L'API historique `extract_fields_llm` reste inchangée (compatibilité ascendante :
elle renvoie toujours seulement les champs).

## 2. Centres de coût désormais MESURÉS

Le coût LLM est valorisé via les centres existants, à partir des **vrais** comptes :

| Centre de coût | Quantité | Source |
|---|---|---|
| `llm_token_input` | `prompt_eval_count` | **mesuré** (Ollama) |
| `llm_token_output` | `eval_count` | **mesuré** (Ollama) |

La *rate card* (`ONIX_RATE_CARD`, €/token) reste paramétrable. Par défaut un
déploiement Ollama local coûte 0 € ; le client peut valoriser
électricité/amortissement GPU.

## 3. Le flag `measured` (mesuré vs estimé)

Deux dimensions **distinctes** de fiabilité :

- **`cost_source`** (`PARAMETRABLE` / `A_VALIDER`) qualifie le **tarif** : une rate
  card a-t-elle été fournie ?
- **`measured`** (booléen) qualifie la **quantité** de tokens :
  - `measured=True` → comptes **réels** d'Ollama (`prompt_eval_count` /
    `eval_count`) ;
  - `measured=False` → **estimation** `chars/4` (repli heuristique sans LLM, ou
    réponse Ollama sans compteurs).

Persisté dans `usage_events.tokens_measured` (0/1 ; migration douce `ALTER TABLE`
pour les bases existantes). Surfacé dans :

- `GET /usage/summary` → bloc `tokens` :
  `measured_input` / `measured_output` / `estimated_input` / `estimated_output` /
  `measured_events` ;
- `GET /cost` → même bloc `tokens` (en plus de `spent_eur` / `budget`).

Ainsi le FinOps **distingue** le ground truth de l'heuristique : un client exigeant
voit immédiatement quelle part du coût repose sur des comptes mesurés.

`POST /usage` accepte aussi un champ `measured` (défaut `false`) pour les
appelants qui enregistrent eux-mêmes un usage déjà mesuré.

## 4. Signal de performance (optionnel)

`LLMUsage.eval_tokens_per_second` = `eval_count / (eval_duration / 1e9)` : débit
**réel** de génération du modèle local (tokens/s), dérivé des durées Ollama.

## 5. Limite HONNÊTE — le cache gateway ↔ Onyx

Le chemin RAG passe par **Onyx**, qui **médie** l'accès au LLM : la gateway
(`access-gateway/`) **ne voit pas** la réponse brute d'Ollama, donc **pas** son
`eval_count`. Par conséquent :

> La métrique `cache_tokens_saved` de la gateway **reste une estimation** (heuristique),
> et **ne peut pas** devenir « mesurée » tant qu'Onyx n'expose pas l'usage réel
> (`prompt_eval_count` / `eval_count`) de l'appel sous-jacent.

Le ground truth de ce document concerne **uniquement** les appels LLM **directs**
d'`onix-actions` à Ollama (chemin audit/extraction `use_llm=true`). Le jour où
Onyx exposera l'usage par requête, la même mécanique (`measured=True`) pourra être
étendue au chemin RAG/cache — sans changement de modèle de données.
