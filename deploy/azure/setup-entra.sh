#!/usr/bin/env bash
# =============================================================================
# setup-entra.sh — crée les apps Entra ID nécessaires à onix sur Azure et pousse
# les secrets dans Azure Key Vault. À LANCER SUR VOTRE POSTE az-connecté
# (`az login` + droits Application Administrator / Privileged Role Admin pour le
# consentement admin). NON exécutable depuis le conteneur d'audit (pas d'az/creds).
#
# Crée 3 enregistrements d'app (principe du moindre privilège) :
#   1) onix-sso         — OIDC connexion utilisateurs (oauth2-proxy / Onyx)
#   2) onix-graph-groups— app-only, résout les groupes Entra (passerelle RBAC)
#   3) onix-sharepoint  — app-only, indexation SharePoint (Graph Sites.Read.All)
#
# Choix actés : RBAC = access-gateway FOSS → SharePoint en INDEXATION (Sites.Read.All).
# (La perm-sync EE par-document = Sites.FullControl.All + CERTIFICAT ; non retenue.)
# =============================================================================
set -euo pipefail

# --- À renseigner ------------------------------------------------------------
: "${TENANT_ID:?export TENANT_ID=<votre tenant gerep>}"
: "${KEYVAULT:?export KEYVAULT=<nom du Key Vault>}"
DOMAIN="${DOMAIN:-onix.example.com}"            # nom DNS public (redirect OIDC)
GRAPH="https://graph.microsoft.com"

command -v az >/dev/null || { echo "az CLI requis (ce script tourne sur VOTRE poste)"; exit 1; }
az account show >/dev/null || { echo "Lancez 'az login' d'abord."; exit 1; }

# IDs de permissions Graph (constants Microsoft) :
SITES_READ_ALL="332a536c-c7ef-4017-ab91-336970924f0d"   # Sites.Read.All (Application)
GROUPMEMBER_READ_ALL="98830695-27a2-44f7-8c18-0c3ebc9698f6" # GroupMember.Read.All (Application)
GRAPH_APPID="00000003-0000-0000-c000-000000000000"

echo "→ 1/3 App SSO (OIDC utilisateurs)"
SSO_APPID=$(az ad app create --display-name "onix-sso" \
  --web-redirect-uris "https://${DOMAIN}/oauth2/callback" \
  --enable-id-token-issuance true --sign-in-audience AzureADMyOrg \
  --query appId -o tsv)
SSO_SECRET=$(az ad app credential reset --id "$SSO_APPID" --display-name "onix" --query password -o tsv)

echo "→ 2/3 App Graph (résolution de groupes — passerelle RBAC, app-only)"
GRP_APPID=$(az ad app create --display-name "onix-graph-groups" --sign-in-audience AzureADMyOrg --query appId -o tsv)
az ad app permission add --id "$GRP_APPID" --api "$GRAPH_APPID" --api-permissions "${GROUPMEMBER_READ_ALL}=Role"
GRP_SECRET=$(az ad app credential reset --id "$GRP_APPID" --display-name "onix" --query password -o tsv)
az ad sp create --id "$GRP_APPID" >/dev/null 2>&1 || true
az ad app permission admin-consent --id "$GRP_APPID"   # nécessite un rôle admin

echo "→ 3/3 App SharePoint (indexation Onyx, app-only — Sites.Read.All)"
SP_APPID=$(az ad app create --display-name "onix-sharepoint" --sign-in-audience AzureADMyOrg --query appId -o tsv)
az ad app permission add --id "$SP_APPID" --api "$GRAPH_APPID" --api-permissions "${SITES_READ_ALL}=Role"
SP_SECRET=$(az ad app credential reset --id "$SP_APPID" --display-name "onix" --query password -o tsv)
az ad sp create --id "$SP_APPID" >/dev/null 2>&1 || true
az ad app permission admin-consent --id "$SP_APPID"
# NB perm-sync EE (non retenue) : il faudrait Sites.FullControl.All + un CERTIFICAT
#     (az ad app credential reset --cert ...) au lieu d'un secret.

echo "→ Stockage des secrets dans Key Vault '$KEYVAULT'"
kv() { az keyvault secret set --vault-name "$KEYVAULT" --name "$1" --value "$2" >/dev/null && echo "   ✓ $1"; }
kv onix-sso-client-id            "$SSO_APPID"
kv onix-sso-client-secret        "$SSO_SECRET"
kv GATEWAY-GRAPH-CLIENT-ID       "$GRP_APPID"
kv GATEWAY-GRAPH-CLIENT-SECRET   "$GRP_SECRET"
kv sharepoint-client-id          "$SP_APPID"
kv sharepoint-client-secret      "$SP_SECRET"

cat <<EOF

✅ Apps Entra créées + secrets dans Key Vault '$KEYVAULT'. Tenant: $TENANT_ID
   SSO appId         : $SSO_APPID
   Graph-groups appId: $GRP_APPID   (GroupMember.Read.All, consentie)
   SharePoint appId  : $SP_APPID    (Sites.Read.All, consentie)

Suite : connecteur SharePoint dans Onyx (Admin → Connectors → SharePoint) avec
client-id/secret 'sharepoint-*', site https://gerep75008.sharepoint.com/sites/dev-assistant-client-360.
Détails : docs/DEPLOY_AZURE.md.
EOF
