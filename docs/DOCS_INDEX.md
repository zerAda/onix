# Index de la documentation onix

> Carte de **toute** la doc, par scope. Embarquement agent : [`../AGENTS.md`](../AGENTS.md).
> Racine : [`../ARCHITECTURE.md`](../ARCHITECTURE.md) · [`../SECURITY.md`](../SECURITY.md) · [`../CLAUDE.md`](../CLAUDE.md).

## 🚀 Embarquement & vue d'ensemble
| Doc | Contenu |
|---|---|
| [`../AGENTS.md`](../AGENTS.md) | **Guide agent** : projet, couches, carte du dépôt, build/test/déploiement, règles de jeu |
| [`../ARCHITECTURE.md`](../ARCHITECTURE.md) | Architecture **système** (4 couches, flux, frontière FOSS/EE/onix) |
| [`../SECURITY.md`](../SECURITY.md) | **Modèle de sécurité** (menaces, contrôles, audit→mitigations, RGPD) |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Détail composants Onyx (réseau interne, services) |

## 🔍 RAG · qualité · LLM (Ollama)
| Doc | Contenu |
|---|---|
| [`RAG_OPTIMIZATION.md`](RAG_OPTIMIZATION.md) | Audit consultant : `num_ctx`, embedding FR, reranker, capacité mesurée |
| [`PLAYBOOK_ONYX_RAG.md`](PLAYBOOK_ONYX_RAG.md) | Procédure d'optimisation (embedder/reranker/analyseur, **ré-index unique**) |
| [`PERFORMANCE.md`](PERFORMANCE.md) | Tuning matériel, KV-cache, capacité CPU/GPU, quantification |
| [`RAG_EVAL.md`](RAG_EVAL.md) | Éval **RAGAS** souveraine (`make rag-eval`/`-ci`, gate anti-régression) |
| [`AGENT_COMMERCIAL.md`](AGENT_COMMERCIAL.md) | Agent « Commercial 360 » : cas d'usage, prompt sourcé |

## 🛡️ RBAC · cache · streaming · garde-fous
| Doc | Contenu |
|---|---|
| [`RBAC.md`](RBAC.md) · [`DECISION_RBAC.md`](DECISION_RBAC.md) | Cloisonnement groupe→Document Set, **ACL par-doc**, FOSS vs EE |
| [`CACHE.md`](CACHE.md) | Cache RBAC-safe (clé HMAC périmètre) + **tier sémantique** + garde anti-divergence |
| [`STREAMING.md`](STREAMING.md) | Streaming SSE + garde DUR incrémental + override final |
| [`QA_GUARDRAILS.md`](QA_GUARDRAILS.md) · [`E2E_GUARDRAILS.md`](E2E_GUARDRAILS.md) · [`LIVE_GUARDRAILS_RESULTS.md`](LIVE_GUARDRAILS_RESULTS.md) | Red-team, post-filtre déterministe, preuves 21/21 |

## ⚙️ Fonctions applicatives (onix-actions)
| Doc | Contenu |
|---|---|
| [`ACTIONS.md`](ACTIONS.md) | OCR, génération .docx, tâches, notify, usage/coût, admin/kill-switch |
| [`FINOPS.md`](FINOPS.md) | Comptage tokens RÉELS (mesuré vs estimé), coûts |
| [`STATELESS_ACTIONS.md`](STATELESS_ACTIONS.md) | État déporté (Postgres/Redis/S3) pour la HA |

## 🔐 Sécurité · RGPD · conformité
| Doc | Contenu |
|---|---|
| [`SECURITY.md`](SECURITY.md) | Baseline durcissement (localhost, services, auth) |
| [`SECURITY_RGPD_ACTIONS.md`](SECURITY_RGPD_ACTIONS.md) | Sécurité/RGPD applicative (HMAC, PII, DLP, rétention) |
| [`RGPD.md`](RGPD.md) · [`REGISTRE_TRAITEMENTS.md`](REGISTRE_TRAITEMENTS.md) · [`DPIA_TEMPLATE.md`](DPIA_TEMPLATE.md) | Conformité : registre, DPIA, droits |

## ☁️ Déploiement · HA · exploitation
| Doc | Contenu |
|---|---|
| [`POC_LOCAL.md`](POC_LOCAL.md) | **POC local** (machine perso 64 Go) : démarrage, **connexion SharePoint pas-à-pas**, accès 1-2 testeurs (Tailscale/LAN), dépannage |
| [`PROD_LOCAL.md`](PROD_LOCAL.md) | **Production machine unique** : overlay durci (santé + démarrage ordonné + `restart:always`), systemd (boot), sauvegardes, accès TLS privé, durcissement auth |
| [`RUNBOOK.md`](RUNBOOK.md) | Mono-poste : upgrade, incidents, scaling, Ollama natif |
| [`HA_SCALING.md`](HA_SCALING.md) · [`HA_ACCEPTANCE.md`](HA_ACCEPTANCE.md) | Chart Helm HA, scale-out, preuves |
| [`DEPLOY_PROD.md`](DEPLOY_PROD.md) | Prod compose (Caddy TLS + OIDC + passerelle) |
| [`DEPLOY_AZURE.md`](DEPLOY_AZURE.md) | **Azure/AKS** : runbook az+helm, IaC `deploy/azure/bicep/`, gotchas |
| [`OBSERVABILITY.md`](OBSERVABILITY.md) | Métriques/logs/alertes (Onyx + gateway + actions) |

## 🔌 Connecteurs
| Doc | Contenu |
|---|---|
| [`connectors/SHAREPOINT.md`](connectors/SHAREPOINT.md) | Connexion SharePoint (Entra/Graph), perm-sync (EE) vs ACL (FOSS) |
| [`connectors/FABRIC.md`](connectors/FABRIC.md) | Accès Microsoft Fabric / OneLake / Power BI (SPN, audiences, RBAC fail-closed, FOSS vs EE) |
| [`E2E_ACCESS_LIVE.md`](E2E_ACCESS_LIVE.md) | **Runbook e2e LIVE** : prouver l'accès SharePoint + Fabric sur votre tenant (app Entra, `ONIX_E2E_*`, codes 0/1/2) |

## 🧪 Audit Onyx & parité
| Doc | Contenu |
|---|---|
| [`audit-onyx/00-VERDICT.md`](audit-onyx/00-VERDICT.md) | **Verdict** prod-ready-vs-POC + scorecard 7 dimensions (10→70) |
| [`PARITE_ENTREPRISE.md`](PARITE_ENTREPRISE.md) | Parité fonctionnelle vs assistant cloud d'entreprise |
| [`COMPARATIF_COPILOT_AC360.md`](COMPARATIF_COPILOT_AC360.md) | Comparatif vs Microsoft Copilot & AC360 |
