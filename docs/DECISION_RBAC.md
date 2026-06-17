# Dossier de décision — RBAC par-document : EE/Cloud vs cloisonnement par groupe (FOSS)

> **Objet.** Ce document **cadre l'astérisque RBAC** d'onix (cf.
> [`PARITE_ENTREPRISE.md`](PARITE_ENTREPRISE.md), réserve ＊＊) : il fournit une
> **matrice de décision chiffrée** entre trois options — **Onyx EE/Cloud**
> (trimming **par document** natif), **cloisonnement par groupe FOSS** via la
> passerelle [`../access-gateway/`](../access-gateway/) (granularité **Document
> Set**), et un mode **hybride** — sur les axes **sécurité, coût, effort,
> conformité, réversibilité**, avec une **recommandation par scénario**.
>
> **Honnêteté.** Le trimming **strict par document** (deux utilisateurs d'un même
> périmètre, droits différents sur un même fichier ; propagation automatique des
> révocations SharePoint) **reste une fonction Enterprise Edition / Cloud**. La
> voie FOSS **ne la reproduit pas** ; elle couvre le cas **multi-commerciaux par
> périmètre homogène**. Ce dossier quantifie le **risque résiduel** précis.
>
> Lecture liée : [`RBAC.md`](RBAC.md) (stratégie + modèle de menace),
> [`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md) (modes A/B).

---

## 0. Synthèse exécutive (TL;DR)

| Question | Réponse |
|---|---|
| Le RBAC **par-document** est-il faisable en FOSS ? | **Non.** C'est une fonction **Onyx EE / Cloud** (permission sync + certificat). Confirmé par les docs Onyx (§5, sources datées). |
| Que couvre la voie FOSS (`access-gateway`) ? | Un cloisonnement **par groupe d'accès → Document Set**, deny-by-default, non-élargissable, **+ un filtre par document côté RÉPONSE** (`doc_acl.py`) qui retire les citations vers les fichiers non autorisés individuellement (refus substitué si zéro citation restante). Suffisant pour **équipes/périmètres homogènes** (multi-commerciaux) **avec un cloisonnement supplémentaire visible par fichier**. |
| Recommandation **équipe homogène par périmètre** | **FOSS + `access-gateway`.** Coût licence = **0 €**. |
| Recommandation **droits hétérogènes fins intra-équipe** OU **révocation auto exigée** | **EE / Cloud.** Coût licence ≠ 0 (cf. §3, daté **2026-06-16**). |
| L'astérisque est-il « cadré » ? | **Oui** : la limite est documentée, le risque résiduel quantifié (§4), la passerelle durcie/testée (§6), la décision outillée (cette matrice). Il **n'est pas supprimé** : le par-document strict reste EE. |

---

## 1. Les trois options

- **Option A — Cloisonnement par groupe + filtre par-document côté RÉPONSE
  (FOSS).** SSO OIDC Entra + **Document Sets** Onyx (un par périmètre
  homogène) + **passerelle `access-gateway`** qui :
  1. mappe *groupe Entra → Document Set(s) autorisés* et **force** le filtre
     à la requête (deny-by-default, non-élargissable) — granularité **Document
     Set ≈ groupe d'accès**, sur le chemin **RECHERCHE** ;
  2. depuis `feat/rbac-perdoc`, applique [`doc_acl.py`](../access-gateway/app/doc_acl.py)
     **sur la RÉPONSE** : retire les citations vers les documents non
     autorisés individuellement (granularité **par document**, côté sortie ;
     refus substitué si zéro citation restante).
  Édition Onyx = **Community (MIT, gratuite)**.

- **Option B — EE / Cloud (permission sync).** Le connecteur SharePoint
  **rapatrie les ACL** de la source (certificat obligatoire) et **filtre par
  document à la requête** selon l'utilisateur. Granularité = **document**.
  Révocation **synchronisée automatiquement** (intervalle). Édition = **Enterprise
  Edition (auto-hébergée) ou Onyx Cloud (SaaS)**.

- **Option C — Hybride.** FOSS + `access-gateway` **maintenant** (gratuit,
  souverain, couvre 80 % du besoin) ; **EE/Cloud activable** plus tard **sur les
  seuls périmètres** exigeant le par-document (ex. un Document Set « juridique »
  passe en permission sync), le reste restant FOSS. Coexistence par instance ou par
  connecteur.

---

## 2. Matrice de décision (axes)

Légende : ✅ couvert · ⚠️ partiel/contrainte · ❌ non couvert.

### 2.1 Sécurité

| Critère | A — FOSS (groupe) | B — EE/Cloud (document) | C — Hybride |
|---|---|---|---|
| **Granularité (RECHERCHE)** | Document Set (groupe d'accès) — le LLM voit tout le set | **Document** (ACL source rejouée — zéro-leak) | Document **sur le périmètre EE**, groupe ailleurs |
| **Granularité (RÉPONSE)** | ✅ **Document** (filtre `doc_acl.py` retire les citations non-autorisées + refus si zéro citation) | ✅ Document (intégré) | ✅ Document partout |
| Isolation inter-équipes (commercial A ↮ B) | ✅ filtre forcé + deny-by-default | ✅ | ✅ |
| Droits **hétérogènes** intra-équipe (même dossier, accès ≠) — **rendu visible** | ✅ **NOUVEAU** : la citation est retirée pour celui qui n'a pas accès au fichier | ✅ | ✅ |
| Droits **hétérogènes** intra-équipe — **fuite indirecte par le texte généré** | ❌ (le LLM a pu lire le contenu pendant la génération, cf. §4) | ✅ (le LLM ne voit jamais les chunks non autorisés) | ✅ **sur périmètre EE** |
| **Propagation de révocation** | ⚠️ **manuelle/différée** : retrait du groupe Entra → effet au ré-login ou expiration du cache (`GATEWAY_GROUP_CACHE_TTL`, défaut 300 s) ; retrait d'accès **fichier** SharePoint **non** propagé | ✅ **auto** : la sync ACL reflète les changements (ordre de grandeur **minutes**, selon l'intervalle) | ✅ auto sur périmètre EE ; différé ailleurs |
| Anti-contournement requête | ✅ intersection `document_set`, `search_doc_ids` neutralisé | ✅ (filtre moteur natif) | ✅ |
| Surface de confiance | en-tête `X-OIDC-Claims` (vérifié en amont) + passerelle = **seul point d'entrée** | moteur Onyx + sync ACL (composant EE) | les deux |
| **Fail-closed** | ✅ identité illisible→401, groupes irrésolvables→502, sans périmètre→403 | ✅ (natif) | ✅ |

### 2.2 Coût (chiffré et **daté 2026-06-16** — voir §3 pour sources)

| Poste | A — FOSS | B — EE *(auto-hébergée)* | B — Cloud *(SaaS)* | C — Hybride |
|---|---|---|---|---|
| **Licence logicielle** | **0 €** (MIT) | **Sur devis** (`founders@onyx.app` ; pas de tarif public ; min. annoncés par des tiers **non confirmés** — cf. §3.4) | **Business 20 $/utilisateur/mois** (annuel) ; **Enterprise** *(SSO OIDC/SAML, on-prem)* = **sur devis** | FOSS (0 €) + EE/Cloud **sur le périmètre concerné** |
| **Infra** | la même (Onyx + Ollama + OpenSearch…) + 1 conteneur passerelle (négligeable) | idem FOSS (auto-hébergé) ; + certificat | **mutualisée chez l'éditeur** (transfert hors site — cf. souveraineté §2.4) | FOSS + surcoût marginal du périmètre EE |
| **Exploitation (humain)** | créer/maintenir Document Sets + mapping `group_map.json` (effort **moyen**, ponctuel) | sync **automatique** (effort **faible**) mais gestion certificat + contrat | **faible** (géré) | moyen (FOSS) + faible (EE) |
| **Coût souveraineté** | **nul** (tout sur site) | **nul** (sur site) | **transfert de données vers l'éditeur** (à arbitrer RGPD/assurance) | partiel selon périmètre Cloud |

> **Repère de modèle.** Le SSO **OIDC/SAML** — brique d'identité **commune aux deux
> voies** — est, sur la grille publique Onyx, une fonction du palier **Enterprise**
> (« Contact us »), tandis que **RBAC + Permission Inheritance** figurent dès le
> palier **Business (20 $/u/mois)**. La permission-sync **par-document** des
> connecteurs (dont SharePoint) est, elle, **EE/Cloud** (§5). Pour la voie FOSS,
> onix obtient l'identité via le **claim `groups` OIDC** ou **Microsoft Graph**
> (app-only), **sans dépendre du SSO payant d'Onyx** — c'est l'astuce du
> cloisonnement gratuit.

### 2.3 Effort de mise en œuvre

| | A — FOSS | B — EE/Cloud | C — Hybride |
|---|---|---|---|
| Identité (SSO/claims/Graph) | OIDC Entra + claim `groups` **ou** Graph `transitiveMemberOf` (`GroupMember.Read.All`) | OIDC/SAML + permissions Graph **plus larges** (annuaire/appartenance) + **certificat** | les deux |
| Modélisation accès | concevoir des **périmètres homogènes** (1 Document Set = 1 accès homogène) | s'appuie sur les **ACL existantes** de SharePoint | mixte |
| Déploiement | +1 conteneur (`access-gateway`), reverse-proxy, mapping JSON | activer permission sync ; négocier le contrat EE | les deux |
| Effort global | **moyen** (one-shot) | **faible** côté run, **mais** dépendance contractuelle + certificat | **moyen** |

### 2.4 Conformité (RGPD / assurance)

| Critère | A — FOSS | B — EE | B — Cloud | C — Hybride |
|---|---|---|---|---|
| **Localisation des données** | 100 % sur site (souverain) | 100 % sur site | **chez l'éditeur** (sous-traitant art. 28 ; clauses + localisation à valider) | sur site + périmètre Cloud |
| **Journal d'accès** (art. 30 / preuve assurance) | ✅ **décisions d'accès journalisées, identité hachée** (`access-gateway/app/audit.py`) | ✅ (logs Onyx EE) | ✅ (géré) | ✅ |
| **Minimisation/pseudonymisation** | ✅ HMAC-SHA256 de l'identité dans les logs (`GATEWAY_AUDIT_SALT`) | dépend de la conf | dépend du contrat | ✅ |
| Adéquation au **strict besoin d'en connaître** | ⚠️ au **niveau périmètre** (pas fichier) | ✅ au **niveau fichier** | ✅ sur périmètre EE | mixte |
| DPIA / registre | cf. [`DPIA_TEMPLATE.md`](DPIA_TEMPLATE.md), [`REGISTRE_TRAITEMENTS.md`](REGISTRE_TRAITEMENTS.md) | idem | **+ sous-traitant à inscrire** | idem + sous-traitant partiel |

### 2.5 Réversibilité (lock-in)

| | A — FOSS | B — EE | B — Cloud | C — Hybride |
|---|---|---|---|---|
| Dépendance éditeur | **nulle** (MIT) | **contrat EE** pour la **prod** du code `ee/` (licence proprio — §5.2) | **forte** (SaaS, données hébergées) | partielle |
| Sortie / bascule | trivial (composant onix autonome) | rétrograder vers FOSS = perdre permission sync | export de données + ré-indexation sur site | retrait du périmètre Cloud |
| Verrouillage technique | aucun (Document Sets + JSON) | modéré | élevé | modéré |

---

## 3. Coût — données **datées** et sources (prix volatils)

> ⚠️ **Prix volatils.** Toutes les valeurs ci-dessous sont **relevées le
> 2026-06-16**. Re-vérifier sur les sources de 1er rang avant tout chiffrage
> contractuel. La recherche **Context7** prévue n'a **pas pu être exécutée**
> (outil refusé par la politique de permissions de l'environnement) ; les faits
> reposent donc sur les **sources web de 1er rang** ci-dessous (onyx.app,
> docs.onyx.app, GitHub, AWS Marketplace), qui sont prioritaires.

### 3.1 Community Edition (FOSS) — base de l'Option A
- **Licence MIT, gratuite, auto-hébergée**, couvre Chat, RAG, Agents, Actions.
  Source : **GitHub `onyx-dot-app/onyx` (README)** — *« Onyx Community Edition (CE)
  is available freely under the MIT license and covers all of the core features
  for Chat, RAG, Agents, and Actions. »* (relevé 2026-06-16).

### 3.2 Onyx Cloud (SaaS) — Option B (Cloud)
- **Business : 20 $ / utilisateur / mois** (facturation annuelle). Inclut Chat/
  Search, agents, Actions, 40+ connecteurs, APIs, **et « Basic Auth, Google OAuth,
  RBAC, Permission Inheritance, Encryption of Secrets »**, support communautaire.
- **Enterprise : « Contact us »** (tarif/déploiement flexibles) — ajoute **OIDC/SAML
  SSO, déploiements on-premise, déploiements par région**, white-label, SLA,
  remises volume, support dédié.
- Source : **onyx.app/pricing** (page tarifs officielle, relevé **2026-06-16**).

### 3.3 Enterprise Edition (auto-hébergée) — Option B (EE)
- **Pas de tarif public.** Acquisition **sur devis** : *« contact us at
  hello@onyx.app »* / *« founders@onyx.app »*. Source : **docs.onyx.app — Enterprise
  Edition** et **onyx.app/pricing** (relevé 2026-06-16).
- **AWS Marketplace** (« Onyx Enterprise Edition ») : contrat **12 mois**, dimensions
  *Number of Users* / *Instances*, prix affichés = **valeurs de remplissage**
  (placeholder à 100 000 000 $) → en pratique **devis vendeur** (`founders@onyx.app`).
  Source : **AWS Marketplace, listing Onyx Enterprise Edition** (relevé 2026-06-16).

### 3.4 Estimations de tiers — **NON confirmées** (à traiter avec prudence)
- Des **agrégateurs/blogs tiers** citent, **sans confirmation par Onyx** :
  un **Cloud à ~16 $/siège/mois** (≠ 20 $ officiel relevé en 3.2) et un palier
  **Enterprise « 50 000 $+/an, ≥ 100 sièges, frais de support obligatoire »**.
  Sources : **Dust (blog comparatif)**, **softwarefinder.com** (relevé 2026-06-16).
  → **À ne pas inscrire dans une offre** sans devis officiel : écart avec la grille
  de 1er rang, et tarifs « contact us ». **Utiliser pour ordre de grandeur
  uniquement.**

### 3.5 Chiffrage indicatif (à valider) — exemple 30 utilisateurs

| Scénario | Licence/an (relevé 2026-06-16) | Remarque |
|---|---|---|
| A — FOSS (30 u.) | **0 €** | + exploitation interne (Document Sets/mapping) |
| B — Cloud Business (30 u.) | **≈ 30 × 20 $ × 12 = 7 200 $/an** | mais Business **n'a pas** OIDC/SAML SSO (palier Enterprise) ⇒ si SSO Entra exigé → **Enterprise « contact us »** |
| B — Cloud Enterprise / EE auto-hébergée | **sur devis** | ordre de grandeur tiers non confirmé (§3.4) |
| C — Hybride | **0 €** + devis **sur le seul périmètre EE** | minimise le coût licence en réservant l'EE au strict nécessaire |

> **Point d'attention tarifaire majeur.** Pour un assistant d'entreprise, le **SSO
> OIDC/SAML** est en pratique requis. Sur Onyx Cloud, il **bascule l'offre au palier
> Enterprise (« contact us »)**, pas au Business 20 $/u. La voie **FOSS contourne ce
> coût** en obtenant l'identité via le **claim `groups` OIDC ou Microsoft Graph**,
> indépendamment du SSO payant d'Onyx (cf. [`RBAC.md`](RBAC.md) §3).

---

## 4. Risque résiduel **précis** du mode FOSS (Option A)

Le cloisonnement FOSS borne la recherche au(x) **Document Set(s)** du périmètre de
l'utilisateur. À l'intérieur d'un périmètre, **il n'y a pas de re-filtrage par
fichier**. Concrètement, pour **deux utilisateurs U1 et U2 du même groupe** mappé
au **même Document Set DS** :

**Ce que la voie FOSS GARANTIT (ne PEUT PAS être violé par U1) :**
- U1 **ne peut pas** interroger un Document Set **hors** de son périmètre (filtre
  forcé + intersection ; demande hors-périmètre → **403**).
- U1 **ne peut pas** élargir son périmètre via un payload trafiqué (`document_set`
  intersecté) ni via `search_doc_ids` (**neutralisé**).
- Un utilisateur **sans groupe mappé** (ou sans claim groupe résolvable) est
  **refusé** (deny-by-default, fail-closed).

**Ce que la voie FOSS NE garantit PAS (risque résiduel à acter) :**
- Si **DS agrège des fichiers dont l'accès diffère** entre U1 et U2 (ex. U1 a accès
  au dossier « Client X » mais **pas** U2, alors que les deux sont mappés à DS),
  alors :
  - **Côté RENDU/CITATION** : depuis le workstream `feat/rbac-perdoc`, le filtre
    [`doc_acl.py`](../access-gateway/app/doc_acl.py) **retire** les citations
    vers « Client X » dans la réponse rendue à U2, et **substitue** un refus
    sourcé si toutes les citations sont retirées. Le risque visible
    (citations affichées + snippets) est **fermé en FOSS**.
  - **Côté RÉCUPÉRATION/LLM** : Onyx FOSS récupère et fait raisonner le LLM
    sur tout DS (pas d'ACL par-fichier à la recherche). Le LLM peut donc
    **avoir vu** le contenu de « Client X » et — dans le pire des cas —
    avoir formulé sa réponse à partir de ce contenu, sans citation traçable
    (le filtre retire la citation mais pas le fragment de texte). **Ce
    résidu n'est levable qu'en EE** (permission sync, ACL à la recherche)
    OU par instances Onyx **séparées par tier d'accès**.
  ⇒ **Mitigation recommandée** : un Document Set conçu **HOMOGÈNE** reste
    l'invariant de premier rang (`docs/RBAC.md` §6.2) ; le filtre par-document
    par-RÉPONSE ferme la fuite VISIBLE quand l'homogénéité est imparfaite.
- **Révocation différée.** Le retrait de U1 d'un **groupe Entra** ne prend effet
  qu'au **ré-login** (claim) ou à l'**expiration du cache** Graph
  (`GATEWAY_GROUP_CACHE_TTL`, défaut **300 s**). Le retrait de l'accès **fichier**
  côté SharePoint **n'est pas** propagé à l'index FOSS (il faut **ré-indexer/re-
  cloisonner**). En EE, la sync ACL le reflète automatiquement (ordre **minutes**).
- **Confiance dans l'amont.** Si l'UI/API Onyx native est **exposée**, le filtre est
  **contournable** (l'utilisateur appelle Onyx en direct). ⇒ **Mitigation
  obligatoire : la passerelle est le SEUL point d'entrée** (§6).

**Formulation pour l'audit/assurance.** *« En édition gratuite, deux membres d'un
même groupe d'accès partagent la visibilité **à la recherche** de tout le périmètre
(Document Set) qui leur est mappé. Le **rendu** (citations visibles, refus si zéro
citation accessible) est cloisonné **par document** côté passerelle (`doc_acl.py`,
filtre de sortie). La séparation **stricte par document à la recherche** (zéro
fuite indirecte par le texte généré) et la propagation automatique des révocations
SharePoint requièrent l'Enterprise Edition (permission sync). Les périmètres sont
donc conçus homogènes ; le filtre par-document côté réponse renforce le rendu. »*

---

## 5. Pourquoi le par-document est **EE/Cloud** (preuves datées)

### 5.1 Faits produit (sources de 1er rang, relevé 2026-06-16)
- **Permission Sync / RBAC fin = Enterprise.** *docs.onyx.app — Enterprise
  Edition* liste « **Permission Sync Connectors** » (*« Automatically inherit user
  permissions from external systems »*) et le **RBAC fin par groupe** (*« Fine-
  grained, group-based access control for Connectors, Document Sets, and Agents »*)
  comme **fonctions EE**.
- **SharePoint : permission sync EE-only + certificat.** *docs.onyx.app —
  SharePoint* : le connecteur *« respects all user permissions (**Enterprise Edition
  only**) »* et *« **Permission sync is only available with certificate-based
  authentication** »*. La sync couvre Confluence, Jira, GitHub, Google Drive, Gmail,
  Slack, Salesforce, **SharePoint**.
- Cohérent avec les citations déjà présentes dans [`RBAC.md`](RBAC.md) (*« Different
  access to documents is only available in the Enterprise Edition »*, *« Permission-
  syncing connectors are an Enterprise Edition feature »*).

### 5.2 Fait de licence (réversibilité)
- Le code **Enterprise** d'Onyx (`backend/ee/`) **n'est pas MIT** : licence
  **propriétaire**. Extrait : le logiciel *« may only be used in production, if you
  (and any entity that you represent) have agreed to, and are in compliance with,
  the **Onyx Subscription Terms** »*. Usage **dev/test** permis sans abonnement,
  **production interdite** sans licence EE. Source : **GitHub
  `onyx-dot-app/onyx`, `backend/ee/LICENSE`** (relevé 2026-06-16).
  ⇒ **Activer la permission sync en prod = abonnement EE** (impact §2.5 réversibilité).

---

## 6. Durcissement de la passerelle (résumé opérationnel)

> Détails et exemples : [`../access-gateway/README.md`](../access-gateway/README.md)
> (section « Durcissement & déploiement ») et le code.

1. **Seul point d'entrée.** Déployer Onyx (`web_server`/`api_server`) sur
   `onix-net` **sans port hôte** ; le reverse-proxy TLS public route **uniquement**
   vers `access-gateway` (port 8200). **Aucune** route publique vers Onyx natif.
   *Sans cela, le filtre Document Set est contournable.*
2. **SSO en amont obligatoire.** Le reverse-proxy/IdP authentifie (OIDC) et injecte
   `X-OIDC-Claims` (**claims vérifiés**). La passerelle **fait confiance** à cet
   en-tête : ne **jamais** l'exposer sans cette couche.
3. **Fail-closed** (testé) :
   - identité illisible/absente → **401** ;
   - groupes **irrésolvables** (overage + repli Graph indisponible/en erreur) →
     **502** (jamais un passage « ouvert ») ;
   - **aucun** Document Set autorisé → **403** (`GATEWAY_DENY_IF_NO_MATCH=true`,
     défaut) ;
   - demande ciblant **uniquement** un set hors-périmètre → **403**.
4. **Journal des décisions d'accès (haché)** : chaque allow/deny est journalisé en
   JSON (`onix.gateway.audit`) avec **identité pseudonymisée** (HMAC-SHA256, sel
   `GATEWAY_AUDIT_SALT`) — **jamais** l'UPN/oid en clair, **jamais** le message
   utilisateur. Source : [`../access-gateway/app/audit.py`](../access-gateway/app/audit.py).
5. **Anti-élargissement** : intersection des `document_set` demandés avec les
   autorisés ; `search_doc_ids` **neutralisé** (non vérifiable par fichier en FOSS).

**Tests dédiés (52 au total) :** utilisateur **sans groupe** → deny ; **multi-
groupes** → **union des Document Sets autorisés uniquement** (ni plus, ni moins) ;
fail-closed sur groupes irrésolvables ; non-fuite d'identité/contenu dans l'audit.
Lancer : `pytest access-gateway/tests -q`.

---

## 7. Recommandation par scénario

| Scénario client | Recommandation | Justification |
|---|---|---|
| **Poste/équipe homogène**, ou **multi-commerciaux** où chaque équipe a **son** portefeuille (accès homogène par périmètre) | **Option A — FOSS + `access-gateway`** | Couvre le besoin réel ; **0 € de licence** ; souverain ; réversible. Risque résiduel (§4) acceptable **si les Document Sets sont homogènes**. |
| **Multi-commerciaux à droits hétérogènes FINS** (même dossier, accès ≠ entre membres) **ou** exigence de **propagation automatique** des révocations SharePoint **ou** cloisonnement **légal strict par document** | **Option B — EE / Cloud** (permission sync, certificat) | Seule voie pour le **trimming par document** et la **sync ACL**. Coût licence ≠ 0 (§3) ; arbitrer EE auto-hébergée (souverain) vs Cloud (géré, transfert de données). |
| Besoin **mixte** : la plupart des périmètres homogènes, **quelques** périmètres sensibles exigeant le par-document | **Option C — Hybride** | FOSS partout (gratuit) + EE/Cloud **sur le seul périmètre sensible** → minimise le coût licence tout en levant le risque résiduel **là où il compte**. |
| Contrainte **souveraineté absolue** (aucun transfert hors site) | **A** ou **B-EE auto-hébergée** ; **éviter le Cloud** | Le Cloud héberge les données chez l'éditeur (sous-traitant art. 28 à encadrer). |

---

## 8. Dans quelle mesure l'astérisque est « cadré »

- **Décision documentée** : matrice multi-axes + recommandation par scénario
  (§2, §7), **chiffrée et datée** (§3), avec risque résiduel **quantifié** (§4).
- **Passerelle durcie et testée** (§6) : seul point d'entrée, fail-closed, audit
  haché, anti-élargissement, **52 tests verts**.
- **Limite assumée, non masquée** : le **par-document strict** et la **propagation
  automatique des révocations** **restent Enterprise Edition / Cloud** (§5, preuves
  de 1er rang datées). La voie FOSS **ne les reproduit pas** ; elle **borne** le
  risque (périmètres homogènes) et **l'outille** (décision, tests, journal).

> En clair : l'astérisque est **cadré** (le client peut **décider en connaissance de
> cause**, avec un chiffrage et un risque résiduel explicites), **pas supprimé**
> (le trimming par-document reste une fonction payante d'Onyx).

---

## Sources (relevé 2026-06-16 — prix volatils, re-vérifier avant offre)

- onyx.app/pricing — paliers **Business 20 $/u/mois** & **Enterprise « contact us »** (OIDC/SAML SSO, on-prem). *1er rang.*
- docs.onyx.app — **Enterprise Edition** (Permission Sync Connectors, RBAC fin = EE). *1er rang.*
- docs.onyx.app — **SharePoint** (permission sync « Enterprise Edition only », « only available with certificate-based authentication »). *1er rang.*
- GitHub `onyx-dot-app/onyx` — **README** (CE = MIT) & **`backend/ee/LICENSE`** (EE proprio, prod sous **Onyx Subscription Terms**). *1er rang.*
- AWS Marketplace — **Onyx Enterprise Edition** (contrat 12 mois, prix = devis vendeur). *1er rang.*
- Dust (blog comparatif), softwarefinder.com — estimations tierces (~16 $/siège ; « 50 000 $+/an, ≥100 sièges ») **non confirmées** par Onyx → ordre de grandeur seulement. *2nd rang.*
- Note : la recherche **Context7** prévue par le périmètre **n'a pas pu être exécutée** (outil refusé par la politique de permissions de l'environnement) ; les faits ci-dessus reposent sur les sources web de 1er rang (prioritaires).
