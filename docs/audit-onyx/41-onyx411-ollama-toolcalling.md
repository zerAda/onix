# Onyx 4.1.1 + Ollama : le mur du tool-calling (#12) — diagnostic & parti pris

> **Statut : prouvé LIVE** (VM Azure, tenant GEREP, 2026-06-23). Ce document
> consolide l'investigation de bout en bout du bug **#12** et explique le choix
> d'architecture d'onix : le **RAG non-agentique**.

## TL;DR

Le **chat agentique** d'Onyx 4.1.1 ne produit **pas de réponse sourcée** avec des
modèles **locaux** (Ollama). Cause : Onyx pousse les outils *via le prompt* (et non
via le tool-calling natif), donc le modèle **recopie l'appel d'outil en texte** au
lieu de l'exécuter. onix contourne avec un **RAG non-agentique** (récupération →
génération directe), fiable et souverain — c'est la bonne architecture pour un
modèle local.

## L'investigation (reproductible)

### 1. Le tool-calling natif dépend du modèle
Test brut Ollama `POST /api/chat` avec `tools` (la requête de référence de la doc Ollama) :

| Modèle | `tool_calls` structuré ? |
|---|---|
| `gemma3:4b` | ❌ aucun (le modèle ne *sait pas* faire de tool-calling) |
| `llama3.1:8b` | ✅ vrai `{"name":"add","arguments":{"a":12,"b":30}}` |

⇒ gemma3 est inadapté à l'agentique ; **llama3.1 sait, nativement**.

### 2. Onyx casse le tool-calling des modèles capables
Avec le provider litellm `ollama` (prompt-based), Onyx envoie les outils **dans le
prompt** → même `llama3.1` **recopie en texte** (`{"name":"internal_search",…}`)
au lieu d'émettre un `tool_calls`. L'UI affiche alors `{}`.

### 3. L'endpoint OpenAI-compat débloque la RÉCUPÉRATION
En enregistrant le provider Onyx en `provider=openai` +
`api_base=http://ollama:11434/v1` (Ollama expose un endpoint OpenAI-compatible avec
tool-calling natif), Onyx **exécute réellement la recherche** : on observe
`search_tool_start → search_tool_queries_delta → search_tool_documents_delta` dans
le flux. **La récupération RAG fonctionne** dans l'agentique.

### 4. Mais l'étape RÉPONSE reste vide
Après la récupération, le modèle appelle l'outil **`add_memory`** (au lieu de
rédiger) ; en restreignant aux seuls outils de recherche (`allowed_tool_ids`), le
2ᵉ appel LLM — censé rédiger à partir du contexte — **revient vide**, sans erreur
loggée (api_server). Mur profond de la boucle agentique Onyx 4.1.1 ↔ Ollama.

## Le parti pris onix : RAG non-agentique

On **ne dépend pas** de la boucle agentique du modèle. Flux : **récupération →
« stuff context » → génération** par un appel Ollama `/api/generate` (sans outils).
Prouvé LIVE — réponse grounded + citée, ~2 s (gemma3:4b CPU) :

> *« La cotisation annuelle est de 12 500 EUR/an (dossier BETA-201). Risque :
> Prévoyance collective. »*

- Module testé : [`actions/app/rag_local.py`](../../actions/app/rag_local.py) (souverain, fail-closed, I/O injectables).
- Démo : [`scripts/demo/demo-rag.sh`](../../scripts/demo/demo-rag.sh).

## Conséquences

- Le **chat agentique de l'UI Onyx** reste en l'état (récupération OK ; réponse =
  rôle du RAG non-agentique, ou d'un modèle GPU à function-calling fiable **+**
  correctif du parser litellm/Ollama, en v2).
- La passerelle `access-gateway` (`force_internal_search` + unwrap `{result}`)
  visait l'**ancienne** API Onyx ; sur 4.1.1 le modèle ne produit plus de réponse à
  déballer. Sa valeur **RBAC + garde-fous** reste pertinente (v2, avec adaptation du
  streaming `obj/placement` de 4.1.1).
- **Recommandation modèle** : pour de l'agentique fiable en local, viser un modèle
  *tools-tagged* Ollama ≥ 7-8B **et** un transport OpenAI-compat ; sinon, RAG
  non-agentique (défaut onix).

## Voir aussi
- Scope RAG/prompts : [`docs/scopes/rag-prompts.md`](../scopes/rag-prompts.md).
- Récit FOSS vs EE (#12 d'origine) : [`docs/audit-onyx/00-VERDICT.md`](00-VERDICT.md).
