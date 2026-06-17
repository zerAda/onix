# ARCHITECTURE — onix (vue d'ensemble système)

> Vue **holistique** (tous scopes). Le détail par composant Onyx est dans
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) ; chaque sous-système a sa doc
> dédiée (cf. [`docs/DOCS_INDEX.md`](docs/DOCS_INDEX.md)). Embarquement : [`AGENTS.md`](AGENTS.md).

## 1. Vue d'ensemble — 4 couches
```
                         Utilisateur (navigateur / API)
                                    │  HTTPS
                          ┌─────────▼──────────┐
                          │ Ingress + TLS      │  (nginx/Caddy/App Gateway)
                          │ + oauth2-proxy OIDC │  ← Entra ID (identité vérifiée)
                          └───┬──────────────┬──┘
              /api/chat/*  →  │              │  ← tout le reste → Onyx natif
                       ┌──────▼───────────┐  │
   COUCHE ONIX ───────►│ access-gateway   │  │   RBAC (cloisonnement + ACL par-doc),
   (valeur ajoutée)    │ (FastAPI)        │  │   cache RBAC-safe, streaming SSE,
                       │ X-OIDC-Claims    │  │   post-filtre garde-fous, /metrics
                       └──────┬───────────┘  │
                              ▼              ▼
                       ┌──────────────────────────┐        ┌───────────────────┐
   COUCHE ONYX ───────►│ api_server (FastAPI)      │◄──────►│ inference          │
   (FOSS, MIT)         │ background (Celery, index)│        │ model-server       │
                       └───┬───────┬───────┬───────┘        │ (embeddings/rerank)│
                           │       │       │                └───────────────────┘
              ┌────────────┼───────┼───────┼──────────────┐
              ▼            ▼       ▼       ▼              ▼
        ┌──────────┐ ┌──────────┐ ┌──────┐ ┌──────────┐ ┌──────────────────────┐
        │ Postgres │ │OpenSearch│ │ Redis│ │  MinIO   │ │ Ollama (LLM local)    │  COUCHE LLM
        │ (méta)   │ │(vecteur+ │ │(broker│ │ (fichiers│ │ provider `ollama_chat`│
        │          │ │ BM25)    │ │+cache│ │  S3)     │ │ CPU ou GPU            │
        └──────────┘ └──────────┘ └──────┘ └──────────┘ └──────────────────────┘
              ▲ data-tier (COUCHE INFRA : managé Azure ou in-cluster/compose)

   onix-actions (microservice) ◄── Onyx Custom Actions ──► audit OCR · docgen .docx ·
   tâches · notify · usage/coût · admin/kill-switch · audit HMAC · PII · DLP · rétention
```

## 2. Composants
| Composant | Couche | Rôle | Stateless ? |
|---|---|---|---|
| ingress + oauth2-proxy | infra | TLS + SSO OIDC (Entra), pose `X-OIDC-Claims` vérifié | oui |
| **access-gateway** | onix | RBAC (groupes→Document Sets + **ACL par-doc**), cache RBAC-safe, streaming, garde-fous, `/metrics` | oui |
| api_server (Onyx) | onyx | API chat/recherche, auth, admin | oui |
| background (Onyx) | onyx | indexation, Celery, permission-sync (EE) | oui (état→DB/Redis) |
| inference/index model-server | onyx | embeddings + reranking | oui |
| **onix-actions** | onix | OCR, docgen, tâches, notify, usage/coût, admin, **audit HMAC**, PII, DLP, rétention | oui (état→PG/Redis/S3) |
| Ollama | llm | génération LLM locale (`ollama_chat`) | modèle en mémoire |
| Postgres | infra | métadonnées, chat, file-store(option) | **état** |
| OpenSearch | infra | index vecteur + lexical (BM25) | **état** |
| Redis | infra | broker Celery + locks + cache | **état** |
| MinIO / S3 | infra | fichiers + .docx générés | **état** |

## 3. Flux de données
- **Ingestion** : connecteur SharePoint (Graph, app-only) → `background` (chunking 512, embeddings via model-server) → **OpenSearch** (vecteur+BM25) + **MinIO** (fichiers). FOSS = indexation **sans** ACL (perm-sync = EE) → cloisonnement assuré en aval par la passerelle.
- **Requête / chat** : user → ingress(OIDC) → **gateway** : (1) cloisonnement Document Set par groupe ; (2) **cache** RBAC-safe (clé HMAC = périmètre trié) — hit ⇒ 0 LLM ; (3) sinon → Onyx `api_server` → retrieval OpenSearch + rerank → **Ollama** (génération) → **post-filtre garde-fous** (déterministe, hors-LLM) → cache store ; (4) **ACL par-doc par utilisateur** (filtre de sortie, ré-appliqué hit ET miss) → streaming SSE → user.
- **Actions applicatives** : l'assistant appelle une **Onyx Custom Action** → `onix-actions` (OCR/docgen/tâches/notify…) avec clé API + identité HMAC + DLP egress.
- **Audit / observabilité** : décisions d'accès + garde-fous → journal **HMAC chaîné** (tamper-evident) ; métriques Prometheus (Onyx + gateway + actions) → Grafana.

## 4. Frontière FOSS / EE / onix (décisif — cf. audit)
| Capacité | Onyx FOSS | Onyx EE (payant) | **onix (FOSS)** |
|---|:--:|:--:|:--:|
| RAG sourcé, connecteurs, chat agentique | ✅ | ✅ | (utilise Onyx) |
| RBAC **par document** | ❌ | ✅ (perm-sync, cert) | ✅ **filtre de sortie** (gateway doc-ACL) |
| **Audit-trail** « qui a vu quoi » | ❌ | ❌ | ✅ **HMAC chaîné** (actions) |
| Chiffrement secrets | ❌ (clair) | ✅ (secrets) | ✅ `ENCRYPTION_KEY_SECRET` + Key Vault/CMK |
| Cache réponses / streaming garde-fouté | ❌ | ❌ | ✅ gateway |
| Effacement art.17 / rétention | ⚠️ cassé | ⚠️ | ✅ endpoints actions |
| Télémétrie sortante | ON | ON (+domaine) | **OFF** |

## 5. État, scalabilité, HA
Tier applicatif **stateless** (api/background/model-server/gateway/actions) → réplicas + HPA.
Tier data = **état** → en prod : **managé HA** (Azure DB Postgres Flexible zone-redondant +
Azure Cache Redis) pour tuer les SPOF que l'[audit](docs/audit-onyx/10-architecture-scalability.md)
a pointés ; OpenSearch + MinIO en StatefulSet multi-nœuds (pas de managé Azure). Migrations
Alembic via **Job pre-install** (pas inline → pas de course). Détail : [`docs/HA_SCALING.md`](docs/HA_SCALING.md).

## 6. Cibles de déploiement
| Cible | Quoi | Doc |
|---|---|---|
| Mono-poste | `docker-compose.yml` durci (dev/démo, souverain) | [`docs/RUNBOOK.md`](docs/RUNBOOK.md) |
| Prod compose | Caddy TLS + OIDC oauth2-proxy + passerelle | [`docs/DEPLOY_PROD.md`](docs/DEPLOY_PROD.md) |
| **Kubernetes HA** | chart `deploy/k8s/onix-ha` (data-tier HA, HPA, +gateway natif, +GPU activable) | [`docs/HA_SCALING.md`](docs/HA_SCALING.md) |
| **Azure / AKS** | `deploy/azure/` : `values-azure.yaml` + `bicep/` (IaC) + `setup-entra.sh` | [`docs/DEPLOY_AZURE.md`](docs/DEPLOY_AZURE.md) |

## 7. Décisions d'architecture (pourquoi)
- **Souveraineté** : LLM (Ollama) + index + fichiers **sur site / dans votre tenant** ; aucun appel cloud d'IA ; télémétrie off.
- **RBAC en FOSS** : filtre de **sortie** (gateway) car la perm-sync à la récupération est EE. Compromis assumé, documenté ([`docs/DECISION_RBAC.md`](docs/DECISION_RBAC.md)).
- **Managé pour le data-tier** (Azure) : corrige les SPOF par défaut d'Onyx sans réinventer la HA.
- **Déterminisme sécurité** : le post-filtre garde-fous est **hors-LLM** (non manipulable par injection).
