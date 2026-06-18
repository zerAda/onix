# Harnais e2e LIVE — accès SharePoint + Microsoft Fabric

`run_access_e2e.py` prouve, **contre un vrai tenant Entra** (live-only), que les
modules d'onix accèdent réellement à SharePoint (Microsoft Graph) et à Microsoft
Fabric, et que le **RBAC est fail-closed** (un autorisé est accordé, un
non-autorisé est refusé).

Il **réutilise** le code de service déployé (aucune réimplémentation) :
`app/graph_client.py`, `app/graph_acl.py`, `app/fabric_client.py`,
`app/fabric_acl.py`, `app/config.py`. Ce n'est **pas** un test pytest : c'est un
script autonome qui imprime un rapport et renvoie un code de sortie.

> Zéro secret en repo : tout vient de variables d'environnement. Aucun jeton ni
> secret n'est jamais journalisé.

## Scénarios

| Code | Bloc | Vérifie |
|---|---|---|
| A1 | SharePoint | jeton Graph (client credentials) + listing d'un drive (auth + lecture) |
| A2 | SharePoint | utilisateur **autorisé** → ACCORDÉ sur l'item de test (groupes transitifs ∩ permissions de l'item) |
| A3 | SharePoint | utilisateur **non-autorisé** → REFUSÉ (fail-closed) |
| B1 | Fabric | jeton Fabric → `list_workspaces` / `list_items` |
| B2 | Fabric | jeton stockage → `onelake_list_paths` (+ lecture si `ONIX_E2E_ONELAKE_PATH`) |
| B3 | Fabric | jeton Power BI → `list_powerbi_datasets` (si `ONIX_E2E_PBI_WORKSPACE_ID`) |
| B4 | Fabric | principal **autorisé** → `can_principal_read` ACCORDE |
| B5 | Fabric | principal **non-autorisé** → `can_principal_read` REFUSE (fail-closed) |

Un bloc (A ou B) ne s'exécute que si **toutes** ses variables requises sont
présentes ; sinon il est marqué **SKIP** (le reste tourne).

## Variables d'environnement

```bash
# Communes (REQUISES pour tout bloc)
export ONIX_E2E_TENANT_ID=...           # GUID du tenant Entra
export ONIX_E2E_CLIENT_ID=...           # appId du SPN (client credentials)
export ONIX_E2E_CLIENT_SECRET=...       # secret du SPN (jamais journalisé)

# Bloc A — SharePoint (toutes requises)
export ONIX_E2E_SP_SITE_ID=...          # id du site (host,siteGuid,webGuid)
export ONIX_E2E_SP_DRIVE_ID=...         # id du drive (bibliothèque)
export ONIX_E2E_SP_ITEM_ID=...          # id du driveItem servant à la preuve RBAC
export ONIX_E2E_SP_USER_OK=...          # utilisateur attendu autorisé (oid ou UPN)
export ONIX_E2E_SP_USER_DENIED=...      # utilisateur attendu refusé (oid ou UPN)

# Bloc B — Fabric (requises)
export ONIX_E2E_FABRIC_WORKSPACE_ID=...
export ONIX_E2E_FABRIC_ITEM_ID=...
export ONIX_E2E_FABRIC_ITEM_TYPE=Lakehouse
export ONIX_E2E_FABRIC_PRINCIPAL_OK=...      # oid attendu autorisé
export ONIX_E2E_FABRIC_PRINCIPAL_DENIED=...  # oid attendu refusé
# Bloc B — optionnelles
export ONIX_E2E_ONELAKE_PATH=...        # chemin OneLake à lire
export ONIX_E2E_PBI_WORKSPACE_ID=...    # workspace Power BI à lister

# Réglages réseau (optionnels)
export ONIX_E2E_HTTP_TIMEOUT=20         # timeout HTTP (s)
# Hôtes souverains (Gov/China) : GATEWAY_GRAPH_HOST, GATEWAY_GRAPH_AUTHORITY,
# GATEWAY_FABRIC_API_HOST, GATEWAY_ONELAKE_HOST, GATEWAY_POWERBI_HOST (cf. config.py)
```

Permissions Graph applicatives minimales (admin consent) : `Sites.Read.All`
(SharePoint) et `GroupMember.Read.All` (résolution des groupes transitifs). Côté
Fabric : « Service principals can use Fabric APIs » + un rôle du workspace.

## Exécution

```bash
python access-gateway/tests/e2e/run_access_e2e.py        # exécute les blocs configurés
python access-gateway/tests/e2e/run_access_e2e.py --list-vars   # rappel des variables
python access-gateway/tests/e2e/run_access_e2e.py --help        # aide complète
```

## Codes de sortie

| Code | Signification |
|---|---|
| `0` | tous les blocs présents sont passés (et ≥1 bloc exécuté) |
| `1` | au moins un scénario a échoué |
| `2` | aucun bloc n'avait ses variables (skip total — **pas** un échec) |
