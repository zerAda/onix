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

## 7. Validation
- L'indexation se termine (Admin → Connectors → statut « succeeded »).
- Une question sur un document **connu** renvoie une **réponse sourcée** (citation).
- (FOSS + gateway) un commercial **d'une autre équipe** n'obtient **rien** sur un
  périmètre qui n'est pas le sien (cf. tests `access-gateway/tests`).
- (Mode B / EE) un utilisateur **sans accès** à un dossier n'obtient **rien**
  dessus, **par document**.
