// =============================================================================
// aks.bicep — AKS PRIVÉ pour onix (France Central)
//
// Reproduit DEPLOY_AZURE.md P0 :
//   - Cluster PRIVÉ (apiServerAccessProfile.enablePrivateCluster).
//   - Pool système (mode System).
//   - Pool applicatif CPU : Standard_E8s_v5 (RAM pour Ollama CPU + OpenSearch),
//     zones 1-3, autoscale. E-series = mémoire (cf. runbook).
//   - Pool GPU OPTIONNEL (param gpuEnabled) : Standard_NC8as_T4_v3, taint
//     sku=gpu:NoSchedule, autoscale min 0 -> n'allume RIEN tant qu'inutilisé.
//   - OIDC issuer + Workload Identity (securityProfile.workloadIdentity).
//   - Addons : azureKeyvaultSecretsProvider (CSI Key Vault) +
//     azureMonitorProfile.metrics (Prometheus managé) + Container Insights.
//   - AcrPull au kubelet identity (attach ACR).
//
// Réseau : network-plugin azure, sous-réseau aks dédié (VNet privé du module network).
// =============================================================================

@description('Région de déploiement.')
param location string

@description('Nom du cluster AKS (ex: onix-aks).')
param aksName string

@description('Préfixe DNS du cluster.')
param dnsPrefix string = aksName

@description('Tags appliqués aux ressources.')
param tags object = {}

@description('Version Kubernetes (vide = défaut de la région).')
param kubernetesVersion string = ''

@description(' resourceId du sous-réseau AKS (module network).')
param aksSubnetId string

@description('resourceId du Log Analytics Workspace (Container Insights).')
param logAnalyticsWorkspaceId string

@description('Activer azureMonitorProfile.metrics (Prometheus managé).')
param enableManagedPrometheus bool = true

// --- Pool système ------------------------------------------------------------
@description('Taille VM du pool système.')
param systemVmSize string = 'Standard_D4s_v5'

@description('Nombre de nœuds système (autoscale min).')
param systemMinCount int = 2

@description('Nombre de nœuds système (autoscale max).')
param systemMaxCount int = 3

// --- Pool applicatif CPU -----------------------------------------------------
@description('Taille VM du pool applicatif CPU (RAM pour Ollama CPU + OpenSearch).')
param appVmSize string = 'Standard_E8s_v5'

@description('Pool applicatif : autoscale min.')
param appMinCount int = 3

@description('Pool applicatif : autoscale max.')
param appMaxCount int = 6

// --- Pool GPU (OPTIONNEL, désactivé par défaut) ------------------------------
@description('Activer le pool GPU (par défaut OFF — Ollama CPU au départ).')
param gpuEnabled bool = false

@description('Taille VM du pool GPU (T4).')
param gpuVmSize string = 'Standard_NC8as_T4_v3'

@description('Pool GPU : autoscale min (0 = aucun nœud allumé tant qu\'inutilisé).')
param gpuMinCount int = 0

@description('Pool GPU : autoscale max.')
param gpuMaxCount int = 2

resource aks 'Microsoft.ContainerService/managedClusters@2024-09-01' = {
  name: aksName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    kubernetesVersion: empty(kubernetesVersion) ? null : kubernetesVersion
    dnsPrefix: dnsPrefix
    enableRBAC: true
    disableLocalAccounts: true // pas de kubeconfig admin local : Entra/RBAC uniquement

    // --- Cluster PRIVÉ -------------------------------------------------------
    apiServerAccessProfile: {
      enablePrivateCluster: true
      privateDNSZone: 'system'
    }

    // --- Réseau (azure CNI + zones DNS privées du VNet) ----------------------
    networkProfile: {
      networkPlugin: 'azure'
      networkPolicy: 'azure'
      loadBalancerSku: 'standard'
      outboundType: 'loadBalancer'
    }

    // --- OIDC + Workload Identity (pour Key Vault CSI / Entra) ----------------
    oidcIssuerProfile: {
      enabled: true
    }
    securityProfile: {
      workloadIdentity: {
        enabled: true
      }
    }

    // --- Addons : Key Vault CSI + Container Insights --------------------------
    addonProfiles: {
      azureKeyvaultSecretsProvider: {
        enabled: true
        config: {
          enableSecretRotation: 'true'
        }
      }
      omsagent: {
        enabled: true
        config: {
          logAnalyticsWorkspaceResourceID: logAnalyticsWorkspaceId
        }
      }
    }

    // --- Prometheus managé (métriques) ---------------------------------------
    azureMonitorProfile: {
      metrics: {
        enabled: enableManagedPrometheus
      }
    }

    // --- Pools de nœuds ------------------------------------------------------
    agentPoolProfiles: [
      {
        // Pool système : services AKS / CoreDNS / etc.
        name: 'system'
        mode: 'System'
        osType: 'Linux'
        osSKU: 'Ubuntu'
        vmSize: systemVmSize
        count: systemMinCount
        minCount: systemMinCount
        maxCount: systemMaxCount
        enableAutoScaling: true
        vnetSubnetID: aksSubnetId
        availabilityZones: [
          '1'
          '2'
          '3'
        ]
        type: 'VirtualMachineScaleSets'
        osDiskType: 'Managed'
      }
      {
        // Pool applicatif CPU : Onyx + Ollama CPU + OpenSearch + MinIO.
        name: 'app'
        mode: 'User'
        osType: 'Linux'
        osSKU: 'Ubuntu'
        vmSize: appVmSize
        count: appMinCount
        minCount: appMinCount
        maxCount: appMaxCount
        enableAutoScaling: true
        vnetSubnetID: aksSubnetId
        availabilityZones: [
          '1'
          '2'
          '3'
        ]
        type: 'VirtualMachineScaleSets'
        osDiskType: 'Managed'
      }
    ]
  }
}

// --- Pool GPU OPTIONNEL (ressource séparée pour le toggle min 0) --------------
// taint sku=gpu:NoSchedule => seuls les pods Ollama (toleration + nodeSelector)
// y atterrissent. min 0 => aucun coût GPU tant qu'aucun pod GPU n'est planifié.
resource gpuPool 'Microsoft.ContainerService/managedClusters/agentPools@2024-09-01' = if (gpuEnabled) {
  parent: aks
  name: 'gpu'
  properties: {
    mode: 'User'
    osType: 'Linux'
    osSKU: 'Ubuntu'
    vmSize: gpuVmSize
    count: gpuMinCount
    minCount: gpuMinCount
    maxCount: gpuMaxCount
    enableAutoScaling: true
    vnetSubnetID: aksSubnetId
    availabilityZones: [
      '1'
      '2'
      '3'
    ]
    type: 'VirtualMachineScaleSets'
    osDiskType: 'Managed'
    nodeTaints: [
      'sku=gpu:NoSchedule'
    ]
    nodeLabels: {
      'onix.io/gpu': 'true'
    }
  }
}

// NB : l'assignation AcrPull (kubelet identity -> ACR) est faite dans acr.bicep
// (rôle scopé sur l'ACR, pas sur le cluster), à partir de kubeletObjectId exporté.

// --- Sorties -----------------------------------------------------------------
output aksId string = aks.id
output aksName string = aks.name
output oidcIssuerUrl string = aks.properties.oidcIssuerProfile.issuerURL
output kubeletObjectId string = aks.properties.identityProfile.kubeletidentity.objectId
output clusterIdentityPrincipalId string = aks.identity.principalId
output nodeResourceGroup string = aks.properties.nodeResourceGroup
