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
| Audit documentaire OCR (extraction + verdict) | **onix-actions** : OCR **local** (tesseract/poppler) → extraction de champs canoniques → comparaison vs référence → verdict typé + score | ✅ **implémenté (onix-actions)** (cf. `ACTIONS.md`) |
| Génération de fiches / documents (.docx) | **onix-actions** : `POST /generate/fiche` (python-docx) + `GET /download/{id}` | ✅ **implémenté (onix-actions)** |
| Relances / tâches (type Planner) | **onix-actions** : `POST/GET /tasks` (SQLite local) + `webhook_url` vers un système externe | ✅ **implémenté (onix-actions)** (local, sans M365) |
| Notifications (Teams / Power Automate) | **onix-actions** : `POST /notify` — webhook (Slack/Mattermost/Teams) ou SMTP | ✅ **implémenté (onix-actions)** |
| Suivi d'usage / FinOps / kill-switch (admin) | **onix-actions** : `/usage`, `/cost`, `/admin/control` (UPN hashés, flags qui gatent réellement) | ✅ **implémenté (onix-actions)** |
| Connecteurs au-delà de SharePoint (Teams, Confluence, Drive, web…) | Catalogue de connecteurs Onyx | ✅ **natif** (bonus) |

## Les 2 vraies réserves (à cadrer avec le client)

1. **RBAC par document = EE/Cloud.** En FOSS, pas de trimming par utilisateur :
   n'indexer que des périmètres à accès **homogène**, ou cloisonner (connecteurs/
   instances par groupe), ou passer en EE/Cloud pour la parité totale. Détails :
   [`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md) §6.
2. **Fonctions « applicatives » au-delà du RAG** (audit OCR, génération de
   documents, relances, notifications, usage/FinOps, kill-switch) sont désormais
   **implémentées** dans le microservice local **`onix-actions`** (cf.
   [`ACTIONS.md`](ACTIONS.md)), branché à l'assistant via **Onyx Custom Actions**.
   Maturité honnête : moteur d'audit, génération `.docx`, tâches, usage, coût et
   administration sont **opérationnels et testés** ; l'**OCR de PDF scannés/images**
   nécessite les binaires `tesseract`/`poppler` (fournis par l'image Docker du
   service) et dégrade proprement à défaut ; les connecteurs externes (webhook
   tâches/notify, SMTP) sont des **MVP** prêts à configurer.

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
relances, notifications, usage/FinOps, kill-switch) sont **implémentées** dans
**`onix-actions`** (cf. [`ACTIONS.md`](ACTIONS.md)) : un acquis, branché via Onyx
Custom Actions — avec une maturité documentée honnêtement (OCR de scans
tributaire des binaires système ; connecteurs externes en MVP).
