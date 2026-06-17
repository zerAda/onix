# RBAC par utilisateur — ce qu'onix permet, honnêtement (FOSS vs Enterprise)

Le **contrôle d'accès par utilisateur** (chacun ne voit que SES documents) est la
plus grosse difficulté d'un assistant RAG d'entreprise sur SharePoint. Cette page
dit **précisément** ce qui est possible en édition gratuite (FOSS / Community
Edition d'Onyx), ce qui exige l'**Enterprise Edition (EE) / Cloud**, et la
**stratégie FOSS de cloisonnement par groupe** qu'onix met en œuvre pour couvrir
le cas réel multi-commerciaux — avec ses limites assumées.

> TL;DR
> - **Trimming par DOCUMENT À LA RECHERCHE** natif (l'index sait qui peut voir
>   chaque fichier, le LLM ne voit que les chunks autorisés) = **EE / Cloud
>   uniquement**. La doc Onyx est explicite :
>   *« Different access to documents is only available in the Enterprise Edition of Onyx »*
>   et *« Permission-syncing connectors are an Enterprise Edition feature »*.
> - **Cloisonnement par GROUPE / Document Set** = **faisable en FOSS** via SSO OIDC
>   Entra + Document Sets + la **passerelle `access-gateway/`** d'onix.
> - **Filtre par DOCUMENT À LA RÉPONSE** (retire les citations vers les fichiers
>   non autorisés individuellement ; refus substitué si zéro citation restante)
>   = **NOUVEAU en FOSS** via [`doc_acl.py`](../access-gateway/app/doc_acl.py)
>   (§4.3). Granularité par document **côté sortie** ; ne remplace pas la
>   permission sync EE (§4.4 — honnêteté).
>
> 📋 **Dossier de décision chiffré (EE/Cloud vs FOSS vs hybride)** : voir
> [`DECISION_RBAC.md`](DECISION_RBAC.md) — matrice sécurité/coût/effort/conformité/
> réversibilité, **prix datés**, **risque résiduel quantifié**, et **recommandation
> par scénario**. Ce dossier **cadre** l'astérisque RBAC (décision outillée).

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

### 4.3 Filtre ACL par DOCUMENT côté RÉPONSE (`access-gateway/app/doc_acl.py`)

**Nouveau dans FOSS** (workstream `feat/rbac-perdoc`). Le cloisonnement par
Document Set (§4.1/4.2) borne la **recherche** au périmètre. Restait un trou
**dans le rendu** : à l'intérieur d'un Document Set, Onyx pouvait renvoyer des
citations vers des fichiers auxquels l'utilisateur n'avait pas individuellement
accès. Le module [`doc_acl.py`](../access-gateway/app/doc_acl.py) ferme cette
fuite **côté sortie** :

```
Onyx /chat/send-message ─▶ access-gateway
                            1. (déjà) post-filtre garde-fous (couche 3, hors-LLM)
                            2. **NOUVEAU** : filter_citations(body, principal, acl)
                               • retire de top_documents / context_docs /
                                 final_context_docs / documents /
                                 source_documents / citations les entrées
                                 dont le doc_id N'est PAS autorisé pour l'appelant
                               • si TOUTES les citations sont retirées et que
                                 strip_uncited=true → SUBSTITUE la réponse par
                                 REFUSAL_NO_ACCESSIBLE_SOURCE (refus sourcé FR)
                               • journalise (HMAC-chain) chaque drop + un résumé
                          ─▶ utilisateur
```

**Mécanisme** :
- `StaticDocACL` charge un fichier JSON `config/doc_acl.json` de forme
  `{ "doc_id": { "groups": ["G1",…], "users": ["upn",…] } }`. Un override par
  utilisateur (UPN/oid) **gagne** sur l'appartenance de groupe.
- Politique par défaut **deny** (parité avec `GATEWAY_DENY_IF_NO_MATCH`) :
  un `doc_id` non listé est INVISIBLE. Configurable
  (`GATEWAY_DOC_ACL_DEFAULT_POLICY=allow` pour les POCs / corpus historique).
- `CompositeDocACL` permet d'OR-merger plusieurs sources (statique + cache
  Graph à venir).
- **Fail-OPEN sur erreur interne** (loader cassé p.ex.) → body inchangé, log
  `doc_acl_error` (concern disponibilité), versus **fail-CLOSED sur
  doc_id inconnu** quand `default_policy=deny` (concern autorisation).

**Configuration** (variables d'env) :

| Variable | Défaut | Effet |
|---|---|---|
| `GATEWAY_DOC_ACL_ENABLED` | `true` | Active le filtre. |
| `GATEWAY_DOC_ACL_PATH` | `config/doc_acl.json` | Chemin du JSON. |
| `GATEWAY_DOC_ACL_DEFAULT_POLICY` | `deny` | `deny` ou `allow`. |
| `GATEWAY_DOC_ACL_STRIP_UNCITED` | `true` | Substitution si zéro citation restante. |

Fichier d'exemple : [`../access-gateway/config/doc_acl.example.json`](../access-gateway/config/doc_acl.example.json)
+ commentaires sibling [`.json.md`](../access-gateway/config/doc_acl.example.json.md).

### 4.4 Honnêteté — ce que le filtre par-document N'EST PAS

> **Filtre de SORTIE, pas de récupération.** Onyx FOSS récupère et fait
> raisonner le LLM sur **tous les documents du Document Set autorisé** ;
> autrement dit, le LLM peut avoir lu et formulé sa réponse à partir d'un
> document qu'il NE faut PAS exposer à l'utilisateur. Le filtre ci-dessus
> retire alors **la citation visible** vers ce document, et — si toutes les
> citations sont retirées — substitue la réponse par un refus. **Mais** des
> fragments d'information ont pu transiter par le texte d'assistant pendant
> la génération, sans citation traçable. Aucun filtre côté réponse ne peut
> rattraper cela à 100 % sans contexte.
>
> **Pour atteindre un « zéro fuite » strict**, deux options seulement :
> 1. **Onyx EE / Cloud** (permission sync) — l'ACL est rejouée à la
>    RÉCUPÉRATION, le LLM ne voit jamais que les chunks autorisés.
> 2. **Instances Onyx séparées par tier d'accès** — chaque population
>    consomme son propre index, isolé physiquement.
>
> Le filtre `doc_acl.py` ferme **la fuite VISIBLE à l'utilisateur** (la grande
> majorité du risque opérationnel : un commercial qui voit citer le dossier
> d'un client d'une autre équipe dans son agent). Il **n'élimine pas** la
> fuite indirecte par le texte généré. Documenter cet écart au RGPD/audit si
> le seuil de risque l'exige.

## 5. Où en est la parité — et où l'EE reste requis

| Besoin | FOSS + onix (`access-gateway`) | EE / Cloud (permission sync) |
|---|---|---|
| Savoir qui interroge (SSO) | ✅ OIDC Entra | ✅ OIDC/SAML |
| Cloisonner **par équipe / périmètre** | ✅ groupe Entra → Document Set | ✅ (et plus fin) |
| Empêcher un commercial de voir le périmètre d'un autre | ✅ (deny-by-default, non-élargissement) | ✅ |
| Trimming **par document à la RECHERCHE** (le LLM ne voit que les chunks autorisés) | ❌ **non** (filtrage uniquement à la sortie, cf. §4.4) | ✅ **oui** (ACL par document rejouée) |
| Filtre **par document côté RÉPONSE** (retire les citations vers les docs non autorisés, refus substitué si plus aucune citation) | ✅ **NOUVEAU** (`doc_acl.py`, §4.3) | ✅ (intégré) |
| ACL **suivant automatiquement** SharePoint (un retrait d'accès se propage) | ⚠️ **manuel** (mettre à jour `doc_acl.json` / re-cloisonner) | ✅ **auto** (sync périodique) |
| Effort d'admin | Moyen (Document Sets + mapping groupes + ACL par-document) | Faible (sync) mais **licence EE** |

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
6. **Fail-closed.** Si la passerelle ne peut **pas** établir les groupes de
   l'appelant (identité illisible → 401 ; overage **et** repli Graph indisponible/
   en erreur → **502**), elle **refuse** ; jamais de passage sans périmètre résolu.
   Un utilisateur sans groupe mappé → **403** (`GATEWAY_DENY_IF_NO_MATCH=true`).
7. **Journal des décisions d'accès (haché).** Chaque allow/deny est journalisé
   (JSON, logger `onix.gateway.audit`) avec une **identité pseudonymisée**
   (HMAC-SHA256, sel `GATEWAY_AUDIT_SALT`) — **jamais** l'UPN/oid en clair, **jamais**
   le message. Appui RGPD (journal d'accès) / assurance. Cf.
   [`../access-gateway/app/audit.py`](../access-gateway/app/audit.py).

### Modèle de menace (synthèse — mis à jour : durcissement)
| Menace | Atténuation (FOSS + gateway) | Preuve |
|---|---|---|
| Un commercial lit le portefeuille d'un autre | Filtre Document Set forcé + deny-by-default | `test_two_commercials_are_isolated` |
| Client tente d'élargir son périmètre (payload trafiqué) | Intersection des `document_set`, `search_doc_ids` neutralisé | `test_user_cannot_widen_scope`, `test_cannot_escape_via_search_doc_ids` |
| Multi-groupes : accès à un set non mappé | Union **bornée aux sets autorisés** uniquement | `test_multi_group_user_gets_union_only`, `test_multi_group_user_cannot_reach_unmapped_set` |
| Utilisateur **sans groupe** / claim groupe absent | **Deny** (403) — fail-closed | `test_user_with_empty_groups_is_denied`, `test_user_without_groups_claim_at_all_is_denied` |
| **Groupes irrésolvables** (overage + Graph indispo/erreur) | **Deny dur (502)** — jamais de passage « ouvert » (fail-closed) | `test_overage_without_graph_fails_closed_502`, `test_overage_with_graph_error_fails_closed` |
| Contournement via l'UI Onyx directe | UI/API Onyx **internes** ; seul `access-gateway` est exposé (cf. déploiement) | `DECISION_RBAC.md` §6 / `access-gateway/README.md` |
| Overage de groupes OIDC (liste tronquée) | Repli Graph `transitiveMemberOf` (mode `auto`) | `test_auto_falls_back_to_graph_on_overage` |
| Traçabilité d'un accès (audit/RGPD) | **Journal des décisions** allow/deny, **identité hachée** (HMAC) | `test_decision_record_never_leaks_plaintext_identity` |
| Citation rendue vers un document non-autorisé pour l'appelant (même Document Set) | **Couvert côté sortie** : `filter_citations` retire la citation, refus substitué si zéro citation restante | `test_rbac_isolation_two_users_same_body_different_filtered`, `test_strip_uncited_substitutes_safe_refusal` |
| Le LLM **a vu** le contenu d'un document non-autorisé pendant la génération (fuite indirecte par le texte) | **Non couvert** côté FOSS : nécessite Onyx EE (permission sync) OU instances séparées par tier d'accès (§4.4) | — |
| Deux droits différents sur un même document **à la RECHERCHE** (zéro-leak strict) | **Non couvert** → EE requis (assumé, §5 ; quantifié `DECISION_RBAC.md` §4) | — |

## 7. Décision à acter avec le client
- **Cas multi-commerciaux / multi-équipes, accès homogène par périmètre** →
  **FOSS + `access-gateway`** suffit (cette page + le composant).
- **Cloisonnement légal strict par document, droits hétérogènes intra-équipe, ou
  propagation automatique des ACL SharePoint** → **Enterprise Edition** (permission
  sync, certificat). Voir [`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md) §6.

👉 **Pour trancher : [`DECISION_RBAC.md`](DECISION_RBAC.md)** — dossier de décision
**chiffré et daté** (EE/Cloud vs FOSS vs hybride), risque résiduel **quantifié**,
recommandation **par scénario**, et durcissement de la passerelle.

Voir aussi : [`PARITE_ENTREPRISE.md`](PARITE_ENTREPRISE.md) (matrice globale) et
[`../access-gateway/`](../access-gateway/) (code, tests, Dockerfile, mapping).
