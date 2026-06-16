# Parité fonctionnelle — onix vs assistant commercial cloud d'entreprise

Objectif d'onix : faire **ce que fait un assistant commercial RAG d'entreprise**
(type Copilot Studio sur SharePoint), en **open-source, local et gratuit**. Cette
page est **honnête** : elle distingue ce qui est **natif**, **par configuration**,
**réservé à l'Enterprise Edition (EE)**, ou **roadmap**.

## Readiness entreprise — re-scoring après remédiation (6 workstreams)

| Dimension | Avant | Après | Preuve |
|---|---|---|---|
| Fonctions applicatives | 88 | ✅ **GO (92)** | moteur audit byte-identique + `/metrics` + 61 tests |
| Couverture métier RAG | 72 | ✅ **GO** | 6 cas portefeuille (formats AC360) + RDV SWOT |
| Garde-fous / tests | 52 | ✅ **GO** ＊ | red-team + éval + anti-régression du prompt (95 tests) |
| Sécurité & identité | 52 | ✅ **GO** ＊＊ | authz par appel, redaction PII, DLP/anti-SSRF, audit HMAC, SAST CI |
| Conformité RGPD | 54 | ✅ **GO** | rétention/effacement art.17, journal d'accès, registre + DPIA |
| Config / ALM (prod) | 58 | ✅ **GO** | Caddy TLS + OIDC forcé, démarrage défaut-sûr, dev/test/prod, CI/CD |
| Connecteur SharePoint | 42 | ✅ **GO** ＊＊ | cloisonnement par groupe (`access-gateway/`) ; pages/tenant FR corrigés |
| Architecture / HA | 28 | 🟢 **VERT by-design** ＊＊＊ | Helm HA (`helm lint` 0) — OpenSearch/Postgres/MinIO/Redis HA, HPA |

**Validation globale (sandbox)** : **217 tests verts** (actions 70 + gateway 52 + RAG 95) + **red-team live 17/21 sur `qwen2.5:7b`** + modes **Postgres/MinIO/Celery** prouvés multi-réplica · bandit 0 H/M · **pip-audit 0 CVE** · gitleaks 0 · `compose config` (base/prod/monitoring) · `helm lint` 0 · `caddy validate` OK.

**Clôture des 3 astérisques (vague de fermeture — preuves réelles) :**
- ＊ **Garde-fous → largement levé.** Red-team **live sur `qwen2.5:7b-instruct` = 81 % (17/21)**, **100 % sur les catégories critiques** (anti-injection, anti-révélation du prompt, anti-exfiltration) ; les 4 cas restants (lecture-seule / info absente) sont couverts par le **post-filtre déterministe** + le durcissement de prompt. *Reste* : E2E complet avec retrieval Onyx sur la stack déployée. (`docs/LIVE_GUARDRAILS_RESULTS.md`)
- ＊＊ **RBAC → cadré.** Dossier de décision **chiffré** (`docs/DECISION_RBAC.md`) + **passerelle durcie** (fail-closed, audit HMAC, 52 tests). Le trimming **strict par-document** + révocation auto reste une **fonction Onyx EE/Cloud** (limite produit, pas un trou de notre code) ; le cloisonnement **par groupe** FOSS borne le risque.
- ＊＊＊ **HA → code prouvé.** `actions` rendu **stateless** : partage d'état **multi-réplica prouvé** avec **vrais Postgres + MinIO + Celery** (kill-switch posé sur une réplique → 403 sur l'autre ; `.docx` généré par l'une → téléchargé par l'autre ; chaîne HMAC vérifiée + altération détectée). *Reste* : recette sur **cluster réel** (HPA sous charge, bascule). (`docs/STATELESS_ACTIONS.md`)

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
   documents, relances, notifications, usage/FinOps, kill-switch) sont
   **implémentées** dans le microservice local **`onix-actions`** (cf.
   [`ACTIONS.md`](ACTIONS.md)), branché à l'assistant via **Onyx Custom Actions**.
   Maturité (validée **bout-en-bout**, preuves réelles) : audit, génération
   `.docx`, tâches, usage, coût et admin **opérationnels et testés** ; **OCR de
   PDF/images scannés prouvé en conteneur** (tesseract+poppler dans l'image,
   pixels → texte → verdict) ; **extraction LLM prouvée via un vrai Ollama** ;
   **notifications (webhook + SMTP) et tâches sortantes prouvées** contre de vrais
   récepteurs. **34 tests** verts, **gitleaks 0**. Limites honnêtes restantes :
   qualité d'extraction LLM dépendante du modèle (≥ 3B recommandé) ; STARTTLS
   couvert en test unitaire (pas encore contre un serveur STARTTLS réel).

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
relances, notifications, usage/FinOps, kill-switch) sont **implémentées et
validées de bout en bout** dans **`onix-actions`** (OCR de scans, extraction LLM
via Ollama, notifications webhook/SMTP, tâches sortantes — **prouvées** ; 34
tests, gitleaks 0) : un acquis, branché via Onyx Custom Actions.
