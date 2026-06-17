#!/usr/bin/env bash
# =============================================================================
# setup-sharepoint-app.sh — crée l'app Entra du connecteur SharePoint d'Onyx
# (INDEXATION, édition FOSS) et imprime les 3 valeurs à coller dans Onyx
# (Admin → Connectors → SharePoint → New credential).
#
# À LANCER SUR VOTRE POSTE az-connecté (`az login` + un rôle permettant le
# CONSENTEMENT ADMIN : Application Administrator / Cloud Application Admin / Privileged
# Role Admin). NE tourne PAS dans le conteneur (pas d'az/creds).
#
# Édition FOSS = INDEXATION uniquement (Sites.Read.All). La permission-sync par
# DOCUMENT (qui réplique les ACL SharePoint) est réservée à Onyx EE et exige
# Sites.FullControl.All + un CERTIFICAT — hors périmètre de ce POC.
# =============================================================================
set -euo pipefail
: "${TENANT_ID:?export TENANT_ID=<votre tenant gerep>}"
SITE="${SITE:-https://gerep75008.sharepoint.com/sites/dev-assistant-client-360}"

GRAPH_APPID="00000003-0000-0000-c000-000000000000"
SITES_READ_ALL="332a536c-c7ef-4017-ab91-336970924f0d"   # Graph Sites.Read.All (Application)

command -v az >/dev/null || { echo "az CLI requis (script à lancer sur VOTRE poste)"; exit 1; }
az account show >/dev/null || { echo "Lancez 'az login' d'abord."; exit 1; }

APP_NAME="onix-sharepoint"

# Idempotence : réutilise l'app si elle existe déjà (ré-exécution sans doublon).
# `az ad app list` renvoie une liste vide (pas d'erreur) si aucune app ne matche.
echo "→ Recherche d'une app Entra '$APP_NAME' existante…"
APPID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)
if [ -n "$APPID" ]; then
  echo "   App déjà présente (appId=$APPID) — réutilisée, pas de doublon."
else
  echo "→ Création de l'app Entra '$APP_NAME'…"
  APPID=$(az ad app create --display-name "$APP_NAME" --sign-in-audience AzureADMyOrg --query appId -o tsv)
fi

echo "→ Permission Graph Sites.Read.All (Application)…"
# `permission add` est idempotent côté manifeste (re-pose le même appRole sans doublon).
az ad app permission add --id "$APPID" --api "$GRAPH_APPID" --api-permissions "${SITES_READ_ALL}=Role"

# Service principal : indispensable pour le consentement ; ignore l'erreur s'il existe déjà.
az ad sp create --id "$APPID" >/dev/null 2>&1 || true

echo "→ Génération du secret client…"
# `credential reset` crée un NOUVEAU secret à chaque exécution (les anciens restent
# valides jusqu'à expiration). À ré-exécuter justement quand le secret a expiré.
SECRET=$(az ad app credential reset --id "$APPID" --display-name "onix" --query password -o tsv)

echo "→ Consentement administrateur (Grant admin consent)…"
az ad app permission admin-consent --id "$APPID"

cat <<EOF

✅ App SharePoint créée et consentie (Sites.Read.All). Tenant : $TENANT_ID

   À COLLER dans Onyx → Admin → Connectors → SharePoint → New credential :
   ┌────────────────────────────────────────────────────────────┐
   │ sp_client_id     = $APPID
   │ sp_client_secret = $SECRET
   │ sp_directory_id  = $TENANT_ID
   └────────────────────────────────────────────────────────────┘
   Puis, config du connecteur — « Enter SharePoint sites » :
   $SITE

⚠ Notez le secret MAINTENANT (non ré-affiché). Édition FOSS = indexation sans
  permission-sync par-document. Détails : docs/POC_LOCAL.md.
EOF
