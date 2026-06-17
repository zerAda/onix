// =============================================================================
// network.bicep — Réseau privé onix (France Central)
//
// Crée le VNet, les sous-réseaux (aks, pg, redis, pe), les NSG et les zones
// DNS privées (postgres / redis / vault / acr) nécessaires aux endpoints privés.
//
// Décisions actées : AKS privé, Postgres + Redis MANAGÉS en accès privé,
// OpenSearch + MinIO in-cluster. Aucun accès public sur le data-tier.
// =============================================================================

@description('Région de déploiement (toutes les ressources de ce module).')
param location string

@description('Préfixe de nommage commun (ex: onix).')
param namePrefix string

@description('Tags appliqués à toutes les ressources.')
param tags object = {}

@description('Espace d\'adressage du VNet.')
param vnetAddressSpace string = '10.40.0.0/16'

@description('Sous-réseau des nœuds AKS.')
param aksSubnetPrefix string = '10.40.0.0/20'

@description('Sous-réseau délégué à PostgreSQL Flexible.')
param pgSubnetPrefix string = '10.40.16.0/24'

@description('Sous-réseau pour l\'endpoint privé Redis.')
param redisSubnetPrefix string = '10.40.17.0/24'

@description('Sous-réseau pour les endpoints privés (Key Vault, ACR, etc.).')
param peSubnetPrefix string = '10.40.18.0/24'

// --- NSG AKS : règles minimales (le trafic intra-VNet reste autorisé) --------
resource nsgAks 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: '${namePrefix}-aks-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        // Autorise le LB interne / sondes Azure (App Routing en ingress interne).
        name: 'Allow-AzureLoadBalancer-In'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: 'Allow'
          protocol: '*'
          sourceAddressPrefix: 'AzureLoadBalancer'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}

// --- NSG pour les sous-réseaux de données (pg / redis / pe) -------------------
resource nsgData 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: '${namePrefix}-data-nsg'
  location: location
  tags: tags
  properties: {}
}

// --- VNet + sous-réseaux -----------------------------------------------------
resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: '${namePrefix}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressSpace]
    }
    subnets: [
      {
        name: 'aks-subnet'
        properties: {
          addressPrefix: aksSubnetPrefix
          networkSecurityGroup: { id: nsgAks.id }
        }
      }
      {
        // Sous-réseau DÉLÉGUÉ à PostgreSQL Flexible (VNet-injected).
        name: 'pg-subnet'
        properties: {
          addressPrefix: pgSubnetPrefix
          networkSecurityGroup: { id: nsgData.id }
          delegations: [
            {
              name: 'pg-delegation'
              properties: {
                serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers'
              }
            }
          ]
        }
      }
      {
        name: 'redis-subnet'
        properties: {
          addressPrefix: redisSubnetPrefix
          networkSecurityGroup: { id: nsgData.id }
        }
      }
      {
        // Endpoints privés (Key Vault, ACR, Redis). Policies désactivées pour PE.
        name: 'pe-subnet'
        properties: {
          addressPrefix: peSubnetPrefix
          networkSecurityGroup: { id: nsgData.id }
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

// --- Zones DNS privées (résolution des endpoints privés) ---------------------
// Une zone par service. Les enregistrements A sont créés via privateDnsZoneGroups
// des endpoints privés (Key Vault, ACR, Redis) ou par l'intégration VNet (Postgres).

resource dnsPostgres 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: '${namePrefix}-pg.private.postgres.database.azure.com'
  location: 'global'
  tags: tags
}

resource dnsRedis 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.redis.cache.windows.net'
  location: 'global'
  tags: tags
}

resource dnsVault 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
  tags: tags
}

resource dnsAcr 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'privatelink.azurecr.io'
  location: 'global'
  tags: tags
}

// --- Liens VNet <-> zones DNS privées ----------------------------------------
resource linkPostgres 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: dnsPostgres
  name: '${namePrefix}-pg-link'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnet.id }
  }
}

resource linkRedis 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: dnsRedis
  name: '${namePrefix}-redis-link'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnet.id }
  }
}

resource linkVault 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: dnsVault
  name: '${namePrefix}-vault-link'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnet.id }
  }
}

resource linkAcr 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: dnsAcr
  name: '${namePrefix}-acr-link'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnet.id }
  }
}

// --- Sorties (consommées par les autres modules) -----------------------------
output vnetId string = vnet.id
output vnetName string = vnet.name
output aksSubnetId string = '${vnet.id}/subnets/aks-subnet'
output pgSubnetId string = '${vnet.id}/subnets/pg-subnet'
output redisSubnetId string = '${vnet.id}/subnets/redis-subnet'
output peSubnetId string = '${vnet.id}/subnets/pe-subnet'

output dnsPostgresId string = dnsPostgres.id
output dnsRedisId string = dnsRedis.id
output dnsVaultId string = dnsVault.id
output dnsAcrId string = dnsAcr.id
