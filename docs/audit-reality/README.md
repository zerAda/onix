# Audit byte-by-byte — Documentation ↔ Réalité

> **But** : vérifier, scope par scope, que **chaque affirmation concrète de la doc**
> correspond réellement au **code/config existant** dans ce dépôt. Conformément à la
> règle de jeu n°1 (`AGENTS.md`) : **« Honnêteté > esbroufe. Zéro mock présenté comme
> du réel. »** Tout écart est tracé avec preuve `fichier:ligne`.

## Méthodologie (commune à tous les rapports)
1. **Extraire** de chaque doc les affirmations *vérifiables* (chemins, variables d'env,
   ports, valeurs par défaut, commandes Make, comportements de code, garanties de
   sécurité, cibles CI, garde-fous).
2. **Localiser** l'implémentation correspondante (`fichier:ligne`).
3. **Classer** chaque affirmation (légende ci-dessous), avec preuve.
4. **Évaluer l'écart « production-ready entreprise »** : sécurité, fiabilité,
   observabilité, tests, scalabilité, RGPD.

## Légende de classification
| Symbole | Sens |
|---|---|
| ✅ `CONFORME` | La doc décrit exactement ce que fait le code/config. |
| ⚠️ `ÉCART MINEUR` | Doc imprécise/périmée mais l'intention tient (ex: chemin renommé, défaut changé). |
| ❌ `ÉCART MAJEUR` | La doc affirme un comportement **faux**. |
| 🕳️ `DOC-SANS-CODE` | La doc décrit une fonctionnalité **non implémentée** (risque « mock présenté comme réel »). |
| 🔇 `CODE-SANS-DOC` | Implémenté mais **non/mal documenté**. |
| ❔ `NON VÉRIFIABLE` | L'affirmation porte sur du code externe (ex: Onyx v4.1.1) **non vendoré** ici → à ne pas inventer. |

## Index des rapports par scope
| Rapport | Scope | Docs couvertes |
|---|---|---|
| [`access-gateway.md`](access-gateway.md) | Passerelle RBAC/ACL/cache/streaming/metrics | RBAC, DECISION_RBAC, CACHE, STREAMING |
| [`actions.md`](actions.md) | Microservice `onix-actions` | ACTIONS, FINOPS, SECURITY_RGPD_ACTIONS, STATELESS_ACTIONS |
| [`rag-prompts.md`](rag-prompts.md) | Garde-fous RAG / éval RAGAS / agent commercial | RAG_EVAL, RAG_OPTIMIZATION, PLAYBOOK_ONYX_RAG, E2E/QA/LIVE_GUARDRAILS, AGENT_COMMERCIAL |
| [`deploy-ops.md`](deploy-ops.md) | Déploiement / HA / ops | DEPLOY_PROD, DEPLOY_AZURE, HA_SCALING, HA_ACCEPTANCE, PROD_LOCAL, POC_LOCAL, RUNBOOK, PERFORMANCE |
| [`monitoring.md`](monitoring.md) | Observabilité | OBSERVABILITY |
| [`security-governance.md`](security-governance.md) | Sécurité / RGPD / gouvernance / parité / audit-onyx | SECURITY(.md×2), RGPD, REGISTRE_TRAITEMENTS, DPIA_TEMPLATE, ARCHITECTURE×2, PARITE_ENTREPRISE, COMPARATIF, audit-onyx/* |

## Synthèse
Voir [`_VERDICT.md`](_VERDICT.md) (consolidé après passage des 6 agents).
