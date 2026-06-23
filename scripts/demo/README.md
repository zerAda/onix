# Scripts de démonstration onix + diagnostic #12 (Onyx 4.1.1 ↔ Ollama)

Scripts utilisés pour la démo « Assistant Client 360 » (chemins adaptés à la VM
de démo — généralisez via les variables d'env en tête de chaque script).

| Script | Démontre | Statut |
|---|---|---|
| [`demo-reconcile.sh`](demo-reconcile.sh) | **Réconciliation contrat ↔ SI Fabric** (OCR → réf OneLake via SP → verdict d'écarts) | 🟢 prouvé live |
| [`demo-rag.sh`](demo-rag.sh) | **Chat RAG souverain non-agentique** (récupération → génération locale Ollama) | 🟢 prouvé live |

```bash
./demo-reconcile.sh beta     # => ECART (cotisation 12 500 € contrat vs 13 000 € SI)
./demo-reconcile.sh gamma    # => CONFORME
./demo-rag.sh "Quelle est la cotisation annuelle du dossier CLIENT BETA ?"
#   => "La cotisation annuelle est de 12 500 EUR/an (dossier BETA-201). Risque : Prevoyance collective."
```

## 🔬 Diagnostic #12 sur Onyx 4.1.1 + Ollama (prouvé live)

Le chat **agentique** d'Onyx 4.1.1 ne produit pas de réponse sourcée avec des
modèles **locaux**. Investigation de bout en bout :

1. **Le tool-calling natif dépend du modèle.** Test brut Ollama `/api/chat` avec `tools` :
   - `gemma3:4b` → **aucun** `tool_calls` (le modèle ne sait pas faire de tool-calling) ;
   - `llama3.1:8b` → **vrai** `tool_calls` (`{"name":"add","arguments":{"a":12,"b":30}}`).
   ⇒ gemma3 est inadapté à l'agentique ; llama3.1 sait, **nativement**.

2. **Onyx casse quand même le tool-calling** des modèles capables. Via le provider
   litellm `ollama` (prompt-based), Onyx envoie les outils **dans le prompt** → le
   modèle les **recopie en texte** au lieu d'émettre un `tool_calls` structuré.
   Résultat : la « réponse » est un JSON d'appel d'outil (`internal_search`,
   `add_memory`) régurgité → l'UI affiche `{}`.

3. **L'endpoint OpenAI-compat débloque la recherche.** En enregistrant le provider
   Onyx en `openai` + `api_base=http://ollama:11434/v1` (Ollama expose un endpoint
   OpenAI-compatible avec tool-calling natif), Onyx **exécute réellement la
   recherche** (`search_tool_documents_delta` dans le flux) — la **récupération RAG
   fonctionne** dans l'agentique.

4. **Mais l'étape réponse reste vide.** Après la récupération, le modèle appelle
   l'outil `add_memory` (au lieu de répondre) ; en restreignant aux seuls outils de
   recherche, le 2ᵉ appel LLM (qui doit rédiger la réponse à partir du contexte)
   **revient vide** — sans erreur loggée. Mur profond de la boucle agentique
   Onyx 4.1.1 ↔ Ollama.

### Conséquence / parti pris
On adopte le **RAG non-agentique** (`demo-rag.sh`) : **récupération → génération
directe** par un appel Ollama `/api/generate` (pas de tool-calling). C'est fiable,
souverain, et c'est la **bonne architecture pour un modèle local** (cf. flux
recommandé : `retrieve → stuff context → generate`). Le chat agentique de l'UI
reste en l'état (récupération OK ; réponse = rôle du RAG non-agentique, ou d'un
modèle GPU à function-calling fiable + adaptation du parser litellm, en v2).

> La passerelle `access-gateway` (force_internal_search + unwrap `{result}`) visait
> l'**ancienne** API Onyx ; sur 4.1.1 le modèle ne produit plus de réponse à
> déballer. Sa valeur **RBAC + garde-fous** reste pertinente (v2, avec adaptation du
> streaming `obj/placement` de 4.1.1).
