# Connecteur SharePoint — RAG sur vos documents clients

onix se connecte à **SharePoint Online** via le **connecteur SharePoint natif d'Onyx**
(API Microsoft Graph). Les documents sont indexés (vecteur + lexical) puis
interrogeables par l'agent commercial, **réponses sourcées** à l'appui.

> Le connecteur indexe les **fichiers attachés aux sites** (PDF, Office, etc.).
> Il ne parse pas (encore) le contenu des *pages* SharePoint. La sélection par site
> n'est supportée que pour les tenants en **anglais, espagnol ou allemand**
> (sinon, laisser vide = tout le tenant, ou filtrer autrement).

## 1. Deux modes — choisir selon le besoin de sécurité

| Mode | Auth | RBAC par utilisateur (permission sync) | Édition Onyx | Privilèges Graph |
|---|---|---|---|---|
| **A. Lecture seule simple** | Client secret | ❌ index **partagé** (tous les users onix voient tout l'indexé) | **FOSS** (gratuit) | minimaux |
| **B. Parité entreprise (RBAC)** | **Certificat** | ✅ chaque user ne voit que ses documents | **Cloud / Enterprise Edition** | élevés |

> ⚠️ **Point de fond (à cadrer avec le client).** Le *trimming par utilisateur* —
> cœur de la sécurité d'un assistant commercial d'entreprise — repose sur la
> **synchronisation des permissions**, **disponible uniquement en Onyx Cloud / EE**.
> En **FOSS**, on n'a pas ce trimming : voir §6 pour les stratégies sûres.

## 2. App registration Microsoft Entra ID

1. Portail Azure → **App registrations** → **New registration**
   (ex. « onix SharePoint Connector »). Type mono-tenant.
2. Noter **Application (client) ID** et **Directory (tenant) ID**.
3. Selon le mode :
   - **A. Client secret** : *Certificates & secrets* → **New client secret** → noter la valeur.
   - **B. Certificat** : générer un certificat (auto-signé OK) au format **PFX** et
     l'**uploader** dans *Certificates & secrets → Certificates*. Exemple OpenSSL :
     ```bash
     openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 730 -nodes \
       -subj "/CN=onix-sharepoint"
     openssl pkcs12 -export -out onix-sp.pfx -inkey key.pem -in cert.pem   # -> upload Azure
     ```

## 3. Permissions Microsoft Graph (API permissions)

**Mode A — lecture seule (minimal)** — *Application permissions* :
- `Sites.Read.All` (ou `Sites.Selected` pour cibler des sites précis)
- `Files.Read.All`
- *Delegated* : `User.Read`

**Mode B — certificat + permission sync (parité RBAC)** — *Application permissions* :
- `Sites.Read.All` (ou `Sites.Selected`)
- `Directory.Read.All`
- `Group.Read.All`
- `GroupMember.Read.All`
- `Member.Read.Hidden`
- `User.Read.All`
- *Delegated* : `User.Read`
- **SharePoint** (API « Office 365 SharePoint Online ») : `Sites.FullControl.All`, `User.Read.All`

> `Group.Read.All` + `GroupMember.Read.All` + `Directory.Read.All` servent à
> reconstituer **qui a accès à quoi** (appartenance aux groupes) pour le trimming.
> **Principe de moindre privilège** : si vous n'avez pas besoin du RBAC par
> utilisateur, restez en **Mode A** (bien moins de droits ; pas de `Sites.FullControl.All`).

4. **Grant admin consent** (obligatoire) sur toutes les permissions ajoutées.

## 4. Configurer le connecteur dans Onyx

**Admin → Connectors → SharePoint → Set up** :
- **Connector Name** : ex. `clients-sharepoint`.
- **Sites** : liste des sites à indexer (URL), **ou vide** = tout le tenant.
- **Auth** : *Client Secret* (App ID, Directory ID, Secret) **ou** *Certificate*
  (App ID, Directory ID, upload du PFX).
- **Permission Sync** : disponible **uniquement** en mode certificat (et édition Cloud/EE).
- Lancer l'indexation, puis **regrouper le connecteur dans un Document Set**
  (`Admin → Document Sets`) que l'agent commercial utilisera (cf. `../AGENT_COMMERCIAL.md`).

## 5. Mapping sécurité (parité avec un assistant d'entreprise)

| Propriété entreprise | Mécanisme onix |
|---|---|
| Authentification SSO | **OIDC Entra ID** (cf. [`../SECURITY.md`](../SECURITY.md) §6) |
| « Lecture seule » | l'agent ne fait que de la recherche ; aucune écriture SharePoint |
| Réponses sourcées | citations Onyx + prompt système strict |
| **RBAC par utilisateur** | **permission sync** (Mode B, **Cloud/EE**) |
| Un client à la fois | imposé par le prompt système de l'agent |

## 6. Sans Enterprise Edition (FOSS) — stratégies sûres pour le RBAC

Tant que la permission sync n'est pas dispo (FOSS), **ne pas indexer des dossiers
dont l'accès diffère entre utilisateurs** sur une instance partagée. Options :

1. **Index à accès uniforme** : n'indexer que des sites/dossiers que **tous** les
   utilisateurs onix ont le droit de voir (ex. une équipe avec accès identique).
2. **Connecteurs par groupe** : un connecteur + Document Set + agent **par groupe
   d'accès homogène** (cloisonnement par périmètre).
3. **Instance par périmètre** : déploiements onix séparés pour des populations
   aux droits disjoints.
4. **Passer en Onyx Cloud / EE** si le RBAC fin par document est exigé (parité totale).

> Documentez le choix retenu : c'est la **réserve** n°1 d'un audit de sécurité.

## 7. Validation
- L'indexation se termine (Admin → Connectors → statut « succeeded »).
- Une question sur un document connu renvoie une **réponse sourcée** (citation du fichier).
- (Mode B/EE) un utilisateur **sans accès** à un dossier n'obtient **rien** dessus.
