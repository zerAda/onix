# Déploiement onix (Onyx + Ollama + couche onix) sur Azure / AKS

> **Décisions actées** : Ollama **CPU** au départ (GPU ajouté plus tard) · RBAC =
> **access-gateway FOSS** (perm-sync EE non retenue) · Postgres + Redis **managés
> Azure** · OpenSearch + MinIO **in-cluster** · région **France Central** (RGPD).
>
> Conçu pour **corriger les manques pointés par l'audit Onyx** (`docs/audit-onyx/`) :
> SPOF data-tier → managé HA ; secrets en clair → `ENCRYPTION_KEY_SECRET` + Key Vault ;
> RBAC par-doc → gateway ; audit-trail absent → `onix-actions` (HMAC) ; télémétrie ON
> → coupée ; course migration → Job alembic ; `/health` non fiable → vraies probes.
>
> ⚠️ **Honnêteté** : l'IaC ci-dessous (commandes `az`/`helm`) est **exécutable sur
> votre poste az-connecté** mais **n'a PAS pu être validée dans le sandbox d'audit**
> (ni `az` ni cluster). Faites un `--what-if` / `helm template` avant prod.

## 0. Prérequis
`az login` (rôle *Owner* sur l'abonnement + *Application Administrator* pour le
consentement Entra), `kubectl`, `helm` ≥ 3.12. Variables :
```bash
RG=onix-rg; LOC=francecentral; ACR=onixacr; AKS=onix-aks; KV=onix-kv
PG=onix-pg; REDIS=onix-redis; NS=onix; DOMAIN=onix.gerep.example
az group create -n $RG -l $LOC
```

## P0 — Landing zone (réseau privé, ACR, AKS CPU, Key Vault, monitoring)
```bash
# ACR + cache pull-through Docker Hub (les images onyxdotapp restent inchangées)
az acr create -g $RG -n $ACR --sku Standard
az acr cache create -r $ACR -n onyx -s docker.io/onyxdotapp/* -t onyxdotapp/*

# AKS privé, OIDC + Workload Identity, addon Key Vault CSI + Managed Prometheus
az aks create -g $RG -n $AKS -l $LOC \
  --enable-private-cluster --network-plugin azure \
  --node-count 3 --node-vm-size Standard_E8s_v5 \   # E-series : RAM pour Ollama CPU + OpenSearch
  --enable-oidc-issuer --enable-workload-identity \
  --enable-addons azure-keyvault-secrets-provider,monitoring \
  --attach-acr $ACR --zones 1 2 3
az aks get-credentials -g $RG -n $AKS
kubectl create ns $NS

# Key Vault (CMK + secrets applicatifs)
az keyvault create -g $RG -n $KV -l $LOC --enable-rbac-authorization true
# App Routing (ingress managé) + cert-manager (TLS Let's Encrypt) — ou ingress-nginx
az aks approuting enable -g $RG -n $AKS
```
> **GPU plus tard** : `az aks nodepool add -g $RG --cluster-name $AKS -n gpu --node-vm-size Standard_NC8as_T4_v3 --node-count 0 --enable-cluster-autoscaler --min-count 0 --max-count 2 --node-taints sku=gpu:NoSchedule` (ou **Bicep `gpuEnabled=true`**) puis installer le **NVIDIA GPU Operator** et router Ollama via le chart : `--set ollama.gpu.enabled=true` (+ `nodeSelector`/`tolerations` vers le pool gpu).

## P1 — Data tier MANAGÉ (corrige les SPOF de l'audit)
```bash
# PostgreSQL Flexible — HA zone-redondant, privé, SSL requis
az postgres flexible-server create -g $RG -n $PG -l $LOC \
  --tier GeneralPurpose --sku-name Standard_D4s_v3 --version 16 \
  --high-availability ZoneRedundant --storage-size 128 \
  --vnet $AKS-vnet --subnet pg-subnet   # endpoint privé (pas d'accès public)
az postgres flexible-server db create -g $RG -s $PG -d onyx

# Azure Cache for Redis — Premium (persistance + zones), TLS 6380 par défaut
az redis create -g $RG -n $REDIS -l $LOC --sku Premium --vm-size P1 \
  --redis-configuration '{"maxmemory-policy":"noeviction"}'   # broker/locks : PAS d'eviction
```
> **Gotcha Redis (important)** : Azure Cache impose **TLS sur le port 6380** (le port
> non-TLS 6379 est désactivé). Côté **Onyx (base 0)**, `values-azure.yaml` câble
> désormais `redis.ssl: true` + `redis.port: "6380"` → la ConfigMap rend `REDIS_SSL=true`
> et `REDIS_PORT=6380` (Onyx lit ces variables ; cf. audit `app_configs.py REDIS_SSL`).
> Le mot de passe vient du Secret (`REDIS_PASSWORD`). Côté **passerelle (base 1)**, l'URL
> `rediss://:<clé>@$REDIS.redis.cache.windows.net:6380/1` est dans le Secret. Mettez
> `noeviction` sur l'instance Azure (le défaut LRU casserait le broker/locks).
> **Gotcha Postgres** : `sslmode=require` (Azure l'exige côté serveur). `values-azure.yaml`
> pose `postgresql.port: "5432"` + `postgresql.sslmode: "require"` → ConfigMap
> `POSTGRES_PORT`/`POSTGRES_SSLMODE` (passthrough psycopg/libpq) ; `POSTGRES_PASSWORD`
> dans le Secret. In-cluster (CNPG) : ces clés restent vides (non rendues).

## P1bis — Entra ID (SSO + Graph groupes + SharePoint)
Sur **votre poste** : `TENANT_ID=<…> KEYVAULT=$KV DOMAIN=$DOMAIN bash deploy/azure/setup-entra.sh`
→ crée `onix-sso`, `onix-graph-groups` (GroupMember.Read.All), `onix-sharepoint`
(**Sites.Read.All**, indexation FOSS) + pousse les secrets dans Key Vault.

## P2 — Secrets (Key Vault → K8s) + images
```bash
# Secrets applicatifs aléatoires dans Key Vault (générés localement)
ENV_FILE=deploy/prod/.env.prod bash scripts/gen-secrets.sh   # SECRET, USER_AUTH_SECRET, ENCRYPTION_KEY_SECRET, mdp…
# …puis pousser chaque clé : az keyvault secret set --vault-name $KV --name SECRET --value …
# CRITIQUE (audit) : ENCRYPTION_KEY_SECRET est désormais GÉNÉRÉ par gen-secrets.sh ET
# lu par le chart (clé `ENCRYPTION_KEY_SECRET` du Secret `onix-secrets` → injectée sur
# api/background/migrations). Sinon les creds connecteurs Onyx sont EN CLAIR en base.
# Mapper la clé Key Vault `ENCRYPTION-KEY-SECRET` → clé K8s `ENCRYPTION_KEY_SECRET` (SPC) :
az keyvault secret set --vault-name $KV -n ENCRYPTION-KEY-SECRET --value "$(openssl rand -hex 32)"

# Exposer les secrets au cluster via SecretProviderClass (Key Vault CSI + Workload Identity)
# → produit les Secret K8s `onix-secrets` et `onix-gateway-secrets` (cf. exemple SPC plus bas).

# Images custom → ACR
az acr build -r $ACR -t onix-actions:prod        actions/
az acr build -r $ACR -t onix-access-gateway:prod access-gateway/
```

## P3 — Déploiement de la stack
```bash
# Éditer deploy/azure/values-azure.yaml : <ACR>, <PG_FQDN>, <REDIS_FQDN>, <DOMAIN>, <RELEASE>
helm dependency build deploy/k8s/onix-ha
helm install $NS deploy/k8s/onix-ha -n $NS -f deploy/azure/values-azure.yaml

# La passerelle RBAC est un TEMPLATE NATIF du chart (accessGateway.enabled=true dans
# values-azure.yaml) → déployée par le `helm install` ci-dessus. Rien d'autre à appliquer.
```
**Ingress + OIDC (anti-spoofing)** — état réel du chart vs ce qui reste à câbler :

- ✅ **Route chat → passerelle (templatisée, OPT-IN)** : le chart sait router la route
  EXACTE `/api/chat/send-message` vers `<RELEASE>-access-gateway:8200` (le reste → Onyx
  natif) via `ingress.chatViaGateway.enabled=true` (rendu seulement si
  `accessGateway.enabled`). **Validé statiquement** (`helm template` : route Exact
  prioritaire sur `/api`) ; **comportement runtime à vérifier sur AKS**.
- 🚧 **TODO recette (NON livré par le chart, à câbler par l'opérateur)** : le
  **forward-auth oauth2-proxy** et l'**anti-usurpation** (`strip X-OIDC-Claims` entrant
  + (re)pose depuis l'identité vérifiée) — schéma de `deploy/prod/` (Caddy/nginx).
  Raisons : (1) oauth2-proxy **n'est pas un template** de ce chart ; il faut le déployer
  hors-chart ; (2) le strip d'en-tête exige un **snippet propre au contrôleur**
  (ex. ingress-nginx `configuration-snippet` / `more_clear_input_headers`), souvent
  **désactivé par défaut** (`allow-snippet-annotations=false`). Fournir ces
  comportements via `ingress.chatViaGateway.annotations` (auth-url/auth-signin +
  snippet). **Tant que ce n'est pas fait, n'activez PAS `chatViaGateway` en prod
  régulée** : le chat traverserait la passerelle SANS identité vérifiée.

> Alternative pleinement câblée aujourd'hui : le **mono-nœud exposé `deploy/prod/`**
> (Caddy + oauth2-proxy + nginx) réalise déjà forward-auth + anti-usurpation de bout
> en bout. L'équivalent AKS « tout-en-annotations » dépend du contrôleur d'ingress.

## P4 — Intégration RAG
1. **Ollama** : `kubectl exec` → `ollama pull qwen2.5:7b-instruct` (+ `nomic-embed-text`),
   ou `make models` adapté. `contextLength=8192` (CPU 7b).
2. **Onyx LLM** : Admin → LLM → provider **Ollama** (`ollama_chat`), base `http://<RELEASE>-ollama:11434`.
3. **Qualité FR** : appliquer `docs/PLAYBOOK_ONYX_RAG.md` (embedder `multilingual-e5-large`,
   reranker, analyseur `french`, `MAX_CHUNKS_FED_TO_CHAT=8`) — **un seul ré-index**.
4. **SharePoint** : Admin → Connectors → SharePoint, creds `sharepoint-*`, site
   `https://gerep75008.sharepoint.com/sites/dev-assistant-client-360`. (Indexation FOSS :
   **les ACL SharePoint ne sont PAS répliquées** — d'où la gateway.)
5. **RBAC par-doc** : `make sync-doc-acl` (Graph→`doc_acl.json`) en **CronJob**, monté
   en ConfigMap `onix-doc-acl` pour la passerelle (filtre de **sortie** par utilisateur).

## P5 — Conformité / exploitation
- **Télémétrie OFF** (déjà dans `values-azure.yaml` : `disableTelemetry: true`).
- **Chiffrement** : disques AKS + Postgres + Blob avec **CMK** (Key Vault) ; `ENCRYPTION_KEY_SECRET` posé (P2).
- **Audit / erasure / rétention** : endpoints `onix-actions` (`/admin/retention/purge`, journal HMAC) — l'audit-trail qu'Onyx n'a pas.
- **Observabilité** : Managed Prometheus scrape Onyx + `/metrics` passerelle + `/metrics` actions ; Managed Grafana.
- **Backup/DR** : Postgres Flexible PITR (managé) ; snapshots OpenSearch + miroir MinIO → **Azure Blob** (CronJobs du chart) ; Velero pour l'état cluster.
- **Gate qualité** : `make rag-eval-ci` (nightly RAGAS) contre l'Ollama du cluster.

## Gotchas à retenir
| Sujet | Piège | Solution |
|---|---|---|
| Redis | Azure Cache = **TLS 6380** + `noeviction` | URL `rediss://…:6380`, policy `noeviction` |
| Postgres | `sslmode=require` obligatoire | psycopg OK ; vérifier la chaîne SSL |
| Secrets | `ENCRYPTION_KEY_SECRET` vide = creds **en clair** (audit) | le poser (P2) — fail-loud sinon |
| Images | rate-limit Docker Hub | **ACR pull-through cache** (P0) |
| RBAC | perm-sync = **EE + certificat** (non retenue) | **gateway doc-ACL** (filtre de sortie ; pas le trimming à la récup) |
| Ollama CPU | ~5,8 tok/s, ~1-3 users | **cache RBAC-safe + sémantique** (latence/coût ↓) ; GPU pool quand le débit l'exige |
| Migration | course alembic multi-réplica | `api.runMigrationsJob: true` (Job pre-install) |

## Coût indicatif (UE, /mois)
AKS 3× E8s_v5 (~600-900 $) + Postgres Flexible HA D4s (~300-450 $) + Redis Premium P1
(~250-350 $) + stockage/réseau/monitoring (~200-400 $) ≈ **1,5-2,5 k$/mo** (CPU).
+ node GPU T4 (~400-900 $) quand activé. Pas de licence EE (RBAC = gateway FOSS).

## Limites honnêtes
- IaC `az`/`helm` **non validée dans le sandbox** (pas d'`az`/cluster ici) → `--what-if` + `helm template` avant prod.
- Passerelle = **template natif du chart** (`accessGateway.enabled=true`) ; pool **GPU activable** (`ollama.gpu.enabled` côté chart + `gpuEnabled` côté Bicep).
- RBAC FOSS = **filtre de sortie** (le LLM a vu le périmètre indexé) ; zéro-fuite strict = Onyx EE perm-sync ou instances par tier.
- **IaC Bicep** repeatable : `deploy/azure/bicep/` (modulaire, **`bicep build` propre — 0 erreur/0 warning**), alternative au runbook `az`. Terraform : sur demande. Seul un `az deployment group --what-if` réel confirme quotas/SKU/unicité sur votre tenant.
