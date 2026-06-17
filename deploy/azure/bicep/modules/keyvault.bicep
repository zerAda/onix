// =============================================================================
// keyvault.bicep — Key Vault (autorisation RBAC) + accès workload identity AKS
//
// Stocke les secrets applicatifs onix (SECRET, USER_AUTH_SECRET,
// ENCRYPTION_KEY_SECRET, mots de passe Postgres/Redis/OpenSearch, clés MinIO,
// secrets gateway/actions, secrets Entra). Cf. DEPLOY_AZURE.md P2.
//
// AUDIT : ENCRYPTION_KEY_SECRET DOIT être posé (sinon creds connecteurs en clair)
// — il est injecté hors-IaC (az keyvault secret set), JAMAIS en dur dans Bicep.
//
// Accès : autorisation RBAC (pas de policies). L'identité workload de l'app AKS
// reçoit le rôle « Key Vault Secrets User » pour lire les secrets via le CSI driver.
// =============================================================================

@description('Région de déploiement.')
param location string

@description('Nom du Key Vault (3-24 caractères, globalement unique, ex: onix-kv).')
param keyVaultName string

@description('Tags appliqués aux ressources.')
param tags object = {}

@description('Tenant Entra ID du Key Vault.')
param tenantId string = subscription().tenantId

@description('principalId (objectId) de l\'identité workload AKS à autoriser en lecture de secrets. Vide = pas de role assignment.')
param workloadIdentityPrincipalId string = ''

@description('Activer la protection contre la purge (recommandé en prod).')
param enablePurgeProtection bool = true

@description('resourceId du sous-réseau pour l\'endpoint privé. Vide = pas de PE (accès public).')
param privateEndpointSubnetId string = ''

@description('resourceId de la zone DNS privée Key Vault. Requis si PE activé.')
param privateDnsZoneId string = ''

// Rôle intégré « Key Vault Secrets User » (lecture des secrets via data plane).
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    // Autorisation RBAC (cf. runbook : --enable-rbac-authorization true).
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: enablePurgeProtection ? true : null
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

// --- Role assignment : workload identity AKS -> Key Vault Secrets User --------
resource secretsUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(workloadIdentityPrincipalId)) {
  name: guid(keyVault.id, workloadIdentityPrincipalId, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: workloadIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// --- Endpoint privé (optionnel) ----------------------------------------------
var enablePrivateEndpoint = !empty(privateEndpointSubnetId) && !empty(privateDnsZoneId)

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-01-01' = if (enablePrivateEndpoint) {
  name: '${keyVaultName}-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${keyVaultName}-plsc'
        properties: {
          privateLinkServiceId: keyVault.id
          groupIds: [
            'vault'
          ]
        }
      }
    ]
  }
}

resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = if (enablePrivateEndpoint) {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'vault'
        properties: {
          privateDnsZoneId: privateDnsZoneId
        }
      }
    ]
  }
}

// --- Sorties -----------------------------------------------------------------
output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
