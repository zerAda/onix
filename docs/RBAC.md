# RBAC par utilisateur — ce qu'onix permet, honnêtement (FOSS vs Enterprise)

Le **contrôle d'accès par utilisateur** (chacun ne voit que SES documents) est la
plus grosse difficulté d'un assistant RAG d'entreprise sur SharePoint. Cette page
dit **précisément** ce qui est possible en édition gratuite (FOSS / Community
Edition d'Onyx), ce qui exige l'**Enterprise Edition (EE) / Cloud**, et la
**stratégie FOSS de cloisonnement par groupe** qu'onix met en œuvre pour couvrir
le cas réel multi-commerciaux — avec ses limites assumées.

> TL;DR
> - **Trimming par DOCUMENT** natif (l'index sait qui peut voir chaque fichier) =
>   **EE / Cloud uniquement**. La doc Onyx est explicite :
>   *« Different access to documents is only available in the Enterprise Edition of Onyx »*
>   et *« Permission-syncing connectors are an Enterprise Edition feature »*.
> - **Cloisonnement par GROUPE / Document Set** = **faisable en FOSS** via SSO OIDC
>   Entra + Document Sets + la **passerelle `access-gateway/`** d'onix. Granularité
>   au **groupe d'accès / Document Set**, **pas par document**.

---

## 1. Le « trou » FOSS : l'index est partagé

En FOSS, Onyx **n'applique pas** de liste de contrôle d'accès (ACL) par document à
la recherche. Concrètement :

- Tout ce qui est **indexé** est, par défaut, **interrogeable par tout utilisateur
  authentifié** de l'instance.
- Le connecteur SharePoint indexe les fichiers **avec l'identité applicative**
  (client secret ou certificat) — il « voit » donc potentiellement large ; sans
  *permission sync*, **aucune** ACL par document n'est rejouée à la requête.
- Conséquence directe : **ne jamais indexer, sur une instance FOSS partagée, des
  dossiers dont l'accès diffère entre utilisateurs**, sauf à mettre en place le
  cloisonnement décrit au §4.

C'est une **propriété d'architecture d'Onyx FOSS**, pas un bug d'onix. Le moteur
de trimming par document (rejouer les ACL SharePoint) vit dans le code EE.

## 2. L'option officielle : permission sync (EE / Cloud)

Le trimming par document « comme en entreprise » repose sur la **synchronisation
des permissions** (« Auto Sync Permissions ») du connecteur :

| Élément | Détail (source : docs.onyx.app) |
|---|---|
| Disponibilité | **Enterprise Edition** (et Onyx Cloud). *« Permission-syncing connectors are an Enterprise Edition feature »*. |
| Connecteurs concernés | Confluence, Jira, Google Drive, Gmail, Slack, Salesforce, GitHub, **SharePoint**. |
| Pré-requis SharePoint | **Authentification par certificat** (le client secret ne suffit pas pour la sync). |
| Mécanisme | Onyx rapatrie les ACL de la source (qui ↔ groupe ↔ document), les attache aux chunks, et **filtre au moment de la requête** selon l'utilisateur. |
| Permissions Graph | Plus larges : lecture d'annuaire et d'appartenance (`GroupMember.Read.All`, `Directory.Read.All`, `Group.Read.All`, `Member.Read.Hidden`…). |

C'est la **seule** voie pour un trimming **strict par document** « natif Onyx ».
Si le client l'exige contractuellement (ex. cloisonnement légal par dossier au
sein d'une même équipe), il faut **EE / Cloud**. Inutile de prétendre l'inverse.

## 3. Brique commune indispensable : l'identité (SSO OIDC Entra)

Quelle que soit l'édition, onix doit **savoir qui interroge**. En FOSS comme en EE,
Onyx supporte le **SSO OIDC / SAML** (cf. [`SECURITY.md`](SECURITY.md) §6) :

- `AUTH_TYPE=oidc`, `OPENID_CONFIG_URL` du tenant Entra, `OIDC_PKCE_ENABLED=true`,
  et `VALID_EMAIL_DOMAINS=votre-domaine.com`.
- L'**appartenance aux groupes** Entra peut être obtenue de **deux** façons :
  1. **Claim `groups`** dans le jeton OIDC. C'est un **claim optionnel** (à activer
     dans l'app registration : *Token configuration → groups claim* / manifeste
     `groupMembershipClaims`). Il **n'apparaît pas** dans le document
     `.well-known/openid-configuration` (ce n'est pas un scope standard).
     ⚠ **Overage** : si l'utilisateur dépasse la limite de taille du jeton
     (**≈ 200 groupes** pour un JWT), Entra **n'inclut pas** la liste mais un claim
     d'overage (`hasgroups` / `_claim_names`) imposant un **repli sur Microsoft
     Graph**. Bonne pratique : limiter le claim aux **« Groups assigned to the
     application »** pour éviter l'overage.
  2. **Microsoft Graph** `transitiveMemberOf` (app-only) — voir §4.

## 4. La stratégie FOSS d'onix : cloisonnement par groupe → Document Set

onix **ne se résigne pas** au « tout le monde voit tout » en FOSS. Il réintroduit
un cloisonnement **par groupe d'accès**, suffisant pour le **cas réel
multi-commerciaux** (équipe Nord / équipe Sud / direction transverse), via deux
mécanismes combinés :

### 4.1 Document Sets Onyx = périmètres
On crée **un Document Set par périmètre homogène** (cf.
[`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md)) : `clients-nord`,
`clients-sud`, … Chaque Document Set regroupe le(s) site(s)/drive(s) SharePoint de
ce périmètre. La recherche Onyx sait **filtrer par Document Set**
(`retrieval_options.filters.document_set` dans `/chat/send-message`).

### 4.2 La passerelle `access-gateway/` = aiguillage par identité
Le **trou** restant : en FOSS, **Onyx ne choisit pas tout seul** le Document Set
selon l'utilisateur (ce serait du trimming natif = EE). onix comble ce trou avec
un **proxy identity-aware** ([`../access-gateway/`](../access-gateway/)) qui
s'intercale **devant** la recherche :

```
Utilisateur (SSO OIDC) ─▶ reverse-proxy (valide le jeton, injecte X-OIDC-Claims)
                          │
                          ▼
                  access-gateway (FastAPI)
                   1. lit l'identité + groupes Entra
                      • claim `groups` (OIDC), OU
                      • Graph transitiveMemberOf (app-only) si absent/overage
                   2. mappe groupe ─▶ Document Set(s) autorisés (deny-by-default)
                   3. FORCE retrieval_options.filters.document_set = périmètre
                          │
                          ▼
                     Onyx /chat/send-message  (ne cherche QUE dans le périmètre)
```

Propriétés :
- **Deny-by-default** : un utilisateur sans groupe mappé est **refusé** (403).
- **Non-élargissement** : un client ne peut PAS s'octroyer un Document Set hors de
  son périmètre (le filtre demandé est **intersecté** avec l'autorisé ; un accès
  direct par `search_doc_ids` est **neutralisé**).
- **Repli overage géré** : si le claim `groups` est tronqué, la passerelle bascule
  sur Graph `transitiveMemberOf` automatiquement (mode `auto`).

#### Appel Graph (moindre privilège)
```http
GET https://graph.microsoft.com/v1.0/users/{oid}/transitiveMemberOf/microsoft.graph.group
    ?$select=id,displayName&$top=999
Authorization: Bearer <app-token>
ConsistencyLevel: eventual
```
- **Permission applicative minimale : `GroupMember.Read.All`** (suffit pour lister
  l'appartenance transitive d'un autre utilisateur ; `User.Read.All` marche aussi
  mais est plus large). **Pas besoin de `Directory.Read.All`** ici.
- L'**OData cast** `/microsoft.graph.group` + `ConsistencyLevel: eventual` sont
  requis (advanced query). Pagination via `@odata.nextLink`.

#### Mapping groupe → Document Set
Fichier JSON monté en lecture seule (exemple :
[`../access-gateway/config/group_map.example.json`](../access-gateway/config/group_map.example.json)) :
```json
{
  "version": 1,
  "default_document_sets": [],
  "groups": {
    "11111111-…": {"label": "Commerciaux Nord",   "document_sets": ["clients-nord"]},
    "22222222-…": {"label": "Commerciaux Sud",    "document_sets": ["clients-sud"]},
    "33333333-…": {"label": "Direction (transverse)", "document_sets": ["clients-nord","clients-sud"]}
  }
}
```
La clé est l'**objectId (GUID)** du groupe de sécurité Entra (stable ; recommandé)
ou son `displayName`. La valeur est le **nom exact** des Document Sets Onyx.

## 5. Où en est la parité — et où l'EE reste requis

| Besoin | FOSS + onix (`access-gateway`) | EE / Cloud (permission sync) |
|---|---|---|
| Savoir qui interroge (SSO) | ✅ OIDC Entra | ✅ OIDC/SAML |
| Cloisonner **par équipe / périmètre** | ✅ groupe Entra → Document Set | ✅ (et plus fin) |
| Empêcher un commercial de voir le périmètre d'un autre | ✅ (deny-by-default, non-élargissement) | ✅ |
| Trimming **strict par document** (deux personnes d'une **même** équipe avec des droits **différents sur un même dossier**) | ❌ **non** (granularité Document Set) | ✅ **oui** (ACL par document rejouée) |
| ACL **suivant automatiquement** SharePoint (un retrait d'accès se propage) | ⚠️ **manuel** (re-mapper / re-cloisonner) | ✅ **auto** (sync périodique) |
| Effort d'admin | Moyen (créer Document Sets + mapping) | Faible (sync) mais **licence EE** |

**Verdict honnête.** En FOSS, onix atteint la parité **au niveau groupe d'accès /
Document Set** — ce qui **couvre le cas multi-commerciaux** (chaque équipe son
portefeuille). Il **n'atteint pas** la parité du **trimming par document** : si la
règle de sécurité distingue **deux utilisateurs d'un même périmètre** sur un
**même document**, ou exige que tout **retrait d'accès SharePoint se propage
automatiquement** à l'index, alors **l'Enterprise Edition (permission sync,
certificat) est requise**. Ce n'est pas l'OBO par-document d'un AC360/Copilot.

## 6. Limites & garde-fous de la voie FOSS (à documenter pour l'audit)

1. **Granularité = Document Set**, pas document. Concevez les périmètres en
   conséquence (un Document Set = un ensemble à accès **homogène**).
2. **Cohérence d'indexation.** Un Document Set ne doit agréger que des sources que
   **tous** les membres du/des groupe(s) mappé(s) ont le droit de voir.
3. **La passerelle est le point de contrôle.** L'UI Onyx native (port direct) doit
   **rester interne** ; les utilisateurs passent par `access-gateway`. Sinon, le
   filtre est contournable (cf. modèle de menace ci-dessous).
4. **Confiance dans `X-OIDC-Claims`.** La passerelle fait confiance aux claims
   **déjà vérifiés** par le reverse-proxy/IdP en amont. Ne **jamais** exposer la
   passerelle sans cette couche d'authentification devant elle.
5. **Révocation.** Le retrait d'un utilisateur d'un groupe Entra se reflète au
   prochain rafraîchissement (claim au ré-login, ou TTL du cache Graph côté
   passerelle, `GATEWAY_GROUP_CACHE_TTL`). Ce n'est pas instantané.

### Modèle de menace (synthèse)
| Menace | Atténuation (FOSS + gateway) |
|---|---|
| Un commercial lit le portefeuille d'un autre | Filtre Document Set forcé + deny-by-default |
| Client tente d'élargir son périmètre (payload trafiqué) | Intersection des `document_set`, `search_doc_ids` neutralisé |
| Contournement via l'UI Onyx directe | UI/API Onyx **internes** ; seul `access-gateway` est exposé |
| Overage de groupes OIDC (liste tronquée) | Repli Graph `transitiveMemberOf` (mode `auto`) |
| Deux droits différents sur un même document | **Non couvert** → EE requis (assumé, §5) |

## 7. Décision à acter avec le client
- **Cas multi-commerciaux / multi-équipes, accès homogène par périmètre** →
  **FOSS + `access-gateway`** suffit (cette page + le composant).
- **Cloisonnement légal strict par document, droits hétérogènes intra-équipe, ou
  propagation automatique des ACL SharePoint** → **Enterprise Edition** (permission
  sync, certificat). Voir [`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md) §6.

Voir aussi : [`PARITE_ENTREPRISE.md`](PARITE_ENTREPRISE.md) (matrice globale) et
[`../access-gateway/`](../access-gateway/) (code, tests, Dockerfile, mapping).
