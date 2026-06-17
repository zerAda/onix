# Agent « Assistant Commercial 360 » — assistant RAG sourcé sur SharePoint

onix réplique, en **open-source et 100 % local**, le comportement d'un assistant
commercial d'entreprise (type Copilot Studio) : **réponses sourcées uniquement**,
**un client à la fois**, **respect des permissions SharePoint**, **lecture seule**.
Le moteur est **Onyx** (RAG) + **Ollama** (LLM local) ; la source est un connecteur
**SharePoint** (voir [`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md)).

## 1. Créer l'agent dans Onyx

1. Indexer SharePoint d'abord (cf. `connectors/SHAREPOINT.md`) → un **Document Set**
   regroupant le(s) site(s) clients.
2. **Admin → Assistants/Agents → New**.
3. **Instructions** : coller le prompt de
   [`../prompts/agent_commercial_systeme.md`](../prompts/agent_commercial_systeme.md).
4. **Knowledge / Document Sets** : restreindre l'agent au Document Set SharePoint
   (et à lui seul → pas de fuite hors périmètre).
5. **LLM** : choisir le modèle Ollama (`http://ollama:11434`, ex. `llama3.2:3b` /
   `qwen2.5:7b-instruct` selon `make tune`).
6. **Citations** : activées (par défaut dans Onyx) → chaque réponse porte ses sources.
7. **Starter messages** : reprendre les exemples de
   [`../prompts/exemples_questions.md`](../prompts/exemples_questions.md).

## 2. « Réponses sourcées uniquement » (pas de connaissances générales)

Comme l'assistant d'entreprise (`useModelKnowledge = false`), onix doit répondre
**seulement** depuis les documents :
- Le **prompt système** l'impose explicitement (règle 1 & 2).
- Restreindre l'agent à **un Document Set** (pas de réponse hors corpus).
- Activer un seuil de pertinence raisonnable dans la config de recherche pour
  éviter les réponses sur du bruit, et laisser l'agent répondre « non disponible »
  quand le contexte est vide (le prompt l'exige).

## 3. Sécurité d'accès — RBAC par utilisateur (point critique)

L'assistant d'entreprise lit SharePoint **avec l'identité de l'utilisateur** (RBAC :
chacun ne voit que ses documents). Dans onix :
- **Identité** : activer le **SSO OIDC Entra ID** (cf. [`SECURITY.md`](SECURITY.md) §6)
  → onix sait qui est l'utilisateur.
- **Trimming par document** : repose sur la **synchronisation des permissions** du
  connecteur SharePoint… **disponible uniquement en Onyx Cloud / Enterprise Edition**
  (cf. `connectors/SHAREPOINT.md` §Permission sync). En édition **FOSS**, l'index est
  **partagé** : tout utilisateur authentifié voit tout ce qui est indexé.
  → Stratégies FOSS sûres dans `connectors/SHAREPOINT.md` (index à accès uniforme,
  connecteurs par groupe). **À cadrer explicitement avec le client** : c'est LA
  différence de fond avec la version entreprise.

## 4. Catalogue des cas d'usage (parité fonctionnelle)

| Cas d'usage | Exemple de question | Sortie attendue |
|---|---|---|
| Résumé client | « Résume-moi le dossier du client ABC » | Synthèse + sources |
| Recherche document | « Quel est le dernier contrat pour ce client ? » | Extrait + source |
| Recherche d'info / clause | « Y a-t-il une clause de résiliation ? » | Réponse sourcée ou « non disponible » |
| Préparation RDV | « Prépare-moi un briefing avant la réunion » | Fiche briefing sourcée |
| Points d'attention | « Quels sont les risques sur ce client ? » | Liste priorisée |
| Brouillon mail | « Rédige un mail de suivi » | Mail à valider (jamais d'envoi) |
| Documents manquants | « Quels documents manquent au dossier ? » | Liste |
| Arguments de vente | « Donne-moi des arguments sourcés » | Liste sourcée |
| Recherche juridique | « Quelles obligations dans ce contrat ? » | Extrait + avertissement |

Comportements de garde (imposés par le prompt) : **client ambigu** → demande de
précision ; **info absente** → « non disponible » ; **autre client / hors corpus /
CRM / météo** → hors périmètre ; **demande de modification** → refus (lecture seule).

## 5. Validation (équivalent du jeu de tests d'acceptation)

Rejouer les questions de [`../prompts/exemples_questions.md`](../prompts/exemples_questions.md)
(résumé, recherche, RDV, mail, info absente, client ambigu, **permissions**,
hors-sujet, sources) et vérifier : réponses **sourcées**, **mono-client**, **aucune
invention**, **citation systématique**, et — si permission sync activée (EE) — qu'un
utilisateur ne voit **pas** un dossier auquel il n'a pas accès.
