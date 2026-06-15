# Parité fonctionnelle — onix vs assistant commercial cloud d'entreprise

Objectif d'onix : faire **ce que fait un assistant commercial RAG d'entreprise**
(type Copilot Studio sur SharePoint), en **open-source, local et gratuit**. Cette
page est **honnête** : elle distingue ce qui est **natif**, **par configuration**,
**réservé à l'Enterprise Edition (EE)**, ou **roadmap**.

## Matrice de parité

| Capacité (assistant d'entreprise) | onix / Onyx | Statut |
|---|---|---|
| RAG sourcé sur documents SharePoint | Connecteur SharePoint natif + index vecteur/lexical | ✅ **natif** (config) |
| Réponses **sourcées uniquement** (pas de connaissances générales) | Prompt système strict + Document Set + citations | ✅ **config** |
| Citations / sources affichées | Citations natives Onyx | ✅ **natif** |
| **Un client à la fois**, anti-mélange | Imposé par le prompt système de l'agent | ✅ **config** |
| Cas d'usage : résumé, recherche doc/clause, RDV, points d'attention, mail, docs manquants, arguments, juridique | Agent « Assistant Commercial 360 » (un prompt + Document Set) | ✅ **config** (cf. `AGENT_COMMERCIAL.md`) |
| **Lecture seule** (pas d'écriture SharePoint) | L'agent ne fait que de la recherche | ✅ **par conception** |
| SSO entreprise | **OIDC Entra ID** | ✅ **config** (`SECURITY.md` §6) |
| **RBAC par utilisateur** (chacun ne voit que ses docs) | **Permission sync** du connecteur SharePoint | ⚠️ **EE / Cloud uniquement** (FOSS : index à accès uniforme / par groupe) |
| LLM | **Ollama local** (souverain) ou tout LLM | ✅ **natif** |
| Multi-format (PDF, Office…) | Indexation native Onyx | ✅ **natif** |
| Souveraineté / hors-ligne / zéro transfert | Tout en local (Ollama + OpenSearch + MinIO) | ✅ **supérieur** au cloud |
| Audit documentaire OCR (extraction + verdict) | — (Onyx indexe, ne « audite » pas) | 🔜 **roadmap** (pipeline applicatif à ajouter) |
| Génération de fiches / documents (.docx) | — | 🔜 **roadmap** |
| Relances / tâches (type Planner) | — | 🔜 **roadmap** (intégration externe) |
| Connecteurs au-delà de SharePoint (Teams, Confluence, Drive, web…) | Catalogue de connecteurs Onyx | ✅ **natif** (bonus) |

## Les 2 vraies réserves (à cadrer avec le client)

1. **RBAC par document = EE/Cloud.** En FOSS, pas de trimming par utilisateur :
   n'indexer que des périmètres à accès **homogène**, ou cloisonner (connecteurs/
   instances par groupe), ou passer en EE/Cloud pour la parité totale. Détails :
   [`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md) §6.
2. **Fonctions « applicatives » au-delà du RAG** (audit OCR, génération de
   documents, relances) ne sont pas dans le périmètre RAG d'Onyx : elles relèvent
   d'une **brique applicative** à brancher (roadmap), pas d'une simple config.

## Ce qu'onix apporte EN PLUS d'un assistant cloud
- **Souveraineté totale** : inférence + index + fichiers **sur site**, aucun
  transfert vers un fournisseur d'IA, **gratuit**.
- **Sans dépendance de licence** pour le cœur RAG (hors permission sync EE).
- **Auto-hébergé, durci, auto-tuné** (cf. `SECURITY.md`, `PERFORMANCE.md`).

## En résumé
Pour un **assistant commercial RAG sourcé sur SharePoint, en lecture seule, mono-
client, souverain** : onix atteint la **parité fonctionnelle** par configuration.
La seule limite de fond en édition gratuite est le **RBAC fin par document**
(permission sync EE). Les fonctions applicatives annexes (audit OCR, génération,
relances) sont une **roadmap**, pas un acquis.
