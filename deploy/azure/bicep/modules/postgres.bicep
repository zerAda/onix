// =============================================================================
// postgres.bicep — Azure Database for PostgreSQL Flexible Server (MANAGÉ)
//
// Reproduit DEPLOY_AZURE.md P1 (corrige le SPOF data-tier de l'audit) :
//   - Tier GeneralPurpose, version 16.
//   - Haute dispo ZoneRedundant (bascule auto inter-zones).
//   - Réseau PRIVÉ : VNet-injected (sous-réseau délégué) + zone DNS privée.
//     => pas d'accès public. SSL requis (sslmode=require, défaut Azure).
//   - Base applicative `onyx`.
//
// Sécurité : le mot de passe admin est un @secure() param (jamais en dur).
// Recommandation : le tirer d'un secret Key Vault via main.bicepparam (getSecret).
// =============================================================================

@description('Région de déploiement.')
param location string

@description('Nom du serveur PostgreSQL Flexible (ex: onix-pg).')
param serverName string

@description('Tags appliqués aux ressources.')
param tags object = {}

@description('Identifiant administrateur PostgreSQL.')
param administratorLogin string = 'onixadmin'

@description('Mot de passe administrateur (secret — fournir via Key Vault ref).')
@secure()
param administratorPassword string

@description('SKU de calcul (GeneralPurpose).')
param skuName string = 'Standard_D4s_v3'

@description('Taille du stockage en Go.')
param storageSizeGB int = 128

@description('Version majeure PostgreSQL.')
param postgresVersion string = '16'

@description('resourceId du sous-réseau délégué à PostgreSQL (module network).')
param delegatedSubnetId string

@description('resourceId de la zone DNS privée Postgres (module network).')
param privateDnsZoneId string

@description('Rétention des sauvegardes (jours) — PITR managé.')
param backupRetentionDays int = 14

@description('Nom de la base de données applicative.')
param databaseName string = 'onyx'

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: 'GeneralPurpose'
  }
  properties: {
    version: postgresVersion
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorPassword
    storage: {
      storageSizeGB: storageSizeGB
      autoGrow: 'Enabled'
    }
    // HA zone-redondant : réplica synchrone dans une autre zone (anti-SPOF).
    highAvailability: {
      mode: 'ZoneRedundant'
    }
    // Accès PRIVÉ uniquement (VNet-injected) + résolution DNS privée.
    network: {
      delegatedSubnetResourceId: delegatedSubnetId
      privateDnsZoneArmResourceId: privateDnsZoneId
      publicNetworkAccess: 'Disabled'
    }
    backup: {
      backupRetentionDays: backupRetentionDays
      geoRedundantBackup: 'Disabled'
    }
    authConfig: {
      passwordAuth: 'Enabled'
      activeDirectoryAuth: 'Disabled'
    }
  }
}

// --- Base de données applicative `onyx` --------------------------------------
resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// --- Sorties -----------------------------------------------------------------
output postgresId string = postgres.id
output postgresName string = postgres.name
output postgresFqdn string = postgres.properties.fullyQualifiedDomainName
output databaseName string = database.name
