// =============================================================================
// main.bicepparam — Paramètres de déploiement onix (France Central)
//
// AUCUN SECRET EN DUR : le mot de passe Postgres est lu depuis Key Vault via
// az.getSecret(...) au moment du déploiement (le secret doit préexister dans le
// Key Vault — cf. deploy.sh / DEPLOY_AZURE.md P2).
//
// Renseignez subscriptionId + le nom du RG/Key Vault « bootstrap » qui héberge
// POSTGRES-ADMIN-PASSWORD (peut être le Key Vault onix lui-même une fois créé,
// ou un vault d'amorçage). Adaptez les noms si conflit d'unicité globale (ACR/KV).
// =============================================================================

using './main.bicep'

// --- Région & nommage --------------------------------------------------------
param location = 'francecentral'
param namePrefix = 'onix'

// --- Toggle GPU (DÉCISION ACTÉE : OFF par défaut — Ollama CPU) ----------------
// Passez à true pour ajouter le pool NVIDIA T4 (taint sku=gpu:NoSchedule, min 0).
param gpuEnabled = false

// --- Noms de ressources (overridables si conflit d'unicité) ------------------
param acrName = 'onixacr'
param aksName = 'onix-aks'
param postgresName = 'onix-pg'
param redisName = 'onix-redis'
param keyVaultName = 'onix-kv'

// --- Fédération workload identity (ServiceAccount K8s onix) -------------------
param k8sNamespace = 'onix'
param k8sServiceAccount = 'onix'

// --- Secret Postgres : Key Vault ref (JAMAIS de mot de passe en clair) --------
// Remplacez <SUBSCRIPTION_ID> + <BOOTSTRAP_RG> + <BOOTSTRAP_KV> par vos valeurs.
// Le secret 'POSTGRES-ADMIN-PASSWORD' doit exister AVANT le déploiement :
//   az keyvault secret set --vault-name <BOOTSTRAP_KV> -n POSTGRES-ADMIN-PASSWORD \
//     --value "$(openssl rand -base64 32)"
param postgresAdminPassword = az.getSecret(
  '<SUBSCRIPTION_ID>',
  '<BOOTSTRAP_RG>',
  '<BOOTSTRAP_KV>',
  'POSTGRES-ADMIN-PASSWORD'
)
