# deploy/azure — onix sur Azure / AKS

Déploiement de la stack onix (Onyx + Ollama + access-gateway + onix-actions) sur
**AKS**, région **France Central**, avec **Postgres + Redis managés**, OpenSearch +
MinIO in-cluster, **Ollama CPU** (GPU ajoutable), **RBAC via access-gateway FOSS**.

| Fichier | Rôle |
|---|---|
| [`../../docs/DEPLOY_AZURE.md`](../../docs/DEPLOY_AZURE.md) | **Runbook** pas-à-pas (az + helm), gotchas, coûts, limites |
| `values-azure.yaml` | Overrides Helm pour `onix-ha` (data-tier managé, Ollama CPU, ACR, télémétrie off, **passerelle native `accessGateway.enabled=true`**) |
| `bicep/` | **IaC Bicep** modulaire (validée `bicep build`) : réseau + AKS (GPU optionnel) + Postgres/Redis managés + Key Vault + monitoring |
| `setup-entra.sh` | Crée les apps Entra (SSO + Graph groupes + SharePoint) + secrets Key Vault — **à lancer sur votre poste az** |

> La passerelle RBAC est désormais un **template natif du chart** (`accessGateway.enabled`),
> plus de manifeste autonome. Le Bicep **compile proprement** (`bicep build` 0 erreur) ;
> seul un `az deployment group --what-if` réel confirme quotas/SKU/unicité sur le tenant.
