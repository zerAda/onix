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

## (a) Prérequis — à faire AVANT de lancer

À exécuter **sur votre poste az-connecté** (pas dans le conteneur). Détails de
scope Fabric : [`connectors/FABRIC.md`](connectors/FABRIC.md) ; SharePoint :
[`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md).

1. **App Entra (SPN) + permissions Graph + secret.** Lancez le script idempotent
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

### Communes — REQUISES pour tout bloc

| Variable | Sens |
|---|---|
| `ONIX_E2E_TENANT_ID` | GUID du tenant Entra |
| `ONIX_E2E_CLIENT_ID` | appId du SPN (client credentials) |
| `ONIX_E2E_CLIENT_SECRET` | secret du SPN (**jamais journalisé**) |

### Bloc A — SharePoint (toutes requises)

| Variable | Sens |
|---|---|
| `ONIX_E2E_SP_SITE_ID` | id du site (`host,siteGuid,webGuid`) |
| `ONIX_E2E_SP_DRIVE_ID` | id du drive (bibliothèque) |
| `ONIX_E2E_SP_ITEM_ID` | id du `driveItem` servant à la preuve RBAC |
| `ONIX_E2E_SP_USER_OK` | utilisateur **attendu autorisé** (oid ou UPN) |
| `ONIX_E2E_SP_USER_DENIED` | utilisateur **attendu refusé** (oid ou UPN) |

### Bloc B — Fabric (requises + optionnelles)

| Variable | Requis | Sens |
|---|---|---|
| `ONIX_E2E_FABRIC_WORKSPACE_ID` | ✅ | id du workspace Fabric |
| `ONIX_E2E_FABRIC_ITEM_ID` | ✅ | id de l'item (lakehouse…) |
| `ONIX_E2E_FABRIC_ITEM_TYPE` | ✅ | type d'item (ex. `Lakehouse`) |
| `ONIX_E2E_FABRIC_PRINCIPAL_OK` | ✅ | principal **attendu autorisé** (oid) |
| `ONIX_E2E_FABRIC_PRINCIPAL_DENIED` | ✅ | principal **attendu refusé** (oid) |
| `ONIX_E2E_ONELAKE_PATH` | optionnel | chemin OneLake à lire (sinon listing seul) |
| `ONIX_E2E_PBI_WORKSPACE_ID` | optionnel | workspace Power BI à lister (active B3) |

### Réglages réseau (optionnels)

| Variable | Défaut | Sens |
|---|---|---|
| `ONIX_E2E_HTTP_TIMEOUT` | `20` | timeout HTTP (s) |
| `GATEWAY_GRAPH_HOST` / `GATEWAY_GRAPH_AUTHORITY` | publics | hôtes souverains Graph (Gov/China) |
| `GATEWAY_FABRIC_API_HOST` / `GATEWAY_ONELAKE_HOST` / `GATEWAY_POWERBI_HOST` | publics | hôtes souverains Fabric (cf. `config.py`) |

> Le harnais mappe `ONIX_E2E_TENANT_ID`/`_CLIENT_ID`/`_CLIENT_SECRET` vers les
> `GATEWAY_GRAPH_*` lus par `app.config` ; Fabric **hérite du même SPN** par
> défaut (cf. [`connectors/FABRIC.md`](connectors/FABRIC.md) §4).

## (c) Lancer + interpréter

```bash
# Rappel des variables, sans rien exécuter :
python access-gateway/tests/e2e/run_access_e2e.py --list-vars

# Exécution (exemple bloc Fabric + commun ; ajoutez les ONIX_E2E_SP_* pour A) :
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
  faut **Member**/Contributor), ou `ONIX_E2E_FABRIC_ITEM_TYPE` / chemin faux.
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
| Tests unitaires `fabric_client`/`fabric_acl` (offline) | ✅ provider de jeton injecté (`access-gateway/tests`) |
| `run_access_e2e.py` (A1..B5) | ❌ **vrai tenant** (jeton réel, vrais ids, vrais rôles) |
| Réglages tenant Fabric / rôles workspace | ❌ Admin portal, votre tenant |
| Garde-fous `run_e2e.py` (21 vecteurs) | ❌ Ollama réel (LLM local), stack montée |

Voir aussi : [`connectors/FABRIC.md`](connectors/FABRIC.md) ·
[`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md) · [`RBAC.md`](RBAC.md) ·
[`DECISION_RBAC.md`](DECISION_RBAC.md).
