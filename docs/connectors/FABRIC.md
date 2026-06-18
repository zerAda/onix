# Connecteur Microsoft Fabric — accès workspaces / OneLake / Power BI

onix accède à **Microsoft Fabric** (contrôle), à **OneLake** (données ADLS Gen2)
et à **Power BI** (datasets) via un **service principal (SPN)** Entra, en
**client credentials (app-only)**. C'est le pendant Fabric du RBAC SharePoint
par-document déjà en place (`graph_client`/`graph_acl`) : énumération,
lecture, et **décision d'autorisation fail-closed** (`fabric_acl`).

> **Vérification des faits.** Les permissions, scopes et réglages tenant
> ci-dessous proviennent du brief de scope (faits Microsoft Learn). La
> re-vérification via le **Microsoft Learn MCP** prévue **n'a pas pu être
> exécutée** (outil refusé par la politique de permissions de l'environnement —
> même situation que [`SHAREPOINT.md`](SHAREPOINT.md) §6.1 et
> [`../DECISION_RBAC.md`](../DECISION_RBAC.md) §3). **Re-vérifiez sur
> learn.microsoft.com** (« Fabric REST API », « OneLake access », « Service
> principal Fabric ») avant un déploiement sensible. Les scopes/endpoints
> reflètent le code réel : [`../../access-gateway/app/fabric_client.py`](../../access-gateway/app/fabric_client.py).

## 1. Architecture d'accès — trois surfaces, trois audiences

Fabric n'est PAS une seule API : chaque surface a **sa ressource OAuth2** (donc
son **scope `<audience>/.default`** en app-only). Le SPN acquiert un jeton
**par audience** (`fabric_client.acquire_token`,
[`fabric_client.py:70`](../../access-gateway/app/fabric_client.py)) ; les jetons
sont mémoïsés par audience pour la durée du client.

| Surface | Rôle | Audience / scope jeton | Hôte (Settings) |
|---|---|---|---|
| **Contrôle Fabric** | workspaces, items, **roleAssignments** (RBAC de contrôle) | `https://api.fabric.microsoft.com/.default` | `GATEWAY_FABRIC_API_HOST` (`api.fabric.microsoft.com`) |
| **OneLake (données)** | listing / lecture des fichiers (ADLS Gen2 DFS) | `https://storage.azure.com/.default` | `GATEWAY_ONELAKE_HOST` (`onelake.dfs.fabric.microsoft.com`) |
| **Power BI** | datasets | `https://analysis.windows.net/powerbi/api/.default` | `GATEWAY_POWERBI_HOST` (`api.powerbi.com`) |
| **Graph (SharePoint)** | ACL par-doc SharePoint (rappel, autre module) | `https://graph.microsoft.com/.default` | `GATEWAY_GRAPH_HOST` (`graph.microsoft.com`) |

Constantes d'audience :
[`fabric_client.py:49-51`](../../access-gateway/app/fabric_client.py). Endpoint
OneLake DFS :
`https://onelake.dfs.fabric.microsoft.com/{workspace}/{item}.{type}/{path}`
(`onelake_read_file`,
[`fabric_client.py:324`](../../access-gateway/app/fabric_client.py)).

> Les **hôtes** sont des **constantes d'exploitation** (issues des Settings, pas
> d'entrée utilisateur) → pas de SSRF. Pour un cloud **souverain** (Gov/China),
> surchargez `GATEWAY_FABRIC_API_HOST` / `GATEWAY_ONELAKE_HOST` /
> `GATEWAY_POWERBI_HOST` / `GATEWAY_FABRIC_AUTHORITY`
> ([`config.py:254-264`](../../access-gateway/app/config.py)).

## 1 bis. Lecture seule + périmètre GOLD (fail-closed)

Deux garde-fous **non négociables** encadrent l'accès Fabric d'onix :

### Read-only by design

`fabric_client.py` n'émet **que des GET** : aucune méthode POST/PUT/PATCH/DELETE
n'existe ni ne doit être ajoutée (commentaire explicite « read-only by design » en
tête du module,
[`fabric_client.py`](../../access-gateway/app/fabric_client.py)). onix ne **modifie
jamais** Fabric / OneLake / Power BI.

### Tables GOLD uniquement

L'accès **données OneLake** est restreint à **UN lakehouse « gold » précis** dans
**UN workspace précis**, et **sous l'arbre des tables gold** (préfixe `Tables` par
défaut, surchargeable en `Tables/gold` ou un schéma `gold`). La validation
centrale `is_gold_path(settings, workspace, item, item_type, path)`
([`fabric_client.py`](../../access-gateway/app/fabric_client.py)) **refuse**
(fail-closed) tout chemin :

- hors du **workspace gold** (`GATEWAY_FABRIC_GOLD_WORKSPACE_ID`/`_NAME`) ;
- hors du **lakehouse gold** (`GATEWAY_FABRIC_GOLD_LAKEHOUSE_ID`/`_NAME`) ;
- d'un **type d'item** différent (`Lakehouse` par défaut) ;
- hors du **préfixe des tables gold** (ex. `Files/...` = données brutes → REFUSÉ).

`onelake_list_paths` (sans sous-chemin → racine des tables gold) et
`onelake_read_file` appellent cette garde **avant tout appel réseau** : un chemin
hors-gold lève `FabricError` sans toucher au réseau. Côté décision,
`fabric_acl.can_principal_read`/`authorized_items` **n'accordent que** pour le
lakehouse gold (`item_in_gold_scope`,
[`fabric_acl.py`](../../access-gateway/app/fabric_acl.py)) — un item hors gold est
refusé **même si un rôle l'autoriserait**.

**Défaut INERTE** : si le gold n'est pas configuré (`fabric_gold_configured`
False — il manque le workspace OU le lakehouse), **aucun** accès OneLake n'est
accordé. Le gold se câble par les variables `GATEWAY_FABRIC_GOLD_*` (cf. §4).

## 2. Modèle RBAC — deux sources de vérité, OR-mergées, fail-closed

`fabric_acl.can_principal_read(...)` répond : « le principal P (+ ses groupes
Entra) peut-il LIRE l'item I du workspace W ? »
([`fabric_acl.py:149`](../../access-gateway/app/fabric_acl.py)).

### (a) roleAssignments du workspace — RBAC de **contrôle** Fabric

Source primaire. Un **rôle de workspace** qui confère **au moins la lecture**
est exigé, attribué soit **directement** au principal, soit à un **groupe Entra**
dont il est membre (`principal.group_ids`). Les **quatre rôles Fabric** donnent
au moins la lecture des items du workspace :

| Rôle de workspace | Lecture contrôle | Lecture données OneLake |
|---|---|---|
| **Viewer** | ✅ | ❌ (selon réglages ; viewer = consommation, pas accès fichiers bruts) |
| **Contributor** | ✅ | ✅ |
| **Member** | ✅ | ✅ |
| **Admin** | ✅ | ✅ |

Ensemble `_READ_ROLES` (liste EXPLICITE, casse-insensible) :
[`fabric_acl.py:48`](../../access-gateway/app/fabric_acl.py). Lecture des
assignments : `list_workspace_role_assignments`
([`fabric_client.py:247`](../../access-gateway/app/fabric_client.py)).

> **Conséquence pratique.** Pour que onix **lise les fichiers OneLake**, donnez
> au SPN le rôle **Member** (ou Contributor) du workspace — pas seulement Viewer.
> Pour la seule énumération (workspaces/items/roleAssignments), **Viewer** suffit.

### (b) principalAccess OneLake (securityPolicy) — accès **effectif** fin (PREVIEW)

Source d'**élargissement** uniquement. Endpoint
`…/v1.0/workspaces/{ws}/artifacts/{artifact}/securityPolicy/principalAccess`
(`get_principal_effective_access`,
[`fabric_client.py:353`](../../access-gateway/app/fabric_client.py)). Si elle est
disponible **et** accorde la lecture, elle autorise ; si elle est
**indisponible** (404 PREVIEW sur le tenant) ou **403** (SPN non habilité), elle
est **ignorée** — jamais une erreur n'accorde un accès. Interprétation
défensive : [`fabric_acl.py:115`](../../access-gateway/app/fabric_acl.py).

### Discipline FAIL-CLOSED

- Toute erreur d'appel, format inattendu, ou information manquante ⇒ **refus**
  (on n'invente pas un accès sur une donnée non vérifiée).
- (a) est **requise** pour un « oui » par défaut ; (b) ne fait qu'**élargir**
  (jamais restreindre un oui de (a), jamais accorder seule si elle a échoué).
- Fabric **non configuré** (pas de tenant/client/secret) ⇒ aucun appel, refus
  (`fabric_configured`,
  [`config.py:170-175`](../../access-gateway/app/config.py)).

`authorized_items(...)` lit les roleAssignments **une seule fois** par workspace
(décision workspace-level) et n'interroge OneLake que par item
([`fabric_acl.py:217`](../../access-gateway/app/fabric_acl.py)).

## 3. Prérequis tenant (NON automatisables par script)

Le SPN **n'a pas de scope délégué** pour Fabric : son accès est régi par les
**contrôles admin du tenant** + les **rôles d'artefact/workspace**. Trois
réglages à poser **à la main** (Admin portal — aucune API `az` standard) :

1. **« Service principals can use Fabric APIs »** — Admin portal → **Tenant
   settings** → **Developer settings**. Sans lui : 401/403 sur tout appel
   Fabric. Restreignez-le idéalement à un **groupe de sécurité** contenant le
   SPN.
2. **Rôle de workspace Fabric** — ajoutez le SPN au workspace (Manage access) :
   **Viewer** (lecture contrôle) ou **Member**/Contributor (lecture données
   OneLake). Cf. §2.
3. **« Service principals can use Power BI APIs »** (si datasets Power BI) +
   accès du SPN au workspace Power BI ciblé.

Côté **app Entra**, les permissions APPLICATION Graph (pour le RBAC SharePoint
par-document du même SPN) sont, elles, automatisables :
`Sites.Read.All` (ou `Sites.Selected` + octroi par site — moindre privilège),
`Files.Read.All`, `GroupMember.Read.All`, **consentement admin requis**. Le
script [`../../scripts/setup-fabric-app.sh`](../../scripts/setup-fabric-app.sh)
crée l'app, pose ces permissions, génère le secret et **rappelle** les trois
réglages tenant ci-dessus.

## 4. Comment onix l'utilise

- **Lecture** : `FabricClient`
  ([`fabric_client.py:113`](../../access-gateway/app/fabric_client.py)) —
  `list_workspaces`, `list_items`, `list_workspace_role_assignments`,
  `onelake_list_paths`, `onelake_read_file`, `get_principal_effective_access`,
  `list_powerbi_datasets`. Pagination propre à chaque API (Fabric
  `continuationToken`, OData `@odata.nextLink`, ADLS `x-ms-continuation`).
- **Décision** : `fabric_acl.can_principal_read` / `authorized_items`
  ([`fabric_acl.py:149`](../../access-gateway/app/fabric_acl.py),
  [`:217`](../../access-gateway/app/fabric_acl.py)) — fail-closed (cf. §2).
- **Identifiants** : par défaut le **même SPN que Graph** (on ne duplique pas un
  secret) ; overrides dédiés `GATEWAY_FABRIC_*` si le SPN Fabric diffère
  ([`config.py:247-266`](../../access-gateway/app/config.py)).

### Variables d'environnement (passerelle)

| Variable | Défaut | Rôle |
|---|---|---|
| `GATEWAY_FABRIC_TENANT_ID` | repli `GATEWAY_GRAPH_TENANT_ID` | tenant Entra du SPN Fabric |
| `GATEWAY_FABRIC_CLIENT_ID` | repli `GATEWAY_GRAPH_CLIENT_ID` | appId du SPN Fabric |
| `GATEWAY_FABRIC_CLIENT_SECRET` | repli `GATEWAY_GRAPH_CLIENT_SECRET` | secret (env uniquement, jamais journalisé) |
| `GATEWAY_FABRIC_AUTHORITY` | repli `GATEWAY_GRAPH_AUTHORITY` | autorité OAuth2 (souverain : surcharger) |
| `GATEWAY_FABRIC_API_HOST` | `https://api.fabric.microsoft.com` | hôte contrôle Fabric |
| `GATEWAY_ONELAKE_HOST` | `https://onelake.dfs.fabric.microsoft.com` | hôte OneLake DFS |
| `GATEWAY_POWERBI_HOST` | `https://api.powerbi.com` | hôte Power BI |
| `GATEWAY_FABRIC_WORKSPACE_ID` | (vide) | workspace par défaut optionnel |
| `GATEWAY_FABRIC_ITEM_ID` | (vide) | item par défaut optionnel |
| `GATEWAY_FABRIC_GOLD_WORKSPACE_ID` | (vide) | **workspace gold** (id/GUID) — périmètre lecture |
| `GATEWAY_FABRIC_GOLD_WORKSPACE_NAME` | (vide) | workspace gold (nom, alternative à l'id) |
| `GATEWAY_FABRIC_GOLD_LAKEHOUSE_ID` | (vide) | **lakehouse gold** (item id/GUID) |
| `GATEWAY_FABRIC_GOLD_LAKEHOUSE_NAME` | (vide) | lakehouse gold (nom, alternative à l'id) |
| `GATEWAY_FABRIC_GOLD_LAKEHOUSE_TYPE` | `Lakehouse` | type d'item gold (toujours Lakehouse en pratique) |
| `GATEWAY_FABRIC_GOLD_TABLES_PREFIX` | `Tables` | préfixe des tables gold (ex. `Tables/gold`, schéma `gold`) |
| `GATEWAY_FABRIC_USE_AZCLI` | `false` | si `true`, jetons via Azure CLI (`az login`) au lieu du client_secret |

> **Périmètre GOLD requis pour OneLake.** Renseignez **au moins** un id/nom de
> workspace gold **et** un id/nom de lakehouse gold (`fabric_gold_configured`).
> Sans cela, aucune lecture OneLake n'est accordée (défaut inerte, cf. §1 bis).

> **Zéro secret en repo.** Posez `GATEWAY_FABRIC_CLIENT_SECRET` (ou son repli
> Graph) en variable d'environnement / coffre — jamais en clair dans le dépôt.
> **Alternative sans secret : Azure CLI.** Avec `GATEWAY_FABRIC_USE_AZCLI=true`,
> le client acquiert ses jetons via `az account get-access-token` (identité de
> `az login`) — aucun client_secret n'est requis ni stocké
> (`acquire_token_via_azcli`,
> [`fabric_client.py`](../../access-gateway/app/fabric_client.py) ; appel
> `subprocess` avec une **liste d'arguments fixe**, jamais `shell=True` ; le jeton
> n'est jamais journalisé). Pratique en e2e LIVE sur un poste az-connecté ; en
> service, on privilégie le SPN client-credentials (ou une identité managée).

### Preuve e2e LIVE

Le harnais [`../../access-gateway/tests/e2e/run_access_e2e.py`](../../access-gateway/tests/e2e/run_access_e2e.py)
(bloc B) prouve, contre un vrai tenant : connectivité contrôle/OneLake/Power BI
+ RBAC fail-closed (un principal autorisé accordé, un non-autorisé refusé).
Runbook : [`../E2E_ACCESS_LIVE.md`](../E2E_ACCESS_LIVE.md) ;
README : [`../../access-gateway/tests/e2e/README_ACCESS_E2E.md`](../../access-gateway/tests/e2e/README_ACCESS_E2E.md).

## 5. FOSS vs EE — frontière et limites honnêtes

Comme tout le RBAC FOSS d'onix (cf. [`../RBAC.md`](../RBAC.md) §4.4,
[`SHAREPOINT.md`](SHAREPOINT.md) §6.2), l'accès Fabric d'onix est un **filtre de
SORTIE** : il décide **quelles identités peuvent VOIR** un item Fabric / OneLake
(sa **citation**), pas ce que le LLM a récupéré pendant la génération. Il rend le
filtre **automatique** (dérivé des permissions réelles), il **ne change pas sa
nature**.

| Capacité | onix FOSS (filtre de sortie) | Fabric / Onyx EE (contrôle natif) |
|---|---|---|
| Décision « qui peut voir » | ✅ roleAssignments + principalAccess (fail-closed) | ✅ |
| **Lecture seule** (aucune écriture) | ✅ que des GET (read-only by design) | n/a |
| **Périmètre GOLD uniquement** (tables gold d'un lakehouse) | ✅ `is_gold_path` fail-closed | ✅ (réglable) |
| Trimming **au niveau du stockage** (zéro-fuite à la recherche) | ❌ (filtre de sortie) | ✅ |
| Accès effectif fin OneLake | ⚠ **PREVIEW** (404 fréquent → ignoré, fail-closed) | ✅ data access roles GA |

Limites à garder en tête :

- **principalAccess en PREVIEW** : indisponible (404) sur de nombreux tenants ;
  onix dégrade alors **sans accorder** d'accès (retombe sur les roleAssignments).
- **`siteGroup` SharePoint ≠ groupe Entra** (côté ACL SharePoint, cf.
  [`SHAREPOINT.md`](SHAREPOINT.md)) : un SPN n'a pas non plus de groupes Entra
  transitifs « utilisateur » — la décision Fabric par **roleAssignment direct**
  reste valide même si la résolution de groupes échoue.
- **Propagation différée** : un retrait de rôle de workspace n'est visible
  qu'au prochain appel / sync (jamais instantané — comme en EE).
- **Zéro-fuite strict à la RECHERCHE** = Fabric/Onyx **EE/Cloud** ou des
  **instances séparées par tier d'accès**, pas le filtre de sortie FOSS.

> Documentez le choix retenu : c'est la **réserve n°1** d'un audit de sécurité.
> Voir aussi [`../RBAC.md`](../RBAC.md) et
> [`../DECISION_RBAC.md`](../DECISION_RBAC.md).
