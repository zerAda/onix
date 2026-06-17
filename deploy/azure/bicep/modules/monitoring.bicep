// =============================================================================
// monitoring.bicep — Observabilité onix
//
//   - Log Analytics Workspace  : logs conteneurs (addon Container Insights AKS).
//   - Azure Monitor Workspace  : métriques Prometheus MANAGÉ (addon metrics AKS).
//   - Managed Grafana          : dashboards (lecture de l'Azure Monitor Workspace).
//
// Cf. DEPLOY_AZURE.md P5 (Observabilité) : Managed Prometheus scrape Onyx +
// /metrics gateway + /metrics actions ; Managed Grafana pour la visualisation.
//
// Le branchement AKS -> ces ressources (azureMonitorProfile.metrics +
// addon Container Insights) se fait dans aks.bicep via les ids exportés ici.
// =============================================================================

@description('Région de déploiement.')
param location string

@description('Préfixe de nommage commun (ex: onix).')
param namePrefix string

@description('Tags appliqués aux ressources.')
param tags object = {}

@description('Déployer Managed Grafana (peut être désactivé si Grafana externe).')
param deployGrafana bool = true

@description('Rétention des logs (jours).')
param logRetentionDays int = 30

// --- Log Analytics (logs / Container Insights) -------------------------------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-law'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: logRetentionDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

// --- Azure Monitor Workspace (Prometheus managé) -----------------------------
resource monitorWorkspace 'Microsoft.Monitor/accounts@2023-04-03' = {
  name: '${namePrefix}-amw'
  location: location
  tags: tags
}

// --- Managed Grafana ---------------------------------------------------------
// Identité managée + lecture de l'Azure Monitor Workspace (rôle data reader).
resource grafana 'Microsoft.Dashboard/grafana@2023-09-01' = if (deployGrafana) {
  name: '${namePrefix}-grafana'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publicNetworkAccess: 'Enabled'
    grafanaIntegrations: {
      azureMonitorWorkspaceIntegrations: [
        {
          azureMonitorWorkspaceResourceId: monitorWorkspace.id
        }
      ]
    }
  }
}

// Rôle « Monitoring Data Reader » sur l'Azure Monitor Workspace pour Grafana.
var monitoringDataReaderRoleId = 'b0d8363b-8ddd-447d-831f-62ca05bff136'

resource grafanaReaderAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployGrafana) {
  name: guid(monitorWorkspace.id, '${namePrefix}-grafana', monitoringDataReaderRoleId)
  scope: monitorWorkspace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringDataReaderRoleId)
    // grafana est déployé conditionnellement (même condition) -> assertion non-null.
    principalId: grafana!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// --- Sorties -----------------------------------------------------------------
output logAnalyticsId string = logAnalytics.id
output logAnalyticsName string = logAnalytics.name
output monitorWorkspaceId string = monitorWorkspace.id
output grafanaName string = deployGrafana ? grafana.name : ''
