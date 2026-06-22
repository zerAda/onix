# PROD-ROADMAP — onix : NO-GO → GO (boucle de mise en production)

> Plan maître pour amener onix à une **vraie mise en production**, **sécurité blindée**,
> end-to-end, robuste. Source de vérité opérationnelle : on avance **par cycles**, chacun
> = livrable **vérifié** + **gates verts** (`bandit` 0 · `gitleaks` 0 · `pip-audit --strict` 0
> · suites offline) + **docs MAJ** (scope + audit-reality + ralph/state). Invariants non
> négociables : **fail-closed**, **zéro secret en repo**, **zéro mock présenté comme réel**.

Ancré sur les preuves : [`PROD-READINESS.md`](PROD-READINESS.md) (7 dimensions, 0 GO),
[`MISSIONS.md`](MISSIONS.md) (M1–M20), [`RUNTIME-EVIDENCE.md`](RUNTIME-EVIDENCE.md) (#1–12, live Azure).

## Critère de GO (7 dimensions vertes, avec preuve)
1. Fonctionnalités up · 2. Sécurité applicative · 3. RAG produit · 4. Fiabilité/résilience ·
5. Observabilité/alerting · 6. Supply-chain · 7. Compliance/RGPD.

## Cycles (ordre = sécurité d'abord, valeur #1 auditable)

### Cycle 1 — Sécurité applicative 🔴 (BLOQUANT auditeur) — *plan détaillé*
[`docs/superpowers/plans/2026-06-22-onix-prod-cycle1-securite.md`](../docs/superpowers/plans/2026-06-22-onix-prod-cycle1-securite.md)
- **M1** — audit HMAC *algo-downgrade* (`actions/app/audit_log.py:189`) → vérif **fail-closed**, refus du downgrade keyless quand une clé existe.
- **M7** — *trust* `X-OIDC-Claims` verbatim (`access-gateway/app/identity.py:140`, 4 call-sites `main.py`) → exiger un **secret partagé proxy**, rejeter tout header non prouvé (anti-spoof RBAC).
- **M3** — ACL Fabric non câblée au filtre de citations → câbler l'ACL par-doc (deny-by-default).
- **Supply-chain** — `pip-audit --strict` ROUGE → bump dep CVE, gate vert.
- **Sortie** : 4 vulns fermées, tests de non-régression, gates sécu verts.

### Cycle 2 — Fiabilité, résilience & exploitation 🔴
- **M4** — livraison d'alertes *no-op* → canal réel (webhook/SMTP) **fail-closed** + test.
- **Résilience #6** — `restart:always` rate un kill pendant l'init → politique restart/healthcheck durcie + preuve.
- **#9** — provider LLM Onyx **non seedé** au déploiement → seed auto du `llm_provider` (sinon chat mort).
- **#10** — `make tune` sous-dimensionne `OLLAMA_MEM_LIMIT` (12g) pour un 14B (OOM) → dimensionnement au modèle.

### Cycle 3 — RAG produit (le mur #12) 🔴
- **#12** — qwen2.5:14b **inapte au tool-calling agentique** d'Onyx (hallucine au lieu de citer). Décision : modèle à function-calling fiable (**GPU** pour un modèle plus capable) **ou** persona RAG non-agentique garantissant le grounding. Preuve : réponse **sourcée + citation** E2E.
- **RAGAS** baseline réelle (qualité mesurée, pas supposée).

### Cycle 4 — Compliance, RGPD & durcissement final 🔴
- **M20** — honnêteté compliance (FOSS vs EE), RGPD (minimisation, rétention, DLP).
- **Fiabilité Dim 4 restante** — backup **chiffré** + WAL (le tar froid actuel n'est pas suffisant prod).
- **Revue sécu transverse** + **e2e accès LIVE** ([`docs/E2E_ACCESS_LIVE.md`](../docs/E2E_ACCESS_LIVE.md)).

## Boucle d'exécution (chaque cycle)
1. **Plan** détaillé (skill `writing-plans`).
2. **Exécution** multi-agents (subagent-driven / Workflow) : implémente (TDD, worktree) → **vérifie en adversarial**.
3. **Land** : j'applique les diffs vérifiés, lance **gates complets**, commit atomique.
4. **MAJ docs** scope + audit-reality + ralph/state (`make docs-freshness` vert).
5. **Re-score** la dimension dans `PROD-READINESS.md` (preuve `fichier:ligne` ou runtime).
6. Cycle suivant jusqu'à **7/7 GO**.

*Mis à jour à chaque cycle. Avancement détaillé : `ORCHESTRATOR-LOG.md`.*
