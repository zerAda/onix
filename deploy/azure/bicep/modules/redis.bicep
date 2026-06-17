// =============================================================================
// redis.bicep — Azure Cache for Redis Premium (MANAGÉ) + endpoint privé
//
// Reproduit DEPLOY_AZURE.md P1 (corrige le SPOF Redis de l'audit) :
//   - SKU Premium (persistance + zones).
//   - maxmemory-policy = noeviction  ⚠ CRITIQUE : le défaut LRU casserait le
//     broker/locks Onyx (cf. gotcha runbook).
//   - TLS uniquement (port SSL 6380) : enableNonSslPort=false, TLS >= 1.2.
//     Côté app : URL rediss://...:6380 (schéma rediss:// = TLS).
//   - Accès PRIVÉ via private endpoint (publicNetworkAccess=Disabled).
//
// La clé d'accès Redis n'est PAS exposée par l'IaC : à pousser dans Key Vault
// (az redis list-keys) puis montée via le CSI driver.
// =============================================================================

@description('Région de déploiement.')
param location string

@description('Nom de l\'instance Redis (ex: onix-redis).')
param redisName string

@description('Tags appliqués aux ressources.')
param tags object = {}

@description('Capacité Premium (1 = P1, 2 = P2, ...).')
param capacity int = 1

@description('resourceId du sous-réseau pour l\'endpoint privé (module network).')
param privateEndpointSubnetId string

@description('resourceId de la zone DNS privée Redis (module network).')
param privateDnsZoneId string

resource redis 'Microsoft.Cache/redis@2024-03-01' = {
  name: redisName
  location: location
  tags: tags
  // Zones de redondance (Premium) : alignées sur les zones AKS.
  zones: [
    '1'
    '2'
    '3'
  ]
  properties: {
    sku: {
      name: 'Premium'
      family: 'P'
      capacity: capacity
    }
    minimumTlsVersion: '1.2'
    enableNonSslPort: false // TLS only -> port 6380
    redisConfiguration: {
      // PAS d'éviction : broker/locks Onyx (cf. gotcha DEPLOY_AZURE.md).
      'maxmemory-policy': 'noeviction'
    }
    publicNetworkAccess: 'Disabled'
  }
}

// --- Endpoint privé ----------------------------------------------------------
resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: '${redisName}-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${redisName}-plsc'
        properties: {
          privateLinkServiceId: redis.id
          groupIds: [
            'redisCache'
          ]
        }
      }
    ]
  }
}

// --- Enregistrement A dans la zone DNS privée --------------------------------
resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'redis'
        properties: {
          privateDnsZoneId: privateDnsZoneId
        }
      }
    ]
  }
}

// --- Sorties -----------------------------------------------------------------
output redisId string = redis.id
output redisName string = redis.name
output redisHostName string = redis.properties.hostName
output redisSslPort int = redis.properties.sslPort
