#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Déploiement de la landing zone onix (Bicep) sur Azure / AKS
#
# Reproduit docs/DEPLOY_AZURE.md (P0/P1) en IaC :
#   1) az group create (RG pré-créé)
#   2) secret POSTGRES-ADMIN-PASSWORD dans un Key Vault d'amorçage (si absent)
#   3) az deployment group create --what-if  (revue obligatoire)
#   4) apply
#   5) post-steps (get-credentials, rappels chart / setup-entra)
#
# ⚠ HONNÊTETÉ : seul un vrai `az deployment group ... --what-if` sur VOTRE tenant
# confirme quotas (vCPU E-series), unicité globale (ACR/KV) et disponibilité SKU
# (Redis Premium zones, NC8as_T4_v3) en France Central. Faites le --what-if d'abord.
# =============================================================================
set -euo pipefail

# --- Variables (alignées sur DEPLOY_AZURE.md §0) -----------------------------
RG="${RG:-onix-rg}"
LOC="${LOC:-francecentral}"
DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-onix-landingzone}"
BICEP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${BICEP_DIR}/main.bicep"
PARAMS="${BICEP_DIR}/main.bicepparam"

# Key Vault d'amorçage hébergeant le secret Postgres AVANT le déploiement.
# (Peut être un petit vault dédié ; le Key Vault applicatif onix est créé par le template.)
BOOTSTRAP_KV="${BOOTSTRAP_KV:-onix-bootstrap-kv}"

command -v az >/dev/null || { echo "az CLI requis."; exit 1; }
az account show >/dev/null || { echo "Lancez 'az login' d'abord."; exit 1; }

echo "==> 1/5 Resource group ${RG} (${LOC})"
az group create -n "${RG}" -l "${LOC}" -o none

echo "==> 2/5 Key Vault d'amorçage + secret Postgres (idempotent)"
# Crée le vault d'amorçage s'il n'existe pas (RBAC), puis pose le secret si absent.
if ! az keyvault show -n "${BOOTSTRAP_KV}" -g "${RG}" -o none 2>/dev/null; then
  az keyvault create -g "${RG}" -n "${BOOTSTRAP_KV}" -l "${LOC}" \
    --enable-rbac-authorization true -o none
  echo "    (pensez à vous donner 'Key Vault Secrets Officer' sur ${BOOTSTRAP_KV})"
fi
if ! az keyvault secret show --vault-name "${BOOTSTRAP_KV}" -n POSTGRES-ADMIN-PASSWORD -o none 2>/dev/null; then
  az keyvault secret set --vault-name "${BOOTSTRAP_KV}" -n POSTGRES-ADMIN-PASSWORD \
    --value "$(openssl rand -base64 32)" -o none
  echo "    secret POSTGRES-ADMIN-PASSWORD généré dans ${BOOTSTRAP_KV}"
fi

echo "==> 3/5 WHAT-IF (revue des changements — RIEN n'est appliqué)"
az deployment group what-if \
  -g "${RG}" -n "${DEPLOYMENT_NAME}" \
  -f "${TEMPLATE}" -p "${PARAMS}"

read -r -p "Appliquer ce déploiement ? [y/N] " ans
[[ "${ans:-N}" =~ ^[Yy]$ ]] || { echo "Abandon (what-if seulement)."; exit 0; }

echo "==> 4/5 APPLY"
az deployment group create \
  -g "${RG}" -n "${DEPLOYMENT_NAME}" \
  -f "${TEMPLATE}" -p "${PARAMS}" -o json > /tmp/onix-deploy-out.json
echo "    sorties -> /tmp/onix-deploy-out.json"

echo "==> 5/5 Post-steps"
AKS_NAME="$(az deployment group show -g "${RG}" -n "${DEPLOYMENT_NAME}" \
  --query properties.outputs.aksClusterName.value -o tsv)"
az aks get-credentials -g "${RG}" -n "${AKS_NAME}" --overwrite-existing
kubectl create namespace onix --dry-run=client -o yaml | kubectl apply -f -

cat <<'NEXT'

---------------------------------------------------------------------------
Landing zone déployée. Étapes suivantes (cf. docs/DEPLOY_AZURE.md P1bis -> P5) :

  - P1bis : setup-entra.sh (apps Entra SSO + Graph + SharePoint -> Key Vault)
  - P2    : pousser les secrets applicatifs dans le Key Vault onix
            (SECRET, USER_AUTH_SECRET, ENCRYPTION-KEY-SECRET, REDIS_PASSWORD, ...)
            puis SecretProviderClass (CSI) avec workloadIdentityClientId (sortie).
  - P2    : az acr build des images custom (onix-actions, onix-access-gateway).
  - P3    : helm install -f deploy/azure/values-azure.yaml
            (renseigner <ACR>, <PG_FQDN>, <REDIS_FQDN> depuis les sorties ci-dessous).

Sorties clés du déploiement :
NEXT
az deployment group show -g "${RG}" -n "${DEPLOYMENT_NAME}" \
  --query "properties.outputs.{acr:acrLoginServer.value, pg:postgresFqdn.value, redis:redisHostName.value, redisPort:redisSslPort.value, kv:keyVaultName.value, oidc:aksOidcIssuerUrl.value, workloadClientId:workloadIdentityClientId.value, gpu:gpuEnabled.value}" \
  -o yaml
