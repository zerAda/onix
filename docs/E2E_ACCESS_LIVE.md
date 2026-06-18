# Runbook — e2e d'accès LIVE (SharePoint + Microsoft Fabric)

Ce runbook décrit, **bout-en-bout**, comment prouver **sur votre poste, contre
votre vrai tenant Entra**, que onix accède réellement à **SharePoint**
(Microsoft Graph) et à **Microsoft Fabric / OneLake / Power BI**, et que le
**RBAC est fail-closed**.

> **Honnêteté.** Ce harnais est **LIVE-ONLY** : il exige un **vrai tenant** (app
> Entra consentie + réglages tenant Fabric + rôles de workspace). Il ne « mocke »
> rien. Sans cible configurée, il **SKIP** proprement (code 2 — pas un échec).
> Il **réutilise** le code de service déployé (aucune réimplémentation) :
> [`../access-gateway/app/fabric_client.py`](../access-gateway/app/fabric_client.py),
> [`fabric_acl.py`](../access-gateway/app/fabric_acl.py),
> `graph_client.py`, `graph_acl.py`, `config.py`.
> Référence harnais : [`../access-gateway/tests/e2e/README_ACCESS_E2E.md`](../access-gateway/tests/e2e/README_ACCESS_E2E.md).

## (a0) Choisir le mode d'authentification

Le harnais accepte **deux modes** (variable `ONIX_E2E_AUTH`) ; l'accès Fabric est
**LECTURE SEULE, tables GOLD uniquement** dans les deux cas (cf.
[`connectors/FABRIC.md`](connectors/FABRIC.md) §1 bis).

| Mode | `ONIX_E2E_AUTH` | Identité | Secret en repo |
|---|---|---|---|
| **Azure CLI** (recommandé poste dev ; défaut si `az` présent) | `azcli` | `az login` | **aucun** |
| **Client secret** | `clientsecret` | SPN client credentials | `ONIX_E2E_CLIENT_SECRET` (hors-repo) |

### Procédure `az login` (mode azcli — zéro secret)

```bash
az login                                   # connecte votre identité Entra
az account show --query tenantId -o tsv    # vérifie le tenant courant
export ONIX_E2E_AUTH=azcli                 # explicite (sinon auto si `az` présent)
export ONIX_E2E_TENANT_ID=<votre-tenant>   # requis ; le reste vient de az login
```

Le harnais acquiert alors **tous** les jetons (Fabric, OneLake/storage, Power BI,
Graph) via `az account get-access-token` : **aucun `ONIX_E2E_CLIENT_ID` /
`ONIX_E2E_CLIENT_SECRET` n'est requis**. Votre identité `az login` doit avoir
les rôles/permissions ciblés (rôle de workspace Fabric, `Sites.Read.All` côté
Graph si vous testez aussi SharePoint). Le jeton n'est jamais journalisé.

## (a) Prérequis — à faire AVANT de lancer

À exécuter **sur votre poste az-connecté** (pas dans le conteneur). Détails de
scope Fabric : [`connectors/FABRIC.md`](connectors/FABRIC.md) ; SharePoint :
[`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md).

> En mode **azcli**, l'étape 1 ci-dessous (création du SPN + secret) est
> **facultative** : l'identité provient de `az login`. Elle reste utile en mode
> **clientsecret** ou pour un déploiement en service.

1. **App Entra (SPN) + permissions Graph + secret** (mode clientsecret / service).
   Lancez le script idempotent
   [`../scripts/setup-fabric-app.sh`](../scripts/setup-fabric-app.sh) :
   ```bash
   TENANT_ID=<votre-tenant> bash scripts/setup-fabric-app.sh
   ```
   Il crée/réutilise l'app `onix-fabric`, pose les permissions APPLICATION Graph
   (`Sites.Read.All`, `Files.Read.All`, `GroupMember.Read.All`), génère un secret
   (affiché **une fois**) et **rappelle** les réglages tenant ci-dessous. Notez
   `appId`, `tenant`, `secret` — **hors-repo** (jamais committé).

2. **Réglages TENANT Fabric / Power BI** (manuels, Admin portal — cf.
   [`connectors/FABRIC.md`](connectors/FABRIC.md) §3) :
   - activer **« Service principals can use Fabric APIs »** (Developer settings) ;
   - activer **« Service principals can use Power BI APIs »** (si bloc Power BI) ;
   - ajouter le SPN à un **rôle de workspace** : **Viewer** (contrôle) ou
     **Member**/Contributor (données OneLake).

3. **Consentement admin** des permissions Graph (fait par le script ; sinon
   Azure → API permissions → *Grant admin consent*).

4. **Cibles de test** (ids réels de votre tenant) à fournir en variables (table
   ci-dessous) : un site/drive/item SharePoint + un user autorisé et un refusé ;
   un workspace/item Fabric + un principal autorisé et un refusé.

## (b) Variables d'environnement `ONIX_E2E_*`

Le harnais ne contient **aucun secret** : tout vient de l'environnement. Un bloc
ne s'exécute que si **toutes** ses variables requises sont présentes ; sinon il
est **SKIP** (le reste tourne). Aucun jeton/secret n'est jamais journalisé.

### Mode d'auth (optionnel)

| Variable | Défaut | Sens |
|---|---|---|
| `ONIX_E2E_AUTH` | `azcli` si `az` présent, sinon `clientsecret` | mode d'auth ∈ {`azcli`, `clientsecret`} |

### Communes — REQUISES pour tout bloc

| Variable | Requis | Sens |
|---|---|---|
| `ONIX_E2E_TENANT_ID` | ✅ (tous modes) | GUID du tenant Entra (en azcli, déductible de `az account show`) |
| `ONIX_E2E_CLIENT_ID` | clientsecret | appId du SPN (client credentials) |
| `ONIX_E2E_CLIENT_SECRET` | clientsecret | secret du SPN (**jamais journalisé**) |

### Bloc A — SharePoint (toutes requises)

| Variable | Sens |
|---|---|
| `ONIX_E2E_SP_SITE_ID` | id du site (`host,siteGuid,webGuid`) |
| `ONIX_E2E_SP_DRIVE_ID` | id du drive (bibliothèque) |
| `ONIX_E2E_SP_ITEM_ID` | id du `driveItem` servant à la preuve RBAC |
| `ONIX_E2E_SP_USER_OK` | utilisateur **attendu autorisé** (oid ou UPN) |
| `ONIX_E2E_SP_USER_DENIED` | utilisateur **attendu refusé** (oid ou UPN) |

### Bloc B — Fabric (requises + optionnelles) — **GOLD-ONLY, lecture seule**

Le harnais câble le workspace/item ci-dessous en `GATEWAY_FABRIC_GOLD_*` : seules
les **tables gold** du lakehouse ciblé sont lisibles (cf.
[`connectors/FABRIC.md`](connectors/FABRIC.md) §1 bis). Un `ONIX_E2E_ONELAKE_PATH`
hors préfixe gold est **refusé** (fail-closed).

| Variable | Requis | Sens |
|---|---|---|
| `ONIX_E2E_FABRIC_WORKSPACE_ID` | ✅ | id du workspace **GOLD** |
| `ONIX_E2E_FABRIC_ITEM_ID` | ✅ | id du **lakehouse gold** |
| `ONIX_E2E_FABRIC_ITEM_TYPE` | ✅ | type d'item (ex. `Lakehouse`) |
| `ONIX_E2E_FABRIC_PRINCIPAL_OK` | ✅ | principal **attendu autorisé** (oid) |
| `ONIX_E2E_FABRIC_PRINCIPAL_DENIED` | ✅ | principal **attendu refusé** (oid) |
| `ONIX_E2E_ONELAKE_PATH` | optionnel | chemin à lire — **DOIT** être sous les tables gold |
| `ONIX_E2E_FABRIC_GOLD_TABLES_PREFIX` | optionnel | préfixe tables gold (défaut `Tables`) |
| `ONIX_E2E_PBI_WORKSPACE_ID` | optionnel | workspace Power BI à lister (active B3) |

### Réglages réseau (optionnels)

| Variable | Défaut | Sens |
|---|---|---|
| `ONIX_E2E_HTTP_TIMEOUT` | `20` | timeout HTTP (s) |
| `GATEWAY_GRAPH_HOST` / `GATEWAY_GRAPH_AUTHORITY` | publics | hôtes souverains Graph (Gov/China) |
| `GATEWAY_FABRIC_API_HOST` / `GATEWAY_ONELAKE_HOST` / `GATEWAY_POWERBI_HOST` | publics | hôtes souverains Fabric (cf. `config.py`) |

> Le harnais mappe `ONIX_E2E_TENANT_ID`/`_CLIENT_ID`/`_CLIENT_SECRET` vers les
> `GATEWAY_GRAPH_*` lus par `app.config` ; Fabric **hérite du même SPN** par
> défaut (cf. [`connectors/FABRIC.md`](connectors/FABRIC.md) §4). En mode
> **azcli**, il pose `GATEWAY_FABRIC_USE_AZCLI=true` (jetons via `az`, sans
> secret) et câble les `GATEWAY_FABRIC_GOLD_*` à partir des cibles Fabric.

## (c) Lancer + interpréter

```bash
# Rappel des variables, sans rien exécuter (affiche aussi le mode d'auth actif) :
python access-gateway/tests/e2e/run_access_e2e.py --list-vars

# Exemple A — mode azcli (zéro secret ; après `az login`) :
ONIX_E2E_AUTH=azcli ONIX_E2E_TENANT_ID=... \
ONIX_E2E_FABRIC_WORKSPACE_ID=... ONIX_E2E_FABRIC_ITEM_ID=... ONIX_E2E_FABRIC_ITEM_TYPE=Lakehouse \
ONIX_E2E_FABRIC_PRINCIPAL_OK=... ONIX_E2E_FABRIC_PRINCIPAL_DENIED=... \
    python access-gateway/tests/e2e/run_access_e2e.py

# Exemple B — mode clientsecret (SPN ; secret hors-repo) :
ONIX_E2E_AUTH=clientsecret \
ONIX_E2E_TENANT_ID=...        ONIX_E2E_CLIENT_ID=...   ONIX_E2E_CLIENT_SECRET=... \
ONIX_E2E_FABRIC_WORKSPACE_ID=... ONIX_E2E_FABRIC_ITEM_ID=... ONIX_E2E_FABRIC_ITEM_TYPE=Lakehouse \
ONIX_E2E_FABRIC_PRINCIPAL_OK=... ONIX_E2E_FABRIC_PRINCIPAL_DENIED=... \
    python access-gateway/tests/e2e/run_access_e2e.py
```

Scénarios (cf. README §Scénarios) : **A1** jeton Graph + listing drive ; **A2/A3**
RBAC SharePoint autorisé/refusé ; **B1** jeton Fabric + workspaces/items ; **B2**
jeton stockage + OneLake (+ lecture si `ONIX_E2E_ONELAKE_PATH`) ; **B3** Power BI
(si `ONIX_E2E_PBI_WORKSPACE_ID`) ; **B4/B5** RBAC Fabric autorisé/refusé.

### Codes de sortie

| Code | Signification | Action |
|---|---|---|
| `0` | tous les blocs présents **passés** (≥1 bloc exécuté) | ✅ accès + RBAC prouvés |
| `1` | au moins un scénario a **échoué** | lire la « preuve » du scénario en FAIL (voir dépannage) |
| `2` | **aucun bloc** n'avait ses variables (skip total) | **pas** un échec : définir les variables |

Dépannage rapide des FAIL :
- **A1 / B1 = FAIL** → SPN non habilité (consentement admin Graph manquant, ou
  « Service principals can use Fabric APIs » non activé / pas de rôle workspace),
  ou ids erronés.
- **B2 = FAIL** → rôle **données** insuffisant (Viewer ne lit pas OneLake : il
  faut **Member**/Contributor), ou `ONIX_E2E_FABRIC_ITEM_TYPE` / chemin faux, ou
  **chemin hors périmètre gold** (`ONIX_E2E_ONELAKE_PATH` doit viser une table
  sous le préfixe gold, défaut `Tables` — cf. `is_gold_path`).
- **A3 / B5 = FAIL** → **FUITE** : un non-autorisé a été accordé (alerte dure ;
  vérifiez les roleAssignments / permissions de l'item).
- **A2 / B4 = FAIL** → un autorisé a été refusé : vérifiez le rôle/permission du
  user/principal et la résolution de ses groupes (`GroupMember.Read.All`).

## (d) Lien avec la stack complète

Ce harnais d'accès est **indépendant** de la stack onix (il teste l'accès Entra
brut). Pour la **chaîne RAG complète** (gateway + Onyx + Ollama) :

```bash
make tune && make secrets && make up && make verify   # démarre + contrôle e2e
```

- **`make verify`** vérifie la stack montée (santé services, voir
  [`RUNBOOK.md`](RUNBOOK.md) / [`POC_LOCAL.md`](POC_LOCAL.md)).
- **Garde-fous bout-en-bout** : le harnais
  [`../access-gateway/tests/e2e/run_e2e.py`](../access-gateway/tests/e2e/run_e2e.py)
  rejoue 21 vecteurs **à travers le code déployé** (gateway → relais LLM → Ollama
  → post-filtre), prouvant le RBAC + les garde-fous sur la **réponse réelle**
  (cf. [`E2E_GUARDRAILS.md`](E2E_GUARDRAILS.md),
  [`LIVE_GUARDRAILS_RESULTS.md`](LIVE_GUARDRAILS_RESULTS.md)). C'est le pendant
  « génération » du présent harnais « accès ».
- **ACL SharePoint matérialisée** : une fois l'accès Graph prouvé, propagez les
  permissions par-document avec [`../scripts/sync-doc-acl.py`](../scripts/sync-doc-acl.py)
  (`make sync-doc-acl`) — cf. [`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md) §6.1.

## Ce qui exige un vrai tenant (résumé honnête)

| Étape | Mockable ? |
|---|---|
| Tests unitaires `fabric_client`/`fabric_acl` (offline) | ✅ provider de jeton injecté + `runner` az simulé (`access-gateway/tests`) |
| Validation `is_gold_path` / ACL gold-only (offline) | ✅ tests dédiés (autorise gold, refuse hors-gold) |
| `run_access_e2e.py` (A1..B5) | ❌ **vrai tenant** (jeton réel via `az login` ou SPN, vrais ids/rôles) |
| Réglages tenant Fabric / rôles workspace | ❌ Admin portal, votre tenant |
| Garde-fous `run_e2e.py` (21 vecteurs) | ❌ Ollama réel (LLM local), stack montée |

Voir aussi : [`connectors/FABRIC.md`](connectors/FABRIC.md) ·
[`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md) · [`RBAC.md`](RBAC.md) ·
[`DECISION_RBAC.md`](DECISION_RBAC.md).

---

## Annexe — Cloisonnement PAR-CLIENT (POC « Assistant Client 360 »)

Scénario réel GEREP : le site SharePoint `dev-assistant-client-360` contient
`Dossiers_Clients_POC/` avec un dossier **par client** (Alpha, Beta, Gamma…).
Objectif : **chaque gestionnaire ne voit que SES dossiers clients**, un **manager**
voit tout — prouvé à **deux niveaux** à travers le code déployé.

### Modèle d'accès — groupes Entra (pas siteGroups)

| Principal | Type | Portée |
|---|---|---|
| `SG …-CLIENT-ALPHA` (1 par client) | **groupe de sécurité Entra** | dossier du client |
| `SG …-MANAGERS-READALL` | **groupe de sécurité Entra** | tous les dossiers |

> **Pourquoi Entra et NON les SharePoint siteGroups.** La passerelle résout
> l'appartenance via Microsoft Graph `transitiveMemberOf` → des **GUID Entra**.
> Les **siteGroups** SharePoint ont des **ids entiers** (`17`, `11`…) : `graph_acl`
> les capte (`siteGroup.id`) mais ils ne **matchent jamais** un GUID Entra. Partager
> les dossiers via des **groupes de sécurité Entra** (ajoutés *directement* aux
> permissions de l'item → `grantedToV2.group.id`) rend le RBAC **résoluble** par
> onix. Cf. [`DECISION_RBAC.md`](DECISION_RBAC.md).

### Niveau 1 — RBAC par-document (SharePoint / Graph)

`graph_acl.fetch_item_principals` lit l'ACL réelle de chaque document ; un user est
autorisé si son `oid` est cité OU si l'un de ses groupes Entra transitifs ∩ les
groupes de l'item. Résultat attendu (matrice user × dossier) : **diagonale** pour
les clients, **ligne pleine** pour le manager.

### Niveau 2 — Cloisonnement au CHAT (Document Set forcé)

La passerelle dérive du **mapping groupe Entra → Document Set** la liste des sets
autorisés et **force** ce filtre dans `retrieval_options` avant de relayer à Onyx —
un user d'un autre périmètre ne peut pas élargir la recherche. Mapping
(`GATEWAY_MAPPING_PATH`, cf. [`RBAC.md`](RBAC.md)) :

```json
{
  "version": 1,
  "default_document_sets": [],
  "groups": {
    "<GUID groupe Entra CLIENT-ALPHA>": {"document_sets": ["clients-alpha"]},
    "<GUID groupe Entra CLIENT-BETA>":  {"document_sets": ["clients-beta"]},
    "<GUID groupe Entra CLIENT-GAMMA>": {"document_sets": ["clients-gamma"]},
    "<GUID groupe Entra MANAGERS>":     {"document_sets": ["clients-alpha","clients-beta","clients-gamma"]}
  }
}
```

### Rejouer sur le poste

1. **Partager** chaque dossier client avec son groupe Entra (lecture) + le groupe
   manager (lecture) — *en direct*, pour un grant `group.id` résoluble.
2. **Indexer** le site dans Onyx (connecteur SharePoint) puis **créer un Document
   Set par client** (`clients-alpha`…) regroupant le dossier correspondant.
   > FOSS : Onyx indexe le contenu mais **ne synchronise pas** les ACL SharePoint
   > (permission-sync = EE). C'est **onix** qui cloisonne (filtre `document_set` +
   > ACL par-document à la réponse). Cf. [`RBAC.md`](RBAC.md), [`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md) §6.
3. **Pointer** `GATEWAY_MAPPING_PATH` sur le mapping ci-dessus, puis lancer le chat
   **en tant que** chaque utilisateur (SSO) : vérifier qu'il ne récupère/cite que
   les documents de son périmètre.

### Mockable ?

| Étape | Mockable ? |
|---|---|
| RBAC par-document & matrice (Graph live) | ❌ vrai tenant (groupes Entra + items partagés) |
| Forçage `document_set` au chat (logique passerelle) | ✅ **prouvable hors-Onyx** (claims + mapping, amont moqué) |
| Indexation + retrieval réels honorant le filtre | ❌ stack Onyx montée (poste) |
