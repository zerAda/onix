// =============================================================================
// main.bicep — Landing zone onix sur Azure / AKS (France Central)
//
// Orchestrateur modulaire reproduisant docs/DEPLOY_AZURE.md (P0/P1) :
//   - Réseau privé (VNet + subnets + NSG + zones DNS privées).
//   - ACR Standard + cache pull-through Docker Hub onyxdotapp/*.
//   - AKS PRIVÉ : system pool + CPU app pool (E8s_v5) + GPU pool OPTIONNEL,
//     OIDC + Workload Identity, addons Key Vault CSI + Prometheus managé.
//   - PostgreSQL Flexible MANAGÉ (HA ZoneRedundant, privé) + base onyx.
//   - Azure Cache for Redis Premium MANAGÉ (noeviction, TLS, privé).
//   - Key Vault (RBAC) + identité workload AKS (Secrets User).
//   - Monitoring : Log Analytics + Azure Monitor Workspace + Managed Grafana.
//
// Décisions ACTÉES : Ollama CPU (GPU pool default OFF) · Postgres + Redis managés ·
// OpenSearch + MinIO in-cluster (déployés par le chart Helm, hors IaC) · RBAC FOSS.
//
// Le RG est PRÉ-CRÉÉ (az group create) — cf. deploy.sh. targetScope=resourceGroup.
// Aucun secret en dur : valeurs sensibles via @secure() / Key Vault refs.
// =============================================================================

targetScope = 'resourceGroup'

// --- Paramètres généraux -----------------------------------------------------
@description('Région de déploiement (RGPD : France Central).')
param location string = 'francecentral'

@description('Préfixe de nommage commun à toutes les ressources.')
param namePrefix string = 'onix'

@description('Tags appliqués à toutes les ressources.')
param tags object = {
  application: 'onix'
  environment: 'prod'
  managedBy: 'bicep'
}

// --- Noms de ressources (overridables) ---------------------------------------
@description('Nom du registre ACR (globalement unique, alphanumérique).')
param acrName string = '${namePrefix}acr'

@description('Nom du cluster AKS.')
param aksName string = '${namePrefix}-aks'

@description('Nom du serveur PostgreSQL Flexible.')
param postgresName string = '${namePrefix}-pg'

@description('Nom de l\'instance Azure Cache for Redis.')
param redisName string = '${namePrefix}-redis'

@description('Nom du Key Vault (globalement unique, 3-24 car.).')
param keyVaultName string = '${namePrefix}-kv'

@description('Nom de l\'identité workload (user-assigned) de l\'app onix.')
param workloadIdentityName string = '${namePrefix}-workload'

// --- Toggle GPU (DÉCISION ACTÉE : OFF par défaut, Ollama CPU au départ) -------
@description('Activer le pool de nœuds GPU (NVIDIA T4). Par défaut OFF (Ollama CPU).')
param gpuEnabled bool = false

// --- Workload identity (federation OIDC -> ServiceAccount K8s) ----------------
@description('Namespace Kubernetes de la release onix.')
param k8sNamespace string = 'onix'

@description('ServiceAccount Kubernetes fédéré à l\'identité workload.')
param k8sServiceAccount string = 'onix'

// --- Secret Postgres ---------------------------------------------------------
@description('Mot de passe administrateur PostgreSQL (secret — via Key Vault ref dans .bicepparam).')
@secure()
param postgresAdminPassword string

// =============================================================================
// 1) Réseau privé
// =============================================================================
module network 'modules/network.bicep' = {
  name: 'network'
  params: {
    location: location
    namePrefix: namePrefix
    tags: tags
  }
}

// =============================================================================
// 2) Monitoring (Log Analytics + Azure Monitor Workspace + Grafana)
// =============================================================================
module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    location: location
    namePrefix: namePrefix
    tags: tags
  }
}

// =============================================================================
// 3) ACR (registre + cache pull-through). L'AcrPull est posé après l'AKS.
// =============================================================================
module acr 'modules/acr.bicep' = {
  name: 'acr'
  params: {
    location: location
    acrName: acrName
    tags: tags
    kubeletObjectId: aks.outputs.kubeletObjectId
  }
}

// =============================================================================
// 4) Identité workload (user-assigned) + fédération OIDC vers le SA K8s
// =============================================================================
resource workloadIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: workloadIdentityName
  location: location
  tags: tags
}

// Federated credential : lie le ServiceAccount K8s (issuer OIDC AKS) à l'UAMI.
resource federatedCredential 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: workloadIdentity
  name: '${namePrefix}-federation'
  properties: {
    issuer: aks.outputs.oidcIssuerUrl
    subject: 'system:serviceaccount:${k8sNamespace}:${k8sServiceAccount}'
    audiences: [
      'api://AzureADTokenExchange'
    ]
  }
}

// =============================================================================
// 5) AKS privé (system + CPU app pool + GPU optionnel)
// =============================================================================
module aks 'modules/aks.bicep' = {
  name: 'aks'
  params: {
    location: location
    aksName: aksName
    dnsPrefix: aksName
    tags: tags
    aksSubnetId: network.outputs.aksSubnetId
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsId
    enableManagedPrometheus: true
    gpuEnabled: gpuEnabled
  }
}

// =============================================================================
// 6) PostgreSQL Flexible MANAGÉ (HA ZoneRedundant, privé) + base onyx
// =============================================================================
module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    location: location
    serverName: postgresName
    tags: tags
    administratorPassword: postgresAdminPassword
    delegatedSubnetId: network.outputs.pgSubnetId
    privateDnsZoneId: network.outputs.dnsPostgresId
  }
}

// =============================================================================
// 7) Azure Cache for Redis Premium MANAGÉ (noeviction, TLS, privé)
// =============================================================================
module redis 'modules/redis.bicep' = {
  name: 'redis'
  params: {
    location: location
    redisName: redisName
    tags: tags
    privateEndpointSubnetId: network.outputs.peSubnetId
    privateDnsZoneId: network.outputs.dnsRedisId
  }
}

// =============================================================================
// 8) Key Vault (RBAC) + accès secrets à l'identité workload + endpoint privé
// =============================================================================
module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    location: location
    keyVaultName: keyVaultName
    tags: tags
    workloadIdentityPrincipalId: workloadIdentity.properties.principalId
    privateEndpointSubnetId: network.outputs.peSubnetId
    privateDnsZoneId: network.outputs.dnsVaultId
  }
}

// =============================================================================
// Sorties (consommées par deploy.sh / values-azure.yaml / setup-entra.sh)
// =============================================================================
output acrLoginServer string = acr.outputs.acrLoginServer
output aksClusterName string = aks.outputs.aksName
output aksOidcIssuerUrl string = aks.outputs.oidcIssuerUrl
output postgresFqdn string = postgres.outputs.postgresFqdn
output redisHostName string = redis.outputs.redisHostName
output redisSslPort int = redis.outputs.redisSslPort
output keyVaultName string = keyVault.outputs.keyVaultName
output keyVaultUri string = keyVault.outputs.keyVaultUri
output workloadIdentityClientId string = workloadIdentity.properties.clientId
output gpuEnabled bool = gpuEnabled
