# Dossiers de scope — infrastructure de navigation pour agents

> **But.** Un agent (ou un humain) qui doit intervenir sur onix part d'**ICI**,
> choisit son **scope**, et trouve dans **un seul fichier** : la carte du code, les
> commandes, les tests, les invariants à ne pas casser, l'observabilité, les docs
> de fond et le journal d'état. C'est la couche « routeur » entre la vue projet
> (`CLAUDE.md` / `AGENTS.md`) et les docs de fond (`docs/*.md`).

Embarquement projet : [`../../AGENTS.md`](../../AGENTS.md) · Routeur sujet→doc :
[`../../CLAUDE.md`](../../CLAUDE.md) · Index doc complet :
[`../DOCS_INDEX.md`](../DOCS_INDEX.md) · Méthode de durcissement (boucles) :
[`../../ralph/ORCHESTRATION.md`](../../ralph/ORCHESTRATION.md).

## Les 6 scopes

| Scope | Dossier agent | Mission (1 ligne) | Code | Audit ↔ État |
|---|---|---|---|---|
| **access-gateway** | [`access-gateway.md`](access-gateway.md) | Passerelle RBAC : cloisonnement groupe→Document Set, ACL par-doc, cache RBAC-safe, streaming, `/metrics` | [`../../access-gateway/`](../../access-gateway/) | [audit](../audit-reality/access-gateway.md) · [état](../../ralph/state/access-gateway.md) |
| **actions** | [`actions.md`](actions.md) | Microservice `onix-actions` : OCR, docgen, tâches, notify, usage/coût, admin + sécurité/RGPD (audit HMAC, PII, DLP, rétention) | [`../../actions/`](../../actions/) | [audit](../audit-reality/actions.md) · [état](../../ralph/state/actions.md) |
| **rag-prompts** | [`rag-prompts.md`](rag-prompts.md) | Qualité RAG : garde-fous red-team, post-filtre déterministe, éval RAGAS, prompt agent sourcé/anti-injection | [`../../tests/rag/`](../../tests/rag/) · [`../../prompts/`](../../prompts/) | [audit](../audit-reality/rag-prompts.md) · [état](../../ralph/state/rag-prompts.md) |
| **monitoring** | [`monitoring.md`](monitoring.md) | Observabilité : Prometheus/Grafana/Loki/Promtail/Alertmanager/Blackbox, alertes + SLO | [`../../monitoring/`](../../monitoring/) | [audit](../audit-reality/monitoring.md) · [état](../../ralph/state/monitoring.md) |
| **deploy-ops** | [`deploy-ops.md`](deploy-ops.md) | Déploiement & exploitation : Compose (mono-poste/prod-local/prod), Helm HA, Azure/bicep, scripts ops | [`../../deploy/`](../../deploy/) · [`../../scripts/`](../../scripts/) | [audit](../audit-reality/deploy-ops.md) · [état](../../ralph/state/deploy-ops.md) |
| **security-governance** | [`security-governance.md`](security-governance.md) | Sécurité transverse & conformité : modèle de menaces, durcissement, RGPD (registre, DPIA), parité FOSS/EE | [`../../SECURITY.md`](../../SECURITY.md) · [`../SECURITY.md`](../SECURITY.md) | [audit](../audit-reality/security-governance.md) · [état](../../ralph/state/security-governance.md) |

## Carte sujet → scope (recherche rapide)

| Si tu cherches… | Scope | Dossier / doc de fond |
|---|---|---|
| RBAC, cloisonnement, Document Set, ACL par-doc | access-gateway | [`access-gateway.md`](access-gateway.md) · [`../RBAC.md`](../RBAC.md) · [`../DECISION_RBAC.md`](../DECISION_RBAC.md) |
| Cache (HMAC périmètre, sémantique) | access-gateway | [`../CACHE.md`](../CACHE.md) |
| Streaming (NDJSON), garde DUR incrémental | access-gateway | [`../STREAMING.md`](../STREAMING.md) |
| SharePoint, Microsoft Graph, perm-sync | access-gateway | [`../connectors/SHAREPOINT.md`](../connectors/SHAREPOINT.md) |
| Microsoft Fabric / OneLake / Power BI | access-gateway | [`../connectors/FABRIC.md`](../connectors/FABRIC.md) |
| e2e d'accès LIVE (SharePoint + Fabric) | access-gateway | [`../E2E_ACCESS_LIVE.md`](../E2E_ACCESS_LIVE.md) |
| OCR, génération .docx, tâches, notify | actions | [`actions.md`](actions.md) · [`../ACTIONS.md`](../ACTIONS.md) |
| Comptage tokens / coûts (FinOps) | actions | [`../FINOPS.md`](../FINOPS.md) |
| Audit-trail HMAC, PII, DLP, rétention | actions | [`../SECURITY_RGPD_ACTIONS.md`](../SECURITY_RGPD_ACTIONS.md) |
| État déporté (HA des actions) | actions | [`../STATELESS_ACTIONS.md`](../STATELESS_ACTIONS.md) |
| Qualité RAG, num_ctx, embedding, reranker | rag-prompts | [`rag-prompts.md`](rag-prompts.md) · [`../RAG_OPTIMIZATION.md`](../RAG_OPTIMIZATION.md) · [`../PLAYBOOK_ONYX_RAG.md`](../PLAYBOOK_ONYX_RAG.md) |
| Éval RAGAS (gate anti-régression) | rag-prompts | [`../RAG_EVAL.md`](../RAG_EVAL.md) |
| Red-team, post-filtre, garde-fous | rag-prompts | [`../QA_GUARDRAILS.md`](../QA_GUARDRAILS.md) · [`../E2E_GUARDRAILS.md`](../E2E_GUARDRAILS.md) |
| Prompt agent commercial (anti-injection) | rag-prompts | [`../AGENT_COMMERCIAL.md`](../AGENT_COMMERCIAL.md) |
| Métriques, logs, alertes, SLO, dashboards | monitoring | [`monitoring.md`](monitoring.md) · [`../OBSERVABILITY.md`](../OBSERVABILITY.md) |
| Démarrage mono-poste / POC local | deploy-ops | [`deploy-ops.md`](deploy-ops.md) · [`../POC_LOCAL.md`](../POC_LOCAL.md) |
| Production machine unique | deploy-ops | [`../PROD_LOCAL.md`](../PROD_LOCAL.md) |
| Prod exposée (Caddy TLS + OIDC) | deploy-ops | [`../DEPLOY_PROD.md`](../DEPLOY_PROD.md) |
| Helm HA / scale-out | deploy-ops | [`../HA_SCALING.md`](../HA_SCALING.md) |
| Azure / AKS / bicep | deploy-ops | [`../DEPLOY_AZURE.md`](../DEPLOY_AZURE.md) |
| Branding UI GEREP | deploy-ops | [`../BRANDING_GEREP.md`](../BRANDING_GEREP.md) |
| Modèle de sécurité, menaces, durcissement | security-governance | [`security-governance.md`](security-governance.md) · [`../../SECURITY.md`](../../SECURITY.md) |
| RGPD, registre, DPIA | security-governance | [`../RGPD.md`](../RGPD.md) · [`../REGISTRE_TRAITEMENTS.md`](../REGISTRE_TRAITEMENTS.md) · [`../DPIA_TEMPLATE.md`](../DPIA_TEMPLATE.md) |
| Audit Onyx (FOSS vs EE), parité | security-governance | [`../audit-onyx/00-VERDICT.md`](../audit-onyx/00-VERDICT.md) · [`../PARITE_ENTREPRISE.md`](../PARITE_ENTREPRISE.md) |

## Anatomie d'un dossier de scope (gabarit)

Chaque `docs/scopes/<scope>.md` suit le **même plan** (pour que l'agent sache où
regarder, et pour la validation automatique) :

1. **Mission & frontière FOSS/EE** — ce que le scope apporte, et ce qui reste EE/absent.
2. **Carte du code** — chemins → rôle, point(s) d'entrée.
3. **Commandes** — build / test / run propres au scope (cibles `make` réelles).
4. **Tests & preuves** — suites, ce qu'elles prouvent.
5. **Invariants & pièges** — ce qu'il ne faut **pas** casser (cf. `AGENTS.md §7`), **dont
   la sécurité** : chaque dossier porte une ligne `🔒 Sécurité (scope)` qui renvoie à
   [`../../SECURITY.md`](../../SECURITY.md) + au scope [`security-governance`](security-governance.md)
   et rappelle les gates à passer (fail-closed, zéro secret, bandit/gitleaks/pip-audit/trivy).
6. **Observabilité** — métriques / logs / alertes du scope.
7. **Docs de fond** — pour approfondir.
8. **Audit & journal** — `docs/audit-reality/<scope>.md` + `ralph/state/<scope>.md`.
9. **Sous-agent** — discipline, skills, MCP, cibles de preuve (cf. `ORCHESTRATION.md §2`).
10. **Maintenir cette fiche** — quand et comment la mettre à jour.

## Tenir l'infra à jour (« à chaque action »)

Source de vérité : le registre [`scopes.json`](scopes.json) (scope → code/dossier/audit/state).

```bash
make docs-check        # STRUCTURE : registre + gabarit des dossiers + 0 lien mort (CI + make test)
make docs-freshness    # ANTI-DRIFT : code de scope modifié ⇒ sa doc DOIT l'être (gate CI sur PR)
make hooks-install     # pose les deux gardes en pre-commit (à chaque commit)
```
Règle : **tu touches le code d'un scope → tu mets à jour son dossier ici**, son
`docs/audit-reality/<scope>.md` (preuve `fichier:ligne`) et son
`ralph/state/<scope>.md` (journal). Dérogation justifiée : `[docs-skip:<scope>]` dans
le commit. Détail : [`../../CLAUDE.md`](../../CLAUDE.md) § « Tenir cette doc-infra à jour ».
Carte agent racine : [`../../llms.txt`](../../llms.txt).
