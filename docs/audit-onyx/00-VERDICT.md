# VERDICT — Onyx v4.1.1 : prod-ready premium ou POC ?

> ⚠️ **PROVENANCE** — Onyx auditée en **v4.1.1** (commit `33613e1`), audit du
> **2026-06-18**, depuis un clone **externe** (`/tmp/onyx_v411`) **non vendoré**
> dans `onix`. Les citations `backend/…:ligne` / `ee/…:ligne` **ne sont PAS
> re-vérifiables depuis ce dépôt** (source secondaire sourcée, pas preuve directe).
> Détail : [`README.md`](README.md). Les mitigations `onix` citées, elles, sont
> vérifiables ici (cf. `docs/audit-reality/security-governance.md`).

> Synthèse de l'audit en profondeur (7 dimensions, code réel `/tmp/onyx_v411`,
> git **v4.1.1** commit `33613e1`, ~542K LOC Py + 1783 TS). Chaque section est
> citée `fichier:ligne` ; FOSS vs EE vs Cloud distingués ; **aucune donnée mockée**.

## Réponse en une phrase
**Onyx n'est PAS un POC.** C'est une **plateforme RAG d'ingénierie premium, prod-ready**
(542K LOC, 717 fichiers de test, 380 migrations Alembic **appliquées proprement en
live**, 37 workflows CI, 214 releases, backing commercial). **MAIS** le niveau
« **entreprise/premium** » est un **produit PAYANT (EE/Cloud)** : les fonctions qui
rendent un RAG *fiable pour un client régulé* — **permission-sync par document (RBAC),
RBAC de groupe, SSO complet, chiffrement au repos, audit/historique** — sont
**EE-payantes**, et l'**audit-trail “qui a vu quoi” n'existe dans AUCUNE édition**.
Sur **FOSS auto-hébergé seul**, Onyx n'est **pas turnkey** pour un client-360 RGPD
multi-utilisateur SharePoint — **c'est exactement le manque que `onix` comble**.

## Scorecard (preuves dans les sections liées)

| Dimension | Score | Verdict court |
|---|:--:|---|
| [Architecture & HA/scale](10-architecture-scalability.md) | **3,5/5** | Architecture stateless + indexation checkpointée excellentes ; **défauts HA du chart par défaut** (data-tier SPOF, course migration, beat singleton) |
| [Qualité · tests · CI/CD](20-code-quality-tests-ci.md) | **4/5** | 717 fichiers test (~74% du code), 380 migrations gated/PR, 37 workflows, `ty` strict, 0 bare-except. Manque : pas de gate de couverture, 463 `requests` sans timeout |
| [Sécurité](30-security.md) | **4/5** | AuthN FOSS forte ; **🔴 chiffrement des secrets OFF par défaut (creds en clair)** ; conteneurs root ; Custom Tools contournent l'anti-SSRF |
| [Connecteur SharePoint](40-sharepoint-connector-integration.md) | **4,5/5** | Connecteur premium (retry/délta/checkpoint, Graph 100% correct) ; **permission-sync EE-payante + cert-auth only** |
| [RGPD / gouvernance](50-rgpd-governance.md) | **2,5/5** | **Pas d'audit-trail (aucune édition)**, chiffrement contenu absent, **effacement art.17 cassé**, télémétrie ON par défaut |
| [Observabilité · runtime](60-observability-runtime.md) | **3,75/5** | **2 boots réels : 379 migrations OK → 137 tables** ; métriques Prometheus premium **mais** `/health` ment sur la readiness, OpenSearch bloquant au boot, probes k8s vides |
| [Santé OSS · licence](70-oss-health-licensing.md) | **4,5/5** | 214 releases, semver, 30k★, backing DanswerAI ; **mur de licence EE** (self-host EE = abonnement payant) |

**Moyenne plateforme : 3,8/5** (ingénierie = premium). **Fit “FOSS auto-hébergé
pour client-360 régulé” : ~2,5-3/5** (tiré vers le bas par RGPD + RBAC EE-payant).

## La ligne de faille décisive : FOSS (MIT) vs EE (payant) vs Cloud
Onyx ouvre le **cœur RAG** en MIT (chat agentique, 50+ connecteurs en *indexation*,
custom agents, MCP…) mais **paywalle la couche entreprise** (`backend/ee/LICENSE`,
gating runtime `fetch_versioned_implementation` / `PATH_PREFIX_MIN_TIER`) :

| Fonction entreprise | FOSS (gratuit) | EE/Cloud (payant) |
|---|:--:|:--:|
| **Permission-sync par document (RBAC SharePoint)** | ❌ (indexe tout, **0 ACL** → chacun voit tout) | ✅ EE (cert-auth) |
| RBAC de groupe / Curator | ❌ | ✅ EE |
| SSO OIDC/SAML complet | ⚠️ partiel | ✅ EE |
| Chiffrement des secrets au repos | ❌ (no-op, **clair**) | ✅ EE (secrets only) |
| Historique requêtes / usage | ❌ | ✅ EE |
| **Audit-trail “qui a vu quoi”** | ❌ | ❌ **(absent partout)** |
| SCIM, webhooks, whitelabel | ❌ | ✅ EE |
| Multi-tenant (schéma/tenant) | ❌ | ✅ Cloud |

## Preuves « no-mock » réelles obtenues
- **Runtime** : `alembic upgrade head` exécuté en live par **2 boots indépendants** →
  **379 révisions, 137 tables** (`60-…md`) — schéma *réel*, pas un POC. Le 2ᵉ boot a
  démarré `uvicorn` : il **bloque sur OpenSearch** (dépendance de boot dure) et
  **`/health` renvoie 200 avant d'être prêt** (readiness non fiable).
- **SharePoint** : probe **non-authentifiée réelle** sur la cible exacte —
  `GET /v1.0/sites/gerep75008.sharepoint.com:/sites/dev-assistant-client-360` →
  **HTTP 401 `InvalidAuthenticationToken`** (joignable, token requis) ; token endpoint
  `AADSTS7000216` (chemin client-credentials valide). **Ingestion réelle = EN ATTENTE de creds** (rien d'inventé).

## Risques bloquants pour CE projet (client-360 RGPD sur SharePoint, souverain)
1. **RBAC par document = EE-payant** : en FOSS, l'index ne reflète **aucune ACL
   SharePoint** → *tout utilisateur voit tout document indexé*. Inacceptable pour un
   assistant commercial cloisonné par client. (`40-…md`, `70-…md`)
2. **Aucun audit-trail** dans aucune édition → **non-conformité RGPD art.5(2)** (accountability). (`50-…md`)
3. **Secrets en clair par défaut** (FOSS no-op ; EE silencieux si clé vide) → creds connecteurs/LLM en clair en base. (`30-…md`, `50-…md`)
4. **Effacement art.17 cassé** (FK NOT NULL non gérées) → PII/email conservés. (`50-…md`)
5. **Télémétrie ON par défaut** (métadonnées ; EE divulgue le domaine email). (`50-…md`)
6. **Défauts HA & migration-race** du chart par défaut (data-tier SPOF, pas de lock). (`10-…md`, `60-…md`)

## Recommandation (3 voies honnêtes)
- **A — Onyx EE/Cloud (payant)** : obtient RBAC/permission-sync/SSO/chiffrement out-of-the-box.
  *Reste à compenser : audit-trail (absent même en EE), durcissement secrets/erasure, télémétrie.*
- **B — Onyx FOSS + couche de compensation `onix` (recommandé pour la souveraineté)** :
  garder le cœur RAG MIT et compenser **exactement** les manques bloquants par ce que
  `onix` apporte déjà — **access-gateway (cloisonnement + ACL par-doc), audit HMAC chaîné,
  redaction PII, DLP egress, rétention/effacement, télémétrie OFF, compose/Helm durcis,
  conteneurs non-root**. C'est précisément la raison d'être d'`onix`. *Limite assumée :
  l'ACL FOSS reste un filtre de sortie (cf. docs/RBAC.md), pas le trimming à la
  récupération d'EE.*
- **C — POC seul** : Onyx FOSS tel quel convient à un POC interne mono-périmètre, **pas**
  à une mise en prod régulée multi-clients.

## Pour compléter la preuve SharePoint en RÉEL (lever « EN ATTENTE de creds »)
Fournir dans la session **l'un** de : `AZURE_TENANT_ID`+`AZURE_CLIENT_ID`+`AZURE_CLIENT_SECRET`
(app Entra, `Sites.FullControl.All` pour la perm-sync — admin-consentie) **ou** un
`GRAPH_TOKEN`. La passerelle/le connecteur fera alors une **vraie ingestion** de
`dev-assistant-client-360` (sites → drives → items → permissions), sans mock.

## Limites de cet audit (honnêteté)
Clone *shallow* (pas d'historique PR/issues complet) ; `uvicorn` non démarré
(OpenSearch+model-server requis — non simulé) ; ingestion SharePoint authentifiée
non faite (aucun credential dans le conteneur) ; MCP Microsoft-Learn refusé par la
policy → docs Graph vérifiées via WebFetch de learn.microsoft.com ; analyse statique
(pas de DAST).
