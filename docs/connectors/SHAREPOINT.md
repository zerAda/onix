# Connecteur SharePoint — RAG sur vos documents clients

onix se connecte à **SharePoint Online** via le **connecteur SharePoint natif
d'Onyx** (API Microsoft Graph). Les documents sont indexés (vecteur + lexical)
puis interrogeables par l'agent commercial, **réponses sourcées** à l'appui.

> **Versions** : faits vérifiés sur **Onyx `4.1.1`** (image épinglée du compose).
> Connecteur : `backend/onyx/connectors/sharepoint/connector.py`.

## 0. Ce qui a changé vs anciennes versions (corrections importantes)

- ✅ **Les pages SharePoint SONT supportées** (fichiers `.aspx`). L'ancienne note
  « ne parse pas encore les pages » est **obsolète** : depuis l'ajout des options
  pages/documents, le connecteur indexe **les deux par défaut** (cf. §4.1).
- ✅ **La sélection par site fonctionne quelle que soit la langue du tenant.** La
  limite « EN/ES/DE uniquement » ne concerne **que** la résolution automatique de
  la **bibliothèque par défaut** (« Documents partagés »), pas la sélection du
  site. Pour un **tenant FR**, voir le contournement §4.2.

## 1. Deux modes — choisir selon le besoin de sécurité

| Mode | Auth | Trimming par document (permission sync) | Édition Onyx | Privilèges Graph |
|---|---|---|---|---|
| **A. Lecture seule simple** | Client secret **ou** certificat | ❌ index **partagé** (cf. ⚠ ci-dessous) | **FOSS** (gratuit) | minimaux |
| **B. Parité entreprise (RBAC par document)** | **Certificat (obligatoire)** | ✅ chaque user ne voit que ses documents | **Cloud / Enterprise Edition** | élevés |

> ⚠ **Point de fond.** Le *trimming par document* (cœur de la sécurité d'un
> assistant d'entreprise) repose sur la **permission sync**, **disponible
> uniquement en Onyx Cloud / EE** (et **exige le certificat**). En **FOSS**,
> l'index est partagé — mais onix réintroduit un **cloisonnement par groupe /
> Document Set** via SSO OIDC + la passerelle `access-gateway/`. **Lire
> [`../RBAC.md`](../RBAC.md)** : c'est LA réserve à cadrer avec le client.

## 2. App registration Microsoft Entra ID

1. Portail Azure → **App registrations** → **New registration**
   (ex. « onix SharePoint Connector »). Type mono-tenant.
2. Noter **Application (client) ID** et **Directory (tenant) ID**.
3. Selon le mode :
   - **A. Client secret** : *Certificates & secrets* → **New client secret** → noter la valeur.
   - **B. Certificat** (requis pour la permission sync) : générer un certificat
     (auto-signé OK) au format **PFX** et l'**uploader** dans *Certificates &
     secrets → Certificates*. Exemple OpenSSL :
     ```bash
     openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 730 -nodes \
       -subj "/CN=onix-sharepoint"
     openssl pkcs12 -export -out onix-sp.pfx -inkey key.pem -in cert.pem   # -> upload Azure
     ```

## 3. Permissions Microsoft Graph (moindre privilège)

### Mode A — lecture seule, **principe de moindre privilège**

Préférer **`Sites.Selected`** (Application) : l'app n'accède qu'aux **sites
explicitement autorisés**, pas à tout le tenant. C'est la permission **la moins
privilégiée** pour SharePoint.

- *Application* : **`Sites.Selected`**
- *Delegated* : `User.Read`

Puis **accorder le site** à l'app (une fois `Sites.Selected` consentie). Exemple
(rôle `read` sur un site précis) :
```http
POST https://graph.microsoft.com/v1.0/sites/{site-id}/permissions
Content-Type: application/json

{
  "roles": ["read"],
  "grantedToIdentities": [
    { "application": { "id": "<APP_CLIENT_ID>", "displayName": "onix SharePoint Connector" } }
  ]
}
```

> 🔒 **Piège « additif ».** Les permissions Graph sont **additives** et la **plus
> permissive l'emporte**. Si vous laissez **`Sites.Read.All`** ou
> **`Files.Read.All`** consenties **en plus** de `Sites.Selected`, le cadrage par
> site **ne s'applique plus** (l'app voit tout). Pour un vrai moindre privilège :
> **`Sites.Selected` SEULE**, sans `Sites.Read.All`/`Files.Read.All`.
> (`Sites.Selected` est *site-scoped* ; drives et fichiers héritent de la
> restriction puisqu'ils appartiennent au site.)

### Mode B — certificat + permission sync (RBAC par document, **EE**) — *Application*
- `Sites.Read.All` (ou `Sites.Selected` + octrois)
- `Directory.Read.All`, `Group.Read.All`, `GroupMember.Read.All`, `Member.Read.Hidden`, `User.Read.All`
- *Delegated* : `User.Read`
- **SharePoint** (API « Office 365 SharePoint Online ») : `Sites.FullControl.All`, `User.Read.All`

> Ces droits plus larges servent à **reconstituer les ACL** (qui ↔ groupe ↔
> document) pour le trimming. Si vous n'avez **pas** besoin du RBAC par document,
> **restez en Mode A** (bien moins de droits ; pas de `Sites.FullControl.All`).
> Pour le cloisonnement **par groupe** en FOSS, voir [`../RBAC.md`](../RBAC.md).

4. **Grant admin consent** (obligatoire) sur toutes les permissions ajoutées.

## 4. Configurer le connecteur dans Onyx

**Admin → Connectors → SharePoint → Set up** :
- **Connector Name** : ex. `clients-sharepoint`.
- **Sites** : liste d'**URL de sites complètes**, ou **vide** = tout le tenant.
  Format attendu (validé par le connecteur) :
  `https://<tenant>.sharepoint.com/sites/<site>` ou `…/teams/<team>`.
- **Auth** : *Client Secret* (App ID, Directory ID, Secret) **ou** *Certificate*.
- **Permission Sync** : **uniquement** en mode **certificat** + édition **Cloud/EE**.
- Lancer l'indexation, puis **regrouper le connecteur dans un Document Set**
  (`Admin → Document Sets`) que l'agent utilisera (cf. `../AGENT_COMMERCIAL.md` ;
  pour le cloisonnement par groupe, **un Document Set par périmètre**, cf.
  [`../RBAC.md`](../RBAC.md) §4).

### 4.1 Pages SharePoint vs documents — activer / désactiver

Le connecteur **4.1.1** expose deux options (toutes deux **activées par défaut**) :

| Option (UI) | Champ connecteur | Défaut | Effet |
|---|---|---|---|
| **Include Site Documents** | `include_site_documents` | **`True`** | Indexe les fichiers des bibliothèques (PDF, Office…). |
| **Include Site Pages** | `include_site_pages` | **`True`** | Indexe les **pages** du site (fichiers `.aspx`). |

- **Au moins une** des deux doit être active (sinon le connecteur refuse :
  *« At least one content type must be enabled »*).
- **Pour n'indexer que les documents** (désactiver les pages) : **décocher
  *Include Site Pages*** à la configuration du connecteur.
- ⚠ **Portée des pages.** Quand *Include Site Pages* est actif, **toutes** les
  pages `.aspx` des sites visés sont indexées : cela **ignore** les restrictions
  de drive/dossier que vous auriez posées par ailleurs. Si vous cloisonnez
  finement par bibliothèque, **désactivez les pages**.

> Note d'implémentation : `process_site_pages` (vu dans le code) est un **drapeau
> d'état interne** du *checkpoint* d'indexation (l'indexation traite d'abord les
> documents, puis **bascule** `process_site_pages=True` pour traiter les pages).
> **Ce n'est pas** le réglage utilisateur — le réglage est **Include Site Pages**
> (`include_site_pages`). Ne pas confondre les deux.

### 4.2 Tenant **FR** — contourner `SHARED_DOCUMENTS_MAP`

Pour résoudre la bibliothèque **par défaut** d'un site, le connecteur traduit le
nom localisé via une table figée — qui ne couvre **que EN / DE / ES** :
```python
SHARED_DOCUMENTS_MAP = {
    "Documents":  "Shared Documents",        # EN
    "Dokumente":  "Freigegebene Dokumente",  # DE
    "Documentos": "Documentos compartidos",  # ES
}
```
Un tenant **français** (bibliothèque « **Documents partagés** ») n'y figure pas :
la **résolution automatique** de la bibliothèque par défaut peut échouer.
**Contournements** (par ordre de préférence) :

1. **Cibler la bibliothèque par son nom (drive_name).** Le connecteur accepte une
   **URL de site incluant la bibliothèque** ; il résout alors le **drive** par son
   nom (`drive_name`, **insensible à la casse**) sans dépendre de la table.
   Indiquez l'URL pointant la bibliothèque FR, p. ex. :
   `https://<tenant>.sharepoint.com/sites/<site>/Documents%20partag%C3%A9s`
   (ou le libellé exact de votre bibliothèque).
2. **Cibler par drive-id / URL de drive.** Si vous gérez plusieurs bibliothèques,
   visez chacune explicitement (le connecteur résout `(drive_id, drive_web_url)`),
   plutôt que de compter sur « la bibliothèque par défaut ».
3. **Renommer/aliaser** la bibliothèque en « Documents » (déconseillé en prod :
   impacte les utilisateurs), **ou** ouvrir une PR amont pour ajouter
   `"Documents partagés"` à `SHARED_DOCUMENTS_MAP` (FR).

> En clair : sur un tenant FR, **ne vous reposez pas sur la bibliothèque par
> défaut implicite** — **désignez explicitement le site et la bibliothèque**.

### 4.3 Fraîcheur (intervalle de synchronisation)

Réglages dans **Advanced Configuration** du connecteur (valeurs par défaut Onyx) :

| Réglage | Défaut | Rôle |
|---|---|---|
| **Refresh Frequency** | **30 minutes** | Fréquence de récupération des **nouveautés** depuis la source. |
| **Prune Frequency** | **30 jours** | Fréquence de **purge** des documents disparus de la source. |
| **Indexing Start Date** | début des données | Borne historique d'indexation. |

- Pour une **fraîcheur accrue** (dossiers très actifs), **réduire** la *Refresh
  Frequency*. Pour **alléger** la charge Graph/indexation, l'**augmenter**.
- ⚠ **FOSS** : un **retrait d'accès** côté SharePoint **n'est pas** propagé par la
  fraîcheur (pas de permission sync) ; le cloisonnement repose sur les Document
  Sets + la passerelle (cf. [`../RBAC.md`](../RBAC.md) §6). En **EE**, la sync des
  permissions se rafraîchit aussi périodiquement.

## 5. Mapping sécurité (parité avec un assistant d'entreprise)

| Propriété entreprise | Mécanisme onix |
|---|---|
| Authentification SSO | **OIDC Entra ID** (cf. [`../SECURITY.md`](../SECURITY.md) §6) |
| « Lecture seule » | l'agent ne fait que de la recherche ; aucune écriture SharePoint |
| Réponses sourcées | citations Onyx + prompt système strict |
| **RBAC par GROUPE** (FOSS) | **Document Sets + `access-gateway`** ([`../RBAC.md`](../RBAC.md) §4) |
| **RBAC par DOCUMENT** (strict) | **permission sync** (Mode B, **Cloud/EE**, certificat) |
| Un client à la fois | imposé par le prompt système de l'agent |

## 6. Sans Enterprise Edition (FOSS) — cloisonnement sûr

Sans permission sync, l'index est partagé. **Deux niveaux** de réponse :

1. **Cloisonnement par groupe (recommandé)** : SSO OIDC Entra + **un Document Set
   par périmètre homogène** + la passerelle **`access-gateway/`** qui route chaque
   utilisateur vers SES Document Sets (deny-by-default). Couvre le cas
   multi-commerciaux. Détails, code, tests : [`../RBAC.md`](../RBAC.md) et
   [`../../access-gateway/`](../../access-gateway/).
2. **Mesures complémentaires** selon l'exigence :
   - **Index à accès uniforme** : n'indexer que des périmètres que **tous** les
     utilisateurs concernés ont le droit de voir.
   - **Instance par périmètre** : déploiements onix séparés pour des populations
     aux droits **disjoints**.
   - **Passer en Cloud / EE** si le **RBAC fin par document** est exigé (parité
     totale, certificat + permission sync).

> Documentez le choix retenu : c'est la **réserve n°1** d'un audit de sécurité.

### 6.1 ACL par-document **auto-dérivée** de SharePoint (Microsoft Graph)

Le filtre par-document FOSS ([`../RBAC.md`](../RBAC.md) §4.3,
[`../../access-gateway/app/doc_acl.py`](../../access-gateway/app/doc_acl.py))
s'appuyait jusqu'ici sur un fichier ACL **maintenu à la main** (`doc_acl.json`).
La passerelle sait désormais **dériver cette ACL automatiquement** des
**permissions par item** réelles de SharePoint, via Microsoft Graph
([`../../access-gateway/app/graph_acl.py`](../../access-gateway/app/graph_acl.py)).
Cela **ferme la dernière réserve « RBAC par document = EE » autant que le FOSS le
permet** — mais **reste un filtre de SORTIE** (lire §6.2 ci-dessous : honnêteté).

#### Endpoint Graph + permission applicative

Pour chaque item, on lit ses permissions :
```http
GET https://graph.microsoft.com/v1.0/sites/{site-id}/drives/{drive-id}/items/{item-id}/permissions
Authorization: Bearer <app-token>
Accept: application/json
```
- **Permission APPLICATION (app-only) requise : `Sites.Read.All`** (ou
  **`Sites.Selected`** + octroi par site, cf. §3 Mode A — moindre privilège),
  avec **`Grant admin consent`**. `Files.Read.All` fonctionne aussi mais est plus
  large. **Pas besoin** des droits étendus du Mode B (permission sync EE).

> ℹ️ **Vérification des endpoints.** La confirmation via le **Microsoft Learn MCP**
> prévue par le périmètre **n'a pas pu être exécutée** (outil refusé par la
> politique de permissions de l'environnement — même situation que la recherche
> Context7 notée dans [`../DECISION_RBAC.md`](../DECISION_RBAC.md) §3). L'endpoint
> `…/items/{item-id}/permissions`, la ressource `permission` et l'`identitySet`
> `grantedToV2` (user/group/siteGroup) reposent donc sur l'API Graph **v1.0**
> stable et documentée. **Re-vérifier sur learn.microsoft.com** (« List
> permissions / driveItem ») avant un déploiement sensible.
- Jeton obtenu en **client credentials** (mêmes creds que la passerelle :
  `GATEWAY_GRAPH_TENANT_ID` / `_CLIENT_ID` / `_CLIENT_SECRET`). **Secrets en env
  uniquement** ; jamais journalisés.

#### Modèle de permission parsé (`grantedToV2`)

Pour chaque `permission` de l'item, on retient celles qui confèrent **au moins la
lecture** (`roles` ⊇ `read`/`write`/`owner`…) — directes **ou héritées**
(`inheritedFrom` présent : un héritage de lecture donne bien l'accès) — et on
agrège les identités :

| Champ Graph | → mappé vers | Sens |
|---|---|---|
| `grantedToV2.user.id` | `users` | objectId **utilisateur** Entra |
| `grantedToV2.group.id` | `groups` | objectId **groupe** Entra (de sécurité / M365) |
| `grantedToV2.siteGroup.id` | `groups` | id de **groupe SharePoint** (membres SP du site) |
| `grantedToIdentitiesV2[]` | idem | variante **liste** (partages multiples) |

Les liens **anonymes / organisation** (sans identité) sont **ignorés** (ils
n'identifient personne à recouper avec le `Principal` de l'appelant). Les
identités sont comparées **casse-insensible** (cohérent avec `StaticDocACL`).

> ⚠️ **`siteGroup` (groupe SharePoint) ≠ groupe Entra.** Si une ACL repose sur un
> *groupe SharePoint* (et non un groupe Entra), son `id` est un identifiant **SP**
> qui n'apparaît PAS dans les `group_ids` Entra du `Principal` (claims OIDC /
> `transitiveMemberOf`). Pour que le recoupement fonctionne, privilégiez des
> **permissions par groupe Entra** côté SharePoint, ou alimentez les `group_ids`
> en conséquence. Documenté comme limite ci-dessous.

#### Le maillon dur : mapping `doc_id ↔ item SharePoint`

Un `doc_id` Onyx doit être relié à `(site_id, drive_id, item_id)` pour qu'on
puisse lire ses permissions. **Onyx stocke l'URL source / l'id de drive-item dans
les MÉTADONNÉES** du document (connecteur SharePoint :
`backend/onyx/connectors/sharepoint/connector.py`). On **ne devine pas** ce lien :
on fournit un **mapping explicite** (JSON) :

```json
{
  "_version": 1,
  "<onyx_doc_id_1>": { "site_id": "<site-id>", "drive_id": "<drive-id>", "item_id": "<item-id>" },
  "<onyx_doc_id_2>": { "site_id": "<site-id>", "drive_id": "<drive-id>", "item_id": "<item-id>" }
}
```

**Comment l'obtenir** (selon votre accès à Onyx) :
1. **Métadonnées des documents Onyx.** L'API/admin Onyx expose, par document, le
   `document_id` et sa **source URL** (webUrl SharePoint). Exportez la liste, puis
   résolvez chaque webUrl en `(site_id, drive_id, item_id)` via Graph
   (`GET /sites/{hostname}:/sites/{path}`, puis `…/drives`, puis
   `…/drive/root:/{rel-path}`), ou directement
   `GET /shares/{shareIdOrEncodedUrl}/driveItem` qui renvoie `id` (item),
   `parentReference.driveId` et `parentReference.siteId`.
2. **Connecteur.** Le connecteur SharePoint d'Onyx manipule déjà
   `(drive_id, item_id)` lors de l'indexation : un export ad hoc depuis la base
   Onyx (table des documents + métadonnées de connecteur) fournit le mapping sans
   re-résolution.

> Le mapping est la **frontière de responsabilité honnête** : la qualité de l'ACL
> dérivée dépend de l'exactitude du lien `doc_id ↔ item`. Versionnez-le.

#### Deux modes d'usage

- **Matérialisé (recommandé pour démarrer).** Le CLI
  [`../../scripts/sync-doc-acl.py`](../../scripts/sync-doc-acl.py) lit le mapping +
  les creds Graph (env) et **écrit `doc_acl.json`** — donc le chemin
  `StaticDocACL` existant fonctionne **sans changement de code**, et le résultat
  est **auditable / diffable** :
  ```bash
  make sync-doc-acl            # chemins par défaut (voir variables MAPPING / OUT)
  # ou explicitement :
  GATEWAY_GRAPH_TENANT_ID=… GATEWAY_GRAPH_CLIENT_ID=… GATEWAY_GRAPH_CLIENT_SECRET=… \
    python scripts/sync-doc-acl.py \
      --mapping access-gateway/config/doc_acl_mapping.json \
      --out     access-gateway/config/doc_acl.json
  ```
  À planifier en **cron / CI** pour propager les changements d'accès (un retrait
  d'accès SharePoint disparaît du `doc_acl.json` au sync suivant). **Cadence**
  conseillée : alignée sur la *Refresh Frequency* du connecteur (§4.3, défaut 30
  min) ou plus lâche selon la sensibilité (la propagation reste **différée**,
  jamais instantanée — comme en EE, où la sync est aussi périodique).

- **En vif (ACL vivante en mémoire).** La passerelle peut tenir l'ACL Graph
  **en mémoire** et l'OR-merger avec le statique (`CompositeDocACL`), rafraîchie
  selon un **TTL**. Réglages (env, additifs ; défaut **désactivé**) :

  | Variable | Défaut | Effet |
  |---|---|---|
  | `GATEWAY_DOC_ACL_GRAPH_ENABLED` | `false` | Active la source d'ACL Graph (opt-in). |
  | `GATEWAY_DOC_ACL_MAPPING_PATH` | `config/doc_acl_mapping.json` | Mapping `doc_id → {site_id, drive_id, item_id}`. |
  | `GATEWAY_DOC_ACL_REFRESH_SECONDS` | `900` | TTL (s) avant re-synchronisation. `0` = figée après 1er build. |

  Requiert `GATEWAY_GRAPH_*` configuré. L'OR-merge garantit qu'un document
  autorisé par **l'une** des sources (statique **ou** Graph) reste visible.

#### 6.2 Honnêteté — ce que cette dérivation N'EST PAS

> **Toujours un filtre de SORTIE.** On synchronise **qui peut VOIR** un document
> (donc sa **citation**), pas **ce que le LLM a récupéré** à la génération. Onyx
> FOSS fait toujours raisonner le LLM sur tout le Document Set autorisé ; le LLM a
> donc pu lire un fichier non autorisé et en glisser un fragment dans le texte
> **sans citation traçable**. La dérivation Graph rend le filtre **automatique**
> (plus de JSON manuel) ; elle **ne change pas sa nature**. Le **« zéro-fuite » à
> la RECHERCHE** reste **Onyx EE/Cloud** (permission sync, certificat) ou des
> **instances séparées par tier d'accès**. Détail : [`../RBAC.md`](../RBAC.md)
> §4.4 et [`../DECISION_RBAC.md`](../DECISION_RBAC.md) §4.
>
> Limites secondaires : (a) un `siteGroup` SharePoint ne se recoupe pas avec les
> groupes **Entra** du `Principal` (préférez les ACL par groupe Entra) ; (b) la
> qualité dépend du **mapping `doc_id ↔ item`** ; (c) la propagation est
> **différée** (cadence du sync), pas instantanée.

## 7. Validation
- L'indexation se termine (Admin → Connectors → statut « succeeded »).
- Une question sur un document **connu** renvoie une **réponse sourcée** (citation).
- (FOSS + gateway) un commercial **d'une autre équipe** n'obtient **rien** sur un
  périmètre qui n'est pas le sien (cf. tests `access-gateway/tests`).
- (Mode B / EE) un utilisateur **sans accès** à un dossier n'obtient **rien**
  dessus, **par document**.
