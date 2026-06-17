// =============================================================================
// acr.bicep — Azure Container Registry (Standard) + cache pull-through
//
// Sert deux besoins (cf. DEPLOY_AZURE.md P0) :
//   1) Cache pull-through de Docker Hub onyxdotapp/* — évite le rate-limit DH,
//      les images amont Onyx restent INCHANGÉES.
//   2) Héberge les images custom (onix-actions, onix-access-gateway) buildées
//      via `az acr build`.
//
// L'attachement AKS->ACR (AcrPull au kubelet identity) est fait dans aks.bicep
// à partir de l'id exporté ici.
// =============================================================================

@description('Région de déploiement.')
param location string

@description('Nom du registre ACR (alphanumérique, globalement unique, ex: onixacr).')
param acrName string

@description('Tags appliqués aux ressources.')
param tags object = {}

@description('objectId du kubelet identity AKS à autoriser en pull (AcrPull). Vide = pas d\'attach.')
param kubeletObjectId string = ''

// Rôle intégré AcrPull (pull d'images par le kubelet identity AKS).
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    // Standard : suffisant pour la prod onix ; Premium requis seulement pour
    // geo-réplication / endpoint privé (non retenu ici, AKS attaché en VNet).
    name: 'Standard'
  }
  properties: {
    adminUserEnabled: false // pas d'admin user : on s'appuie sur AcrPull (managed identity)
    publicNetworkAccess: 'Enabled'
    zoneRedundancy: 'Disabled'
  }
}

// --- Cache pull-through Docker Hub : docker.io/onyxdotapp/* -> onyxdotapp/* ----
// NB : un credential set (PAT Docker Hub en Key Vault) peut être ajouté pour
// l'authentification anonyme étendue ; ici on reste sur le pull anonyme caché.
resource cacheRule 'Microsoft.ContainerRegistry/registries/cacheRules@2023-11-01-preview' = {
  parent: acr
  // Nom du cache rule (>= 5 car.) ; n'affecte pas le mapping source/cible.
  name: 'onyxhub'
  properties: {
    sourceRepository: 'docker.io/onyxdotapp/*'
    targetRepository: 'onyxdotapp/*'
  }
}

// --- AcrPull au kubelet identity AKS (attach ACR, scopé sur l'ACR) -----------
// => pas d'imagePullSecret côté chart (cf. values-azure.yaml imagePullSecrets: []).
resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(kubeletObjectId)) {
  name: guid(acr.id, kubeletObjectId, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: kubeletObjectId
    principalType: 'ServicePrincipal'
  }
}

// --- Sorties -----------------------------------------------------------------
output acrId string = acr.id
output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
