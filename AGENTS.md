# AGENTS.md — guide d'embarquement pour agents (et humains) sur **onix**

> **Lis ce fichier en premier.** Il oriente tout développement futur : ce qu'est
> onix, comment c'est organisé, comment build/test/déployer, les **règles de jeu**
> non-négociables, et où vit chaque *scope*. (`CLAUDE.md` pointe ici.)

## 1. C'est quoi onix (en 30 secondes)
**onix = un assistant RAG d'entreprise, 100 % souverain et auto-hébergé**, bâti sur
**Onyx** (plateforme RAG open-source, ex-Danswer) + **Ollama** (LLM local), **plus
une couche de compensation `onix`** qui ajoute ce qu'Onyx FOSS ne fournit pas pour
un client régulé (RGPD, multi-utilisateur, SharePoint client-360).

**Pourquoi cette couche existe** (cf. [`docs/audit-onyx/00-VERDICT.md`](docs/audit-onyx/00-VERDICT.md))
: l'audit byte-level d'Onyx v4.1.1 conclut **« plateforme premium prod-ready, PAS un
POC »**, MAIS les fonctions entreprise décisives sont **payantes (EE/Cloud)** ou
**absentes** : RBAC par-document (perm-sync = EE), **audit-trail (absent partout)**,
chiffrement des secrets (off par défaut, clair), SSO complet (partiel). onix comble
ces manques **en FOSS** : passerelle RBAC + ACL par-doc, audit HMAC chaîné, redaction
PII, DLP egress, rétention/effacement, télémétrie OFF, déploiement durci.

## 2. Modèle mental — 4 couches
```
┌─ Couche INFRA (Azure/AKS ou Docker Compose) ── deploy/ ─────────────────┐
│  Postgres · Redis · OpenSearch · MinIO · ingress/TLS · Key Vault        │
├─ Couche LLM ── Ollama (local, provider natif Onyx `ollama_chat`) ───────┤
├─ Couche ONYX (FOSS, MIT) ── RAG : connecteurs, indexation, chat, model-server
├─ Couche ONIX (notre valeur ajoutée) ───────────────────────────────────┤
│  access-gateway/  (RBAC + ACL par-doc + cache RBAC-safe + streaming + /metrics)
│  actions/         (audit OCR, génération .docx, tâches, notify, usage/coût, admin, audit HMAC, PII, DLP, rétention)
│  prompts/         (agent commercial sourcé, anti-injection)
│  tests/rag/       (red-team + garde-fous + éval RAGAS)
│  monitoring/      (Prometheus/Grafana/Loki)
└─────────────────────────────────────────────────────────────────────────┘
```
Détail : [`ARCHITECTURE.md`](ARCHITECTURE.md) (racine) + [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## 3. Carte du dépôt
| Chemin | Rôle |
|---|---|
| `docker-compose.yml` (+ `.gpu`/`.performance`/`.prod-local`/`.lan`) | Stack mono-poste durcie ; `.prod-local` = **production machine unique** (santé+ordre+`restart:always`), `.lan` = accès testeurs LAN |
| `access-gateway/` | Proxy RBAC FastAPI : cloisonnement, **ACL par-doc**, cache, streaming, `/metrics` |
| `actions/` | Microservice `onix-actions` : OCR/docgen/tasks/notify/usage/coût/admin + sécurité/RGPD |
| `prompts/` | Prompt système de l'agent commercial (sourcé, anti-injection) |
| `tests/rag/` | Garde-fous (red-team, post-filtre), éval **RAGAS** (`ragas_eval/`) |
| `monitoring/` | Observabilité locale (Prometheus/Grafana/Loki/alertes) |
| `deploy/k8s/onix-ha/` | **Chart Helm HA** (OpenSearch/Postgres/MinIO/Redis HA, HPA, Celery, +gateway, +GPU) |
| `deploy/prod/` | Compose prod exposé (Caddy TLS + OIDC oauth2-proxy + passerelle) — domaine public |
| `deploy/local-prod/` | **Production machine unique** : unit systemd (démarrage au boot) + README (cf. `docs/PROD_LOCAL.md`) |
| `deploy/azure/` | **Azure/AKS** : `values-azure.yaml`, `setup-entra.sh`, **`bicep/`** (IaC validée `bicep build`) — passerelle = template natif du chart |
| `scripts/` | `detect-hardware`(.sh/.ps1), `gen-secrets.sh`, `pull-models.sh`, `sync-doc-acl.py`, backup/restore, verify |
| `docs/` | Toute la doc de scope (index : [`docs/DOCS_INDEX.md`](docs/DOCS_INDEX.md)) |
| `docs/audit-onyx/` | Audit byte-level d'Onyx (7 dimensions + verdict) |

## 4. Build / test / déploiement
```bash
# Mono-poste (dev/démo)
make tune && make secrets && make up && make verify     # détecte HW, génère secrets, démarre, vérifie
make models                                             # pré-tire les modèles Ollama (num_ctx gravé)
# POC local complet (machine perso, connexion SharePoint, 1-2 testeurs) → docs/POC_LOCAL.md
make preflight-local                                    # pré-vol des prérequis AVANT make up (daemon, max_map_count, RAM, disque, ports, secrets)
# PRODUCTION sur MACHINE UNIQUE (durci : santé+ordre+restart:always, Tailscale/LAN) → docs/PROD_LOCAL.md
make up-local-prod && make verify                       # stack prod-local + contrôle de bout en bout

# Qualité (DOIT rester vert — voir §5)
make test            # lint + compose-validate + pytest + bandit + pip-audit + gitleaks + trivy
make rag-eval        # éval RAGAS LIVE (juge Ollama local) ; rag-eval-ci = + gate anti-régression

# Helm (HA)
helm lint deploy/k8s/onix-ha
helm template t deploy/k8s/onix-ha -f deploy/azure/values-azure.yaml

# Azure/AKS : runbook docs/DEPLOY_AZURE.md  ·  IaC repeatable : deploy/azure/bicep/
```
Suites pytest : `actions/tests`, `access-gateway/tests`, `tests/rag` (offline) ; éval/boot live = manuels.

## 5. Règles de jeu (NON négociables)
1. **Honnêteté > esbroufe. Zéro mock présenté comme du réel.** Si un truc n'est pas
   testé/vérifié, dis-le. Le client est exigeant ; une fausse affirmation = perte de confiance.
2. **Les portes de qualité restent VERTES** : `pytest` (actions/gateway/rag), `pip-audit
   --strict` (0 CVE), `gitleaks` (0 secret), `bandit` (0 medium+), `helm lint`,
   `compose config`. On relève les pins dès qu'une CVE apparaît (cf. `tests/rag/requirements.txt`).
3. **Sécurité par défaut** : aucun secret en repo (`.env` gitignoré, généré par
   `gen-secrets.sh`) ; fail-closed ; `runAsNonRoot` ; egress allowlisté ; télémétrie OFF.
4. **Style** : commentaires/docstrings **en français**, **stdlib-first** (pas de dépendance
   lourde sans raison), code qui ressemble au code voisin.
5. **Surfaces disjointes** quand tu parallélises (clones `/tmp` isolés, merge par SHA) —
   c'est le pattern multi-agent éprouvé de ce repo.
6. **FOSS vs EE** : toujours distinguer. Ne présuppose pas qu'une feature « entreprise »
   est gratuite (cf. l'audit). Le RBAC par-doc retenu ici = **gateway FOSS** (filtre de sortie).

## 6. Où vit chaque scope (liens)
> **Navigation agent** : commence par le **dossier de scope** [`docs/scopes/`](docs/scopes/README.md)
> (un fichier par scope : code, commandes, tests, invariants, observabilité, docs, journal),
> puis suis ses liens. Routeur sujet→doc : [`CLAUDE.md`](CLAUDE.md) § « Carte de navigation ».
- **RAG / qualité / Ollama** : [`docs/RAG_OPTIMIZATION.md`](docs/RAG_OPTIMIZATION.md) · [`docs/PLAYBOOK_ONYX_RAG.md`](docs/PLAYBOOK_ONYX_RAG.md) · [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md) · [`docs/RAG_EVAL.md`](docs/RAG_EVAL.md)
- **RBAC / cache / streaming** : [`docs/RBAC.md`](docs/RBAC.md) · [`docs/DECISION_RBAC.md`](docs/DECISION_RBAC.md) · [`docs/CACHE.md`](docs/CACHE.md) · [`docs/STREAMING.md`](docs/STREAMING.md)
- **Fonctions applicatives** : [`docs/ACTIONS.md`](docs/ACTIONS.md) · [`docs/FINOPS.md`](docs/FINOPS.md) · [`docs/AGENT_COMMERCIAL.md`](docs/AGENT_COMMERCIAL.md)
- **Sécurité / RGPD** : [`SECURITY.md`](SECURITY.md) · [`docs/SECURITY.md`](docs/SECURITY.md) · [`docs/SECURITY_RGPD_ACTIONS.md`](docs/SECURITY_RGPD_ACTIONS.md) · [`docs/RGPD.md`](docs/RGPD.md)
- **Déploiement / HA / ops** : [`docs/POC_LOCAL.md`](docs/POC_LOCAL.md) · [`docs/PROD_LOCAL.md`](docs/PROD_LOCAL.md) · [`docs/RUNBOOK.md`](docs/RUNBOOK.md) · [`docs/HA_SCALING.md`](docs/HA_SCALING.md) · [`docs/DEPLOY_PROD.md`](docs/DEPLOY_PROD.md) · [`docs/DEPLOY_AZURE.md`](docs/DEPLOY_AZURE.md) · [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md)
- **SharePoint / connecteurs** : [`docs/connectors/SHAREPOINT.md`](docs/connectors/SHAREPOINT.md)
- **Audit Onyx + parité** : [`docs/audit-onyx/00-VERDICT.md`](docs/audit-onyx/00-VERDICT.md) · [`docs/PARITE_ENTREPRISE.md`](docs/PARITE_ENTREPRISE.md) · [`docs/COMPARATIF_COPILOT_AC360.md`](docs/COMPARATIF_COPILOT_AC360.md)
- **Index complet** : [`docs/DOCS_INDEX.md`](docs/DOCS_INDEX.md)

## 7. Pièges à ne pas casser
- Onyx connecte Ollama via le **nom de service interne** (`http://…-ollama:11434`), pas localhost.
- `num_ctx` : défaut Onyx 4096 = **troncature** ; il est câblé (compose/Helm/Modelfile) — ne pas régresser.
- Azure : Redis = **TLS 6380 + noeviction** ; Postgres = `sslmode=require` ; **poser `ENCRYPTION_KEY_SECRET`** (sinon secrets en clair).
- Le cache **ne stocke QUE** le corps périmètre-déterministe ; l'ACL par-doc est ré-appliquée **par requête** (jamais mutualisée). Ne pas inverser cet ordre.
- Permission-sync SharePoint = **EE + certificat** ; en FOSS on indexe sans ACL → la passerelle fait le cloisonnement (filtre de sortie).
