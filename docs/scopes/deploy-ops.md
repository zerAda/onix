# Scope `deploy-ops` — dossier agent

> **Mission** : **déployer et exploiter** onix de façon reproductible — du mono-poste
> (Docker Compose durci) à la **production machine unique** (overlay santé + systemd),
> la **prod exposée** (Caddy TLS + OIDC), la **HA Kubernetes** (chart Helm), et
> **Azure/AKS** (bicep IaC) — plus les **scripts** d'ops (secrets, hardware, backup…).
> **Sous-agent** : SRE / DevOps / IaC. **État** :
> [`../../ralph/state/deploy-ops.md`](../../ralph/state/deploy-ops.md).
>
> 👤 **Owner** : SRE / DevOps / IaC · 🗓️ **Dernière revue** : 2026-06-18 · 🔁 **Cadence de revue** : 120 j (cf. [registre](scopes.json)).

Routeur : [`README.md`](README.md) · Projet : [`../../AGENTS.md`](../../AGENTS.md).

## 1. Mission & frontière FOSS/EE

| | |
|---|---|
| **Apporte (FOSS)** | stack souveraine auto-hébergée, durcie par défaut (localhost, runAsNonRoot, egress allowlisté, télémétrie OFF) ; IaC validée (`compose config`, `helm lint`, `bicep build`) ; zéro étape manuelle cachée (cible `make` pour tout). |
| **Branding UI** | surcouche nginx GEREP (thème/logo/titre) — le whitelabel **admin** Onyx est EE ; on fait l'équivalent en FOSS via nginx (cf. [`../BRANDING_GEREP.md`](../BRANDING_GEREP.md)). |

## 2. Carte du code — [`../../deploy/`](../../deploy/) · [`../../scripts/`](../../scripts/)

| Chemin | Rôle |
|---|---|
| [`../../docker-compose.yml`](../../docker-compose.yml) | Stack mono-poste durcie (base). Surcouches : `.gpu`, `.performance`, `.prod-local`, `.lan`. |
| [`../../deploy/prod/`](../../deploy/prod/) | **Prod exposée** : `docker-compose.prod.yml` + `Caddyfile` (TLS auto) + `nginx.prod.conf` (oauth2-proxy → passerelle) + `env.prod.template`. |
| [`../../deploy/local-prod/`](../../deploy/local-prod/) | **Prod machine unique** : unit `onix.service` (systemd, boot) + README. |
| [`../../deploy/k8s/onix-ha/`](../../deploy/k8s/onix-ha/) | **Chart Helm HA** (OpenSearch/Postgres/MinIO/Redis HA, HPA, Celery, gateway, GPU). |
| [`../../deploy/azure/`](../../deploy/azure/) | **Azure/AKS** : `values-azure.yaml`, `bicep/` (IaC), README. |
| [`../../nginx/`](../../nginx/) | `onyx.conf` (reverse-proxy mono-poste) + `branding/` (thème GEREP). |
| [`../../scripts/`](../../scripts/) | `gen-secrets.sh`, `detect-hardware.sh/.ps1`, `pull-models.sh`, `preflight-local.sh`/`preflight-prod.sh`, `verify.sh`, `backup.sh`/`restore.sh`, `sync-doc-acl.py`, `setup-sharepoint-app.sh`/`setup-fabric-app.sh`. |

## 3. Commandes

```bash
# Mono-poste (dev/démo)
make tune && make secrets && make up && make verify
make preflight-local                 # pré-vol AVANT make up
# Production machine unique (durci)
make up-local-prod && make verify
# Prod exposée (Caddy TLS + OIDC)
make config-prod                     # valide base + surcouche prod
make secrets-prod && make up-prod
# Helm HA
make k8s-lint && make k8s-template   # (helm lint + template)
# Validation IaC (incluse dans make test)
make compose-validate                # tous les compose
# Nettoyage RELEASE (retire le jetable, garde code+docs+configs+.env+backups)
make clean                           # caches/artefacts/temporaires régénérables
make clean-deep                      # clean + venvs Python locaux
```

## 4. Tests & preuves

- `make compose-validate` (tous les overlays) + `make k8s-lint`/`k8s-template` +
  `bicep build` (Azure) — IaC valide, **incluse dans `make test`** (axe A6
  reproductibilité, cf. [`../../ralph/ORCHESTRATION.md`](../../ralph/ORCHESTRATION.md)).
- `make verify` : contrôle de bout en bout d'une stack démarrée.

## 5. Invariants & pièges

- **Ollama via nom de service interne** (`http://…-ollama:11434`), jamais `localhost`.
- **`num_ctx`** câblé (compose/Helm/Modelfile) : défaut Onyx 4096 = **troncature** —
  ne pas régresser.
- **Azure** : Redis = **TLS 6380 + noeviction** ; Postgres = `sslmode=require` ;
  **poser `ENCRYPTION_KEY_SECRET`** (sinon secrets en clair).
- **Zéro secret en repo** : `.env` gitignoré, généré par `gen-secrets.sh`.
- Conteneur nginx lié à **127.0.0.1** côté hôte (mono-poste) ; surcouches prod
  réécrites avec `!reset` (ne pas perdre les volumes — cf. branding).

## 6. Observabilité

Démarrage via `make verify` ; la pile observabilité est le scope
[`monitoring.md`](monitoring.md). Runbook ops : [`../RUNBOOK.md`](../RUNBOOK.md).

## 7. Docs de fond

[`../POC_LOCAL.md`](../POC_LOCAL.md) · [`../PROD_LOCAL.md`](../PROD_LOCAL.md) ·
[`../DEPLOY_PROD.md`](../DEPLOY_PROD.md) · [`../HA_SCALING.md`](../HA_SCALING.md) ·
[`../HA_ACCEPTANCE.md`](../HA_ACCEPTANCE.md) · [`../DEPLOY_AZURE.md`](../DEPLOY_AZURE.md) ·
[`../RUNBOOK.md`](../RUNBOOK.md) · [`../PERFORMANCE.md`](../PERFORMANCE.md) ·
[`../BRANDING_GEREP.md`](../BRANDING_GEREP.md).

## 8. Audit & journal

[`../audit-reality/deploy-ops.md`](../audit-reality/deploy-ops.md) ·
[`../../ralph/state/deploy-ops.md`](../../ralph/state/deploy-ops.md) ·
[`../../ralph/scopes/deploy-ops.md`](../../ralph/scopes/deploy-ops.md).

## 9. Sous-agent

| | |
|---|---|
| Discipline | SRE / DevOps / IaC |
| Skills | `/code-review`, `/verify` |
| MCP | `Microsoft_Learn` (AKS, bicep, Key Vault), `Context7` (helm, compose) ; `github` |
| Cibles de preuve | `helm lint`, `compose config`, `bicep build`, cibles `make` |

## 10. Maintenir cette fiche

Touche à `deploy/`, `nginx/`, `scripts/` ou un compose ⇒ mets à jour §2/§3, rejoue
`make compose-validate`/`k8s-lint`, reporte dans
[`../audit-reality/deploy-ops.md`](../audit-reality/deploy-ops.md) et le journal.
Vérifie : `make docs-check`.
