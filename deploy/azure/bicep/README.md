# deploy/azure/bicep — Landing zone onix en Bicep (AKS / France Central)

IaC **modulaire et validée** (`bicep build` + `bicep lint`, 0 erreur / 0 warning)
reproduisant la zone d'atterrissage du runbook [`../../../docs/DEPLOY_AZURE.md`](../../../docs/DEPLOY_AZURE.md)
(P0/P1). Le reste de la stack (Onyx, Ollama, OpenSearch, MinIO, gateway) est
déployé **par le chart Helm** avec [`../values-azure.yaml`](../values-azure.yaml).

> Décisions ACTÉES : Ollama **CPU** (pool GPU **optionnel, OFF par défaut**) ·
> Postgres + Redis **MANAGÉS** · OpenSearch + MinIO **in-cluster** ·
> région **France Central** (RGPD) · RBAC = **access-gateway FOSS**.

## Périmètre couvert par le Bicep
| Module | Ressource(s) |
|---|---|
| `modules/network.bicep` | VNet + subnets (aks / pg / redis / pe) + NSG + 4 zones DNS privées (postgres, redis, vault, acr) + liens VNet |
| `modules/acr.bicep` | ACR **Standard** + **cache pull-through** `docker.io/onyxdotapp/*` + **AcrPull** au kubelet identity |
| `modules/aks.bicep` | **AKS privé**, system pool + **CPU app pool** (E8s_v5, zones 1-3, autoscale), **GPU pool OPTIONNEL** (T4, taint `sku=gpu:NoSchedule`, min 0), OIDC + **Workload Identity**, addons **Key Vault CSI** + **Prometheus managé** + Container Insights |
| `modules/postgres.bicep` | PostgreSQL **Flexible** GeneralPurpose v16, **HA ZoneRedundant**, subnet délégué privé, base `onyx` |
| `modules/redis.bicep` | Azure Cache for Redis **Premium**, `maxmemory-policy=noeviction`, TLS-only, **private endpoint** |
| `modules/keyvault.bicep` | Key Vault **RBAC** + rôle **Key Vault Secrets User** à l'identité workload + private endpoint |
| `modules/monitoring.bicep` | Log Analytics + **Azure Monitor Workspace** (Prometheus) + **Managed Grafana** |
| `main.bicep` | Orchestrateur (RG pré-créé) + **UAMI workload** fédérée au ServiceAccount K8s |

## Paramètres clés (`main.bicepparam`)
- `location = 'francecentral'`
- **`gpuEnabled = false`** — toggle du pool GPU (passez à `true` pour ajouter le
  pool NVIDIA T4 ; min 0 ⇒ **aucun coût GPU** tant qu'aucun pod GPU n'est planifié).
- `acrName`, `aksName`, `postgresName`, `redisName`, `keyVaultName` — adaptables
  (ACR + Key Vault exigent une **unicité globale**).
- `postgresAdminPassword` — **jamais en clair** : `az.getSecret(...)` lit le secret
  `POSTGRES-ADMIN-PASSWORD` dans un Key Vault d'amorçage au déploiement.

> **Aucun secret en fichier.** Toutes les valeurs sensibles passent par
> `@secure()` + Key Vault. Les clés d'accès Redis / mots de passe app sont
> poussées dans le Key Vault hors-IaC (cf. runbook P2) et montées via le CSI driver.

## Déploiement
```bash
# Prérequis : az login (Owner), kubectl. Adaptez main.bicepparam (<SUBSCRIPTION_ID>, etc.).
./deploy.sh          # az group create + secret bootstrap + what-if + apply + post-steps
```
`deploy.sh` exécute un **`--what-if`** (revue obligatoire) avant tout apply.

## Validation (effectuée hors-ligne)
```bash
curl -fsSL -o /tmp/bicep https://github.com/Azure/bicep/releases/latest/download/bicep-linux-x64
chmod +x /tmp/bicep
/tmp/bicep build       deploy/azure/bicep/main.bicep        # 0 erreur / 0 warning
/tmp/bicep lint        deploy/azure/bicep/main.bicep        # 0 finding
/tmp/bicep build-params deploy/azure/bicep/main.bicepparam  # 0 erreur
```

## Limites honnêtes
- `bicep build`/`lint` valident la **syntaxe, les types et les api-versions** hors-ligne.
  Seul un **`az deployment group ... --what-if` sur votre tenant** confirme :
  quotas vCPU (E-series / T4), **unicité globale** ACR/Key Vault, disponibilité
  des SKU (Redis Premium zones, `Standard_NC8as_T4_v3`) en France Central, et le
  consentement RBAC (role assignments).
- Le **NVIDIA GPU Operator** (drivers) reste à installer dans le cluster quand
  `gpuEnabled=true` (hors périmètre Bicep — étape Helm/manifeste).
- `azureMonitorProfile.metrics=true` active Prometheus managé ; le câblage fin des
  **DCR/DCE de scraping** est géré par Azure à l'activation de l'addon.
- OpenSearch / MinIO / Onyx / Ollama / gateway = **chart Helm** (`values-azure.yaml`),
  hors de cette IaC d'infrastructure.
