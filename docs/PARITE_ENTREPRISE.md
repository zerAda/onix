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

**Validation globale (sandbox)** : tests étendus (actions 70 · gateway **74** · RAG **116** + postfiltre) + **red-team E2E 21/21 par le code de service déployé** sur `qwen2.5:7b` + modes **Postgres/MinIO/Celery** prouvés multi-réplica + **manifests Helm validés server-side** (vrai kube-apiserver) · bandit 0 H/M · **pip-audit 0 CVE** · gitleaks 0 · `compose config` (base/prod/monitoring) · `helm lint` 0 · `caddy validate` OK.

**Clôture des 3 astérisques — preuves réelles renforcées :**
- ＊ **Garde-fous → LEVÉ (contrôle DÉPLOYÉ).** Post-filtre déterministe non-injectable **câblé dans l'`access-gateway`** (chemin réponse, après Onyx, avant l'utilisateur) et **prouvé E2E à travers le code de service** : **21/21** contre un vrai `qwen2.5:7b` (8 refus substitués par la gateway, **0 échec dur**) ; sécurité dure (anti-fuite prompt, non-exécution d'injection) **100 % dès le prompt seul**. *Reste* : booter le **retrieval Onyx natif** = **changement de config** (`GATEWAY_ONYX_BASE_URL`), pas de code. (`docs/E2E_GUARDRAILS.md`)
- ＊＊ **RBAC → cadré.** Décision **chiffrée** (`docs/DECISION_RBAC.md`) + gateway durcie (fail-closed, audit HMAC, 52 tests). Trimming **strict par-document** = **Onyx EE/Cloud** (limite produit) ; cloisonnement par groupe FOSS borne le risque.
- ＊＊＊ **HA → prouvé au maximum hors cluster vivant.** Manifests du chart **validés server-side par un vrai kube-apiserver** (52 objets, CRD CNPG comprises ; CR invalide **rejetée**) + **multi-réplica stateless prouvé** (kill-switch sur un pod → 403 servi par l'autre via Postgres partagé). *Reste* : kubelet vivant (bloqué par l'**env cgroup v1**, pas le chart) + failover/charge réelle. (`docs/HA_ACCEPTANCE.md`)

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
| **RBAC par utilisateur — RECHERCHE** (le LLM ne voit que les chunks autorisés) | **Permission sync** du connecteur SharePoint | ⚠️ **EE / Cloud uniquement** (FOSS : index par groupe, LLM voit tout le Document Set autorisé) |
| **RBAC par utilisateur — RÉPONSE** (citations rendues retirées si doc non autorisé pour l'appelant ; refus substitué si zéro citation restante) | Filtre [`doc_acl.py`](../access-gateway/app/doc_acl.py) dans la **passerelle FOSS**, **ACL auto-dérivée de SharePoint** via Graph ([`graph_acl.py`](../access-gateway/app/graph_acl.py), `make sync-doc-acl`) | ✅ **FOSS** (NOUVEAU `feat/rbac-perdoc` puis `feat/sharepoint-acl-sync`) — granularité **par document** sur la sortie, **désormais auto-synchronisée** depuis les permissions par item SharePoint (plus de JSON manuel), intégrée à l'audit HMAC |
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

1. **RBAC par document — RECHERCHE = EE/Cloud.** En FOSS, le filtre **par
   document est appliqué côté RÉPONSE** par la passerelle
   ([`access-gateway/app/doc_acl.py`](../access-gateway/app/doc_acl.py)) :
   citations vers les fichiers non-autorisés **retirées**, refus substitué si
   zéro citation restante. Cette ACL par-document est désormais **auto-dérivée
   des permissions réelles de SharePoint** via Microsoft Graph
   ([`access-gateway/app/graph_acl.py`](../access-gateway/app/graph_acl.py),
   `make sync-doc-acl` / TTL en vif) — **plus besoin de la maintenir à la
   main**, elle **suit la source** (un retrait d'accès disparaît au sync
   suivant). **Cela ferme la fuite VISIBLE** (citations affichées) **et
   l'automatise**. Le **trimming à la RECHERCHE** (le LLM ne voit jamais les
   chunks non autorisés — zéro fuite indirecte par le texte généré) **reste
   une fonction EE/Cloud** (permission sync, certificat) : la dérivation Graph
   automatise un filtre de SORTIE, elle n'en change pas la nature. Mitigations
   FOSS recommandées : périmètres conçus **homogènes**, ou instances Onyx
   **séparées par tier d'accès**. Détails et matrice :
   [`RBAC.md`](RBAC.md) §4.3/4.3 bis/4.4 + [`DECISION_RBAC.md`](DECISION_RBAC.md) §4.
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
