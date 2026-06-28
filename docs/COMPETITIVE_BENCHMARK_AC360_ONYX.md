# Competitive Benchmark — AC360 vs ONYX (onix)

> Benchmark **factuel** établi par inspection lecture-seule du code des deux projets
> (`.../Zeriri/AC360` et `.../Zeriri/onix`). Honnête et contextuel : il n'y a pas de
> « gagnant absolu », le verdict dépend du **contexte client** (cf. §Verdict).
> Complète [`COMPARATIF_COPILOT_AC360.md`](COMPARATIF_COPILOT_AC360.md) (vue produit/secteur).

## Résumé

| | AC360 | ONYX (onix) |
|---|---|---|
| Nature | Assistant Copilot Studio **Microsoft 365-natif** (Azure Functions Durable, SharePoint, Fabric, Entra, Document Intelligence) | Couche de **gouvernance souveraine FOSS** au-dessus d'**Onyx 4.1.1 + Ollama** (actions FastAPI + gateway RBAC + compose/Helm) |
| Fichiers Python | ~559 | actions + gateway + scripts (suites : actions 209✅, gateway 366✅, rag offline ~180✅) |
| Tests | 62 fichiers (31 backend sécurité, red-team 20 vecteurs) | ~770 tests (sécurité, RGPD, métier, observabilité) |
| Hébergement | Azure (cloud Microsoft) | **On-prem / souverain** (Docker/Helm), zéro dépendance cloud obligatoire |
| OCR | Document Intelligence (**cloud**, coût/page) | **Tesseract/poppler local** (zéro transfert, air-gappable) |
| Coût récurrent | Licences Copilot Studio + Azure Functions + Fabric (~k€/mois) | **Hosting seul** (FOSS, 0 € de licence) |
| **Gagnant actuel** | **selon contexte** (cf. Verdict) | **selon contexte** (cf. Verdict) |

## Score comparatif (sur 10, pondéré par l'audit)

| Domaine | AC360 | ONYX | Gagnant | Remarque (preuve) |
|---|---:|---:|---|---|
| Sécurité | 9 | 9 | = | AC360 : Entra SSO + OBO + DLP + gitleaks CI + IDOR owner_hash (`scripts/auth.py`, `graph_obo.py`). onix : fail-closed transverse, audit HMAC chaîné, DLP egress + rate-limit (`security.py`), PII redaction, anti-traversal docgen, config enums validés. |
| Architecture | 8 | 8 | = | AC360 : Durable Functions (durabilité managée). onix : séparation **gateway RBAC ↔ actions** (cloisonnement par-client par Document Set), Compose/Helm HA. |
| RAG / qualité IA | 8 | 7 | AC360 | AC360 : RAG Copilot Studio natif + citations auto + `useModelKnowledge:false` (red-team RT-01). onix : RAG **non-agentique** souverain (contourne le mur #12 Onyx↔Ollama), sourcé, garde-fou OWASP LLM01 (post-filtre déterministe testé). |
| Fonctionnalités métier | 8 | 8 | = | Les deux : audit doc, génération fiche, **réconciliation contrat↔SI**, relances, recherche. onix a ajouté : **réconciliation de portefeuille** (`reconcile_batch`), **export CSV** sécurisé, **synthèse client-360** + **portfolio_360**, endpoints dédiés. |
| Backend / API | 8 | 8 | = | AC360 : FastAPI gateway + Azure Functions. onix : FastAPI actions + gateway, Celery (async). |
| Observabilité | 8 | 9 | **ONYX** | AC360 : Application Insights + OpenTelemetry redacté. onix : **chaîne complète validée par tests** (alertmanager fail-closed, règles d'alerte, prometheus.yml, dashboards Grafana, sondes blackbox — 5 validateurs). |
| Tests | 8 | 9 | **ONYX** | AC360 : 62 fichiers, qualité (sécurité, red-team). onix : ~770 tests verts, bandit 0 medium+, pip-audit/trivy. |
| DevSecOps / CI | 8 | 8 | = | Les deux : gitleaks, pre-commit, CI. onix ajoute bandit (0 medium+), pip-audit --strict, trivy, docs-freshness gate. |
| Documentation | 8 | 8 | = | Les deux ~41 docs. onix : **doc-infra pour agents** (scopes.json, audit-reality preuve fichier:ligne, llms-full généré). |
| **Souveraineté / coût** | 4 | **10** | **ONYX** | onix : **FOSS, local-first, OCR local, 0 licence, données on-prem, no lock-in**. AC360 : multi-tenant Azure, OCR cloud, licences Fabric/Copilot, lock-in Microsoft. |
| **Time-to-prod / M365** | **9** | 5 | **AC360** | AC360 : Entra/SharePoint/Teams/Planner **out-of-box**, gouvernance héritée Microsoft. onix : doit câbler la passerelle RBAC + l'IdP en amont (le différenciateur ≠ le confort M365). |

## Avantages AC360 (honnête)

1. **Intégration Microsoft 365 native** — Entra ID SSO + OBO délégué SharePoint, Teams, Planner sans backend d'auth perso. Gouvernance/DLP héritées du tenant.
2. **Time-to-production** — orchestration Durable Functions managée, OCR Document Intelligence prêt, scalabilité auto. Moins de DevOps.
3. **RAG Copilot Studio** — citations SharePoint automatiques, `useModelKnowledge:false`, moins de code.
4. **Maturité enterprise prouvée** — red-team 20 vecteurs, OBO, IDOR owner_hash, safe-logger, gitleaks CI.

## Avantages ONYX (onix)

1. **Souveraineté totale** — FOSS auto-hébergeable, **OCR local** (Tesseract), données **on-prem**, air-gappable. Pas de transfert vers un cloud tiers.
2. **Coût** — **zéro licence** (vs Copilot Studio + Fabric + Functions + Document Intelligence). Hosting seul.
3. **Pas de lock-in** — remplaçable (Onyx FOSS, Ollama, Postgres/Vespa, Compose/Helm). Portable Kubernetes/on-prem.
4. **Observabilité + tests verrouillés** — chaîne d'alerting validée bout-en-bout, ~770 tests, gates bandit/pip-audit/trivy.
5. **Différenciateur réconciliation** — chaîne complète contrat↔SI Fabric : unitaire → **portefeuille** → export Excel → **vue client-360** → portefeuille-360, fail-closed et RGPD-minimisée.

## Idées transférables vers onix (inspiration conceptuelle, PAS copie)

| Inspiration AC360 | Pourquoi utile | Adaptation onix |
|---|---|---|
| **Red-team statique** (assertions de réglages : `useModelKnowledge=false`, content-moderation) | Tests sécurité IA rapides, déterministes | onix a déjà un red-team comportemental (`tests/rag/test_red_team.py`) ; on pourrait ajouter des **assertions statiques** sur la config du garde-fou (guardrail_enabled, force_internal_search). |
| **Schémas JSON aux frontières** (`schemas/*.json` valident OCR/audit I/O) | Contrats explicites entre étapes | onix valide via Pydantic aux endpoints ; OK. Inspiration : documenter le **contrat** des dicts internes (déjà fait en docstring pour `reconcile_batch`/`client_360`). |
| **`/api/ready` distinct de `/health`** | Readiness (déps prêtes) vs liveness | Candidat onix : un `/ready` actions qui vérifie OCR/DB/Ollama dispo (probe de readiness K8s). |

## Idées refusées (anti-overengineering)

| Idée AC360 | Pourquoi NON pour onix |
|---|---|
| OBO Graph / Entra dans actions | onix sépare **gateway = RBAC** / actions = API-key interne. Mettre l'OBO dans actions casserait l'architecture (le cloisonnement est au niveau gateway). Choix assumé FOSS-vs-EE. |
| Durable Functions | Lock-in Azure. onix a Celery (async, portable). |
| Document Intelligence cloud | Casse la souveraineté (OCR local = différenciateur). |

## Verdict (contextuel, honnête)

- **Client « M365-first », time-to-prod prioritaire, budget cloud assumé** → **AC360 gagne** (intégration native, gouvernance héritée).
- **Client souverain / sensible aux données / coût / air-gap / anti-lock-in** (ex. assurance/mutuelle française, exigences RGPD strictes) → **ONYX (onix) gagne** (FOSS, local, 0 licence, portable) — **tout en égalant** AC360 sur sécurité/gouvernance et en le **dépassant** sur observabilité/tests.

**Prochaine bataille à gagner pour onix** : combler l'écart **RAG** et soigner le **time-to-prod** (faciliter le câblage gateway/IdP), pour neutraliser les 2 seuls vrais avantages d'AC360.

> **MAJ 2026-06-28 (run LIVE)** : le RAG **non-agentique** souverain est **validé en réel** (Ollama gemma4 8B : récupération accent-folded + génération grounded/sourcée + fail-closed). Surtout, une **sonde agentique** montre que gemma4 émet des `tool_calls` natifs corrects via l'API `/api/chat` → le **mur #12 est un défaut d'intégration Onyx, PAS une limite du modèle local**. L'écart agentique vs AC360 est donc **franchissable en souverain** (couche `agentic_local` via API native, à concevoir avec un design fail-closed — outils lecture-seule, gate, audit). Le seul avantage *durablement* différenciant d'AC360 redevient le **time-to-prod M365-natif**.
