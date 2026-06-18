#!/usr/bin/env bash
# =============================================================================
# setup-fabric-app.sh — prépare l'app Entra (SPN) qui sert l'accès onix à
# Microsoft Fabric / OneLake / Power BI + le RBAC SharePoint par-document
# (modules `access-gateway/app/fabric_client.py`, `fabric_acl.py`,
# `graph_client.py`, `graph_acl.py`). Calqué sur `setup-sharepoint-app.sh`.
#
# Ce que CE script automatise (via `az`, si présent) :
#   - création/repérage idempotent de l'app Entra + de son service principal ;
#   - ajout des permissions APPLICATION Microsoft Graph nécessaires au RBAC
#     SharePoint par-document (Sites.Read.All, Files.Read.All, GroupMember.Read.All) ;
#   - consentement administrateur ;
#   - génération d'un secret client (affiché UNE fois, jamais committé).
#
# Ce que CE script NE PEUT PAS automatiser (réglages TENANT côté portail admin
# Fabric / Power BI — pas d'API standard `az` ; étapes MANUELLES rappelées) :
#   - activer « Service principals can use Fabric APIs » (Admin portal → Tenant
#     settings → Developer settings) ;
#   - activer « Service principals can use Power BI APIs » (si Power BI utilisé) ;
#   - ajouter le SPN à un RÔLE de workspace Fabric (Viewer = lecture du contrôle ;
#     Member = lecture des données OneLake). Le SPN n'a PAS de scope délégué :
#     son accès Fabric est régi par ces contrôles admin + les rôles d'artefact.
#
# À LANCER SUR VOTRE POSTE az-connecté (`az login` avec un rôle permettant le
# CONSENTEMENT ADMIN : Application Administrator / Cloud Application Admin /
# Privileged Role Admin). NE tourne PAS dans le conteneur (pas d'az/creds).
#
# ZÉRO secret en dur. Le secret généré n'est affiché qu'à l'écran ; vous le
# posez HORS-REPO (fichier `.env` gitignoré, coffre, ou variables ONIX_E2E_*).
# Détails d'usage : docs/connectors/FABRIC.md et docs/E2E_ACCESS_LIVE.md.
# =============================================================================
set -euo pipefail

# --- Paramètres (surchargeables par l'environnement) ------------------------
: "${TENANT_ID:?export TENANT_ID=<votre tenant Entra>}"
APP_NAME="${APP_NAME:-onix-fabric}"

# Identifiant constant de l'API Microsoft Graph (« first-party » Microsoft).
GRAPH_APPID="00000003-0000-0000-c000-000000000000"

# IDs des appRoles APPLICATION Graph requis (constants, publics, documentés
# Microsoft — ce ne sont PAS des secrets) :
SITES_READ_ALL="332a536c-c7ef-4017-ab91-336970924f0d"      # Graph Sites.Read.All (Application)
FILES_READ_ALL="01d4889c-1287-42c6-ac1f-5d1e02578ef6"      # Graph Files.Read.All (Application)
GROUPMEMBER_READ_ALL="98830695-27a2-44f7-8c18-0c3ebc9698f6" # Graph GroupMember.Read.All (Application)

# --- Pré-requis -------------------------------------------------------------
command -v az >/dev/null || {
  cat <<'EOF'
⚠ az CLI introuvable — ce script s'automatise avec Azure CLI (à lancer sur VOTRE
  poste, pas dans le conteneur). Sans az, suivez les étapes MANUELLES :

  1. Portail Azure → App registrations → New registration (mono-tenant), nom p.ex.
     « onix-fabric ». Notez Application (client) ID + Directory (tenant) ID.
  2. API permissions → Add a permission → Microsoft Graph → Application permissions :
        - Sites.Read.All        (ou Sites.Selected + octroi par site, moindre privilège)
        - Files.Read.All
        - GroupMember.Read.All  (résolution des groupes transitifs)
     → Grant admin consent.
  3. Certificates & secrets → New client secret → notez la valeur (HORS-REPO).
  4. Réglages TENANT (Admin portal Fabric/Power BI) + rôles de workspace : voir
     la section « RAPPELS » imprimée plus bas et docs/connectors/FABRIC.md.
EOF
  exit 1
}
az account show >/dev/null || { echo "Lancez 'az login' d'abord."; exit 1; }

# --- App Entra (idempotent) -------------------------------------------------
# `az ad app list` renvoie une liste vide (pas d'erreur) si aucune app ne matche.
echo "→ Recherche d'une app Entra '$APP_NAME' existante…"
APPID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)
if [ -n "$APPID" ]; then
  echo "   App déjà présente (appId=$APPID) — réutilisée, pas de doublon."
else
  echo "→ Création de l'app Entra '$APP_NAME'…"
  APPID=$(az ad app create --display-name "$APP_NAME" --sign-in-audience AzureADMyOrg --query appId -o tsv)
fi

# --- Permissions Graph (Application) ---------------------------------------
# `permission add` est idempotent côté manifeste (re-pose le même appRole sans
# doublon). On accorde le strict nécessaire au RBAC par-document SharePoint.
echo "→ Permissions Graph (Application) : Sites.Read.All, Files.Read.All, GroupMember.Read.All…"
az ad app permission add --id "$APPID" --api "$GRAPH_APPID" \
  --api-permissions "${SITES_READ_ALL}=Role"
az ad app permission add --id "$APPID" --api "$GRAPH_APPID" \
  --api-permissions "${FILES_READ_ALL}=Role"
az ad app permission add --id "$APPID" --api "$GRAPH_APPID" \
  --api-permissions "${GROUPMEMBER_READ_ALL}=Role"

# Service principal : indispensable pour le consentement ET pour pouvoir ajouter
# le SPN à un rôle de workspace Fabric. Ignore l'erreur s'il existe déjà.
echo "→ Service principal (création si absent)…"
SP_OBJECT_ID=$(az ad sp create --id "$APPID" --query id -o tsv 2>/dev/null || true)
if [ -z "${SP_OBJECT_ID:-}" ]; then
  SP_OBJECT_ID=$(az ad sp show --id "$APPID" --query id -o tsv 2>/dev/null || true)
fi

# --- Secret client (HORS-REPO) ---------------------------------------------
# `credential reset` crée un NOUVEAU secret à chaque exécution (les anciens
# restent valides jusqu'à expiration). À ré-exécuter quand le secret a expiré.
echo "→ Génération du secret client (affiché UNE seule fois)…"
SECRET=$(az ad app credential reset --id "$APPID" --display-name "onix" --query password -o tsv)

# --- Consentement administrateur -------------------------------------------
echo "→ Consentement administrateur (Grant admin consent)…"
az ad app permission admin-consent --id "$APPID"

# --- Sortie : ce qu'il reste à faire À LA MAIN + où poser les secrets -------
cat <<EOF

✅ App Entra '$APP_NAME' prête (Graph Sites.Read.All + Files.Read.All +
   GroupMember.Read.All, consentie). Tenant : $TENANT_ID
   appId (client)      = $APPID
   objectId du SPN     = ${SP_OBJECT_ID:-<non résolu — voir 'az ad sp show'>}

   À POSER HORS-REPO (jamais committé — .env gitignoré / coffre / variables) :
   ┌────────────────────────────────────────────────────────────┐
   │ client_id     = $APPID
   │ client_secret = $SECRET
   │ tenant_id     = $TENANT_ID
   └────────────────────────────────────────────────────────────┘
   Variables passerelle  : GATEWAY_GRAPH_TENANT_ID / _CLIENT_ID / _CLIENT_SECRET
     (Fabric réutilise par défaut le même SPN ; overrides GATEWAY_FABRIC_* si
      le SPN Fabric diffère — cf. access-gateway/app/config.py).
   Variables harnais e2e : ONIX_E2E_TENANT_ID / _CLIENT_ID / _CLIENT_SECRET
     (cf. access-gateway/tests/e2e/README_ACCESS_E2E.md, docs/E2E_ACCESS_LIVE.md).
   ⚠ Notez le secret MAINTENANT : il n'est PAS ré-affiché.

────────────────────────────────────────────────────────────────────────────
RAPPELS — étapes TENANT NON automatisables par ce script (à faire À LA MAIN) :
────────────────────────────────────────────────────────────────────────────
  1. Fabric — Admin portal → Tenant settings → Developer settings :
     activer « Service principals can use Fabric APIs » (idéalement restreint à
     un groupe de sécurité contenant CE SPN : objectId ${SP_OBJECT_ID:-<SPN>}).
     Sans ce réglage, les appels Fabric renvoient 401/403.

  2. Fabric — ajouter le SPN à un RÔLE de workspace (Workspace → Manage access →
     Add people or groups → coller '$APP_NAME' ou son objectId) :
        - Viewer       → lecture du CONTRÔLE (list_workspaces / items / roleAssignments) ;
        - Member       → lecture des DONNÉES OneLake (onelake_list_paths / read_file).
     (Les scopes délégués ne s'appliquent PAS au SPN : l'accès dépend de CE rôle.)

  3. Power BI (si datasets utilisés) — Admin portal → Tenant settings :
     activer « Service principals can use Power BI APIs » + ajouter le SPN à
     l'accès du workspace Power BI ciblé.

  4. (Optionnel, OneLake fin PREVIEW) data access roles / principalAccess par
     artefact : voir docs/connectors/FABRIC.md (limites + fail-closed).

Audiences de jeton (rappel, gérées par fabric_client.py — aucune action ici) :
  contrôle Fabric : https://api.fabric.microsoft.com/.default
  données OneLake  : https://storage.azure.com/.default
  Power BI         : https://analysis.windows.net/powerbi/api/.default
  Graph (SharePoint) : https://graph.microsoft.com/.default

Suite : docs/connectors/FABRIC.md (scope) · docs/E2E_ACCESS_LIVE.md (runbook).
EOF
