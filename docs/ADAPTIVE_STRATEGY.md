# Adaptive Strategy — ONYX (onix)

> Stratégie **vivante** : mise à jour à chaque itération de la boucle adaptative.
> Référence concurrentielle : [`COMPETITIVE_BENCHMARK_AC360_ONYX.md`](COMPETITIVE_BENCHMARK_AC360_ONYX.md).

## État actuel (audit du 2026-06-25)

- **Score global estimé** : **~89/100** (socle très mature après ~41 itérations + 6 tours feature/audit/test).
- **Niveau** : **production-ready** pour un déploiement souverain ; *enterprise-premium* atteint sur sécurité/gouvernance/observabilité.
- **Risque principal** : **écart RAG** — Onyx 4.1.1 a un mur agentique avec Ollama local (#12) ; onix le contourne en RAG non-agentique sourcé, mais c'est le seul domaine où AC360 (Copilot Studio) garde l'avantage.
- **Blocage principal** : aucun P0/P1 critique ouvert. Le code est vert (~770 tests, bandit 0 medium+, pip-audit/trivy/gitleaks verts).
- **Opportunité principale** : **valeur métier différenciante** — la chaîne « Assistant Client 360 » (réconciliation portefeuille → export → 360 client → portefeuille-360) que onix construit et qu'AC360 n'a pas en self-service exportable.

## Objectifs

- **Court terme** : finir la chaîne 360 (endpoints portfolio_360 + export), neutraliser les 2 avantages AC360 (RAG, time-to-prod).
- **Moyen terme** : combler l'écart RAG (modèle à function-calling fiable sur GPU → agentique natif ; OU améliorer le grounding/citations du mode non-agentique), readiness probe, faciliter le câblage gateway/IdP.
- **Premium** : éval RAG quantifiée (RAGAS au vert sur dataset métier), packaging démo « 1 commande », dossier de conformité (RGPD/souveraineté) prêt à présenter.

## Pondération actuelle (adaptée à l'état mature du projet)

| Domaine | Poids | Pourquoi ce poids (vs barème de départ) |
|---|---:|---|
| RAG / qualité IA | **20** | ↑ (12→20) : SEUL domaine où AC360 mène (mur #12). C'est la bataille à gagner. |
| Fonctionnalités métier / différenciation | **18** | ↑ (10→18) : la chaîne 360 est l'avantage concurrentiel concret ; le socle technique est déjà solide. |
| Sécurité | 12 | ≈ : déjà très fort (fail-closed, audit HMAC, DLP, rate-limit, PII, anti-traversal) → maintenance, pas chantier. |
| Observabilité | 6 | ↓ (8→6) : **saturé** (5 validateurs, chaîne validée). Ne plus y toucher. |
| Tests | 6 | ↓ (8→6) : ~770 tests verts. Couvrir les nouvelles features, pas re-tester l'existant. |
| Architecture | 8 | ≈ : gateway↔actions stable. |
| Backend / API | 8 | ≈ : endpoints additifs au fil des features. |
| DevSecOps | 6 | ≈ : gates verts. |
| Time-to-prod / DevEx | **6** | ↑ (implicite) : faciliter le câblage gateway/IdP = neutraliser l'avantage AC360. |
| Documentation | 4 | ≈ : doc-infra agents en place. |

## Backlog adaptatif

| Priorité | Action | Impact | Risque | Preuve | Statut |
|---|---|---:|---:|---|---|
| P2 | Endpoint `POST /portfolio/360` (exposer `portfolio_360`, gate, format=csv) | Moyen | Faible | Tour 7 du cycle | planifié |
| P2 | Readiness probe `/ready` actions (OCR/DB/Ollama dispo) — inspiré AC360 `/api/ready` | Moyen | Faible | benchmark §inspirations | backlog |
| P2 | Éval RAG quantifiée (RAGAS au vert sur dataset métier GEREP) | Élevé (RAG) | Moyen (env Ollama) | gap RAG | backlog (nécessite VM/GPU) |
| P3 | Assertions statiques de config garde-fou (guardrail_enabled, force_internal_search) — inspiré red-team statique AC360 | Faible | Faible | benchmark §inspirations | backlog |
| P3 | Packaging démo « 1 commande » + dossier conformité souveraineté | Élevé (business) | Faible | objectif premium | backlog |

## Règles de décision

- **Corriger immédiatement** : tout P0 (secret, auth, fuite inter-client, crash) — *aucun ouvert actuellement*.
- **Reporter** : optimisations perf (POC scale OK), refactors de code stable.
- **Refuser** (anti-overengineering) : OBO/Entra dans actions (casse l'archi gateway-RBAC), Durable Functions (lock-in), OCR cloud (casse la souveraineté), toute dépendance lourde sans gain prouvé.
- **Validation humaine requise** : éval RAG LIVE (nécessite VM/Ollama allumés), choix d'un modèle GPU à function-calling, décisions de packaging/pricing.

## Inspirations du projet opposé (retenues)

- Readiness `/ready` distinct de `/health` (production-readiness).
- Red-team **statique** en complément du comportemental.
- Contrats de frontière explicites (déjà fait en docstring pour les fonctions 360).

## Critères d'arrêt de la boucle

On **continue** tant que : la chaîne 360 n'est pas finie ET exposée ; l'écart RAG n'est pas mesuré/atténué ; une feature métier à fort impact reste à livrer.
On **s'arrête** quand : plus de P0/P1 ; score ≥ 90 ; les prochaines actions sont marginales OU nécessitent une décision humaine (GPU, pricing, éval LIVE). **État actuel : proche du seuil d'arrêt — la valeur restante est surtout la finition de la chaîne 360 (boucle feature en cours) et l'écart RAG (partiellement hors contrôle, lié à Onyx 4.1.1).**

## Journal des itérations adaptatives

| Date | Découverte | Décision | Stratégie ajustée |
|---|---|---|---|
| 2026-06-25 | AC360 accessible sur disque ; onix égale AC360 sur sécu/gouvernance (rate-limit `security.py` « parité AC360 » déjà présent), le dépasse sur observabilité/tests/souveraineté ; seul écart réel = RAG. | Ne PAS fabriquer de patch sécurité (déjà couvert). Prioriser **différenciation métier (chaîne 360)** + **écart RAG**. Produire benchmark + stratégie (valeur business immédiate). | Poids RAG 12→20, métier 10→18, observabilité/tests ↓ (saturés). |
| 2026-06-28 | Tour 6 audit : `portfolio_360` SAIN sur 6 axes (borne 500, dédoublonnage, data-min, totaux, fail-safe par client, CSV anti-injection). Seul constat : perf O(clients×tâches) sur les défauts. | Doc-only (pas de faux fix : 2ᵉ fonction saine d'affilée → le temps FEATURE produit du bon code). Documenter la perf + l'échappatoire d'injection « batch ». | Inchangée (RAG + métier prioritaires) ; chaîne 360 → endpoint /portfolio/360 au tour 7. |
