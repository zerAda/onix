# Comparatif — onix vs Microsoft Copilot vs AC360 (par secteur)

> Objectif annoncé : **égaler ou dépasser** un assistant commercial cloud type
> **Microsoft 365 Copilot / Copilot Studio** *et* le projet **AC360** (assistant
> Copilot Studio + Azure Functions), **en open-source, local et souverain**.
> Cette page est **honnête** : elle pointe les rares axes où la parité est
> *par configuration* (et non native), car un client exigeant ne tolère pas
> l'esbroufe. Les preuves sont liées à chaque ligne.

## Scorecard par secteur

| Secteur | Microsoft Copilot (M365 / Studio) | AC360 | **onix (Onyx + Ollama)** | Verdict onix |
|---|---|---|---|---|
| **Souveraineté / résidence des données** | Cloud Microsoft (EU Data Boundary, mais hors-site) | Cloud Azure (tenant client) | **100 % local** : inférence + index + fichiers sur site, **zéro transfert**, télémétrie off | 🟢 **Supérieur** |
| **Coût / licence** | ~par siège/mois + Power Platform pour les actions | Conso Azure (OpenAI, Functions, Fabric) | **Gratuit** (FOSS) ; coût = votre matériel | 🟢 **Supérieur** |
| **Contrôle du modèle** | Modèles OpenAI imposés, non substituables | Azure OpenAI (catalogue MS) | **N'importe quel modèle Ollama**, quantification/num_ctx/température réglables | 🟢 **Supérieur** |
| **RAG sourcé + citations** | Natif (Graph) | Natif (Fabric/OneLake + topics) | **Natif Onyx** (hybride vecteur+BM25, citations) | 🟢 **Parité** |
| **Réponses sourcées-only, mono-client, lecture seule** | Config Studio | Topics durcis | **Prompt système durci + Document Sets + post-filtre déterministe** | 🟢 **Parité+** |
| **Garde-fous LLM (OWASP LLM Top 10)** | Filtres managés (boîte noire) | Modération + topics | **Post-filtre déterministe DÉPLOYÉ** (hors-LLM, non-injectable) — **red-team 21/21** | 🟢 **Supérieur (auditable)** |
| **RBAC par document (trimming par utilisateur)** | **Natif** (permissions Graph/SharePoint) | **Natif** (Entra + OBO) | **Onyx EE/Cloud** *ou* cloisonnement par groupe (FOSS) via `access-gateway/` | 🟡 **Parité ＊ (EE) ou config** |
| **Connecteurs** | M365-natifs (SharePoint, Teams, Outlook…) | SharePoint/Fabric | **Catalogue Onyx** (SharePoint, Teams, Confluence, Drive, web…) | 🟢 **Parité+** |
| **Audit documentaire OCR (extraction + verdict)** | Power Platform / AI Builder (payant) | Azure Functions + OCR | **`onix-actions`** : OCR local (tesseract/poppler) → champs → verdict | 🟢 **Parité (local)** |
| **Génération de documents (.docx)** | Via Graph/Office (payant) | Functions + templates | **`onix-actions`** `POST /generate/fiche` (python-docx) | 🟢 **Parité (local)** |
| **Tâches / relances / notifications** | Planner / Power Automate (payant) | Functions + webhooks | **`onix-actions`** `/tasks`, `/notify` (webhook + SMTP, DLP egress) | 🟢 **Parité (local)** |
| **Usage / FinOps / kill-switch** | Admin M365 (analytics limités) | App Insights + budgets | **`onix-actions`** `/usage`, `/cost`, `/admin/control` (flags qui gatent réellement) | 🟢 **Parité+** |
| **Observabilité / éval qualité RAG** | Analytics admin (boîte noire) | App Insights | **Prometheus `/metrics`** (citation, no-context, P95, garde-fous) + **éval RAGAS** (`make rag-eval`) | 🟢 **Supérieur (ouvert)** |
| **Conformité RGPD (rétention, art. 17, registre, DPIA)** | Contrats MS + outils tenant | À la charge du projet | **Implémenté** : rétention/effacement, journal d'accès chaîné HMAC, registre + DPIA | 🟢 **Parité+ (souverain)** |
| **HA / scale-out** | SaaS managé (transparent) | Azure managé | **Helm HA** (OpenSearch/Postgres/MinIO/Redis HA, HPA, file Celery) — `helm lint` 0 | 🟡 **Parité (auto-géré)** |
| **Hors-ligne / air-gap** | ❌ impossible (cloud) | ❌ (cloud) | ✅ **Fonctionne 100 % hors-ligne** | 🟢 **Supérieur** |

**Synthèse** : onix **dépasse** Copilot et AC360 sur **souveraineté, coût, contrôle
du modèle, auditabilité des garde-fous, observabilité/éval et hors-ligne** ; il
atteint la **parité** sur le cœur RAG, les connecteurs et les fonctions
applicatives (en **local**, sans dépendances payantes). La **seule réserve de
fond** est le **RBAC fin par document** en édition gratuite.

## Les axes où Microsoft garde un avantage natif (honnêteté)

1. **RBAC par document clé-en-main** ＊ : la *permission sync* SharePoint d'Onyx
   est réservée à l'**EE/Cloud**. En FOSS, onix cloisonne **par groupe**
   (`access-gateway/`, deny-by-default, audit HMAC) ou par instance — suffisant
   pour des périmètres à accès homogène, mais pas le trimming par utilisateur
   automatique de Copilot. Détail : [`DECISION_RBAC.md`](DECISION_RBAC.md),
   [`RBAC.md`](RBAC.md), [`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md).
2. **Intégration M365 « zéro effort »** : Copilot vit dans Teams/Word/Outlook
   nativement. onix s'intègre via connecteurs + l'agent commercial, mais
   l'expérience « dans l'app Office » n'est pas l'objectif (assistant web dédié).
3. **Exploitation managée** : Copilot est un SaaS (rien à opérer). onix est
   **auto-hébergé** : vous gagnez la souveraineté, vous assumez l'exploitation
   (atténuée par `make`, Helm HA, monitoring, runbook).

## Pourquoi onix gagne pour un client souverain/exigeant

- **Aucune donnée ne sort** : le différenciateur décisif en secteur régulé
  (santé, finance, public, défense). Copilot et AC360 envoient les contenus au
  cloud ; onix **non**.
- **Coût marginal nul** par utilisateur/requête (pas de licence par siège).
- **Tout est auditable** : prompt système, post-filtre déterministe, métriques,
  éval RAGAS, manifests — vs des garde-fous/analytics en boîte noire.
- **Réversibilité** : pas de verrou propriétaire ; modèles et données vous
  appartiennent.

## Migrer d'AC360 vers onix

AC360 (Copilot Studio + Azure Functions + Fabric) → onix conserve **toutes** les
fonctions (RAG sourcé, audit OCR, génération, tâches, notifications, FinOps,
kill-switch) en les **rapatriant en local** dans `onix-actions` + Onyx. Le moteur
d'audit a été **porté à l'identique** (cf. [`ACTIONS.md`](ACTIONS.md)). Parité
détaillée et preuves : [`PARITE_ENTREPRISE.md`](PARITE_ENTREPRISE.md).

> En une phrase : **tout ce que Copilot/AC360 font, onix le fait en local et
> gratuit ; ce qu'onix fait en plus — souveraineté, contrôle, auditabilité,
> hors-ligne — Copilot ne peut pas l'offrir par construction.**
