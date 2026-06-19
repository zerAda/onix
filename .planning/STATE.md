# Project State: onix — go-live mono-poste sécurisé

---

## Project Reference

**Core Value:** La sécurité et la gouvernance doivent être *prouvables* à un auditeur (RBAC + ACL par-document + garde-fous déterministes + audit inviolable).

**Current Focus:** Phase 1 — CVE upstream + Gardes de démarrage prod (re-scopé post-sync)

---

## Current Position

| Field | Value |
|-------|-------|
| Phase | 1 |
| Phase Name | CVE upstream + Gardes de démarrage prod |
| Plan | None (planning not yet started) |
| Status | Re-baselined — ready for plan/execute decision |
| Milestone | onix go-live mono-poste sécurisé (v1.0) |

**Progress:** `[ ] [ ] [ ] [ ] [ ]` — 0/5 phases ; 3/26 reqs déjà satisfaits (DEP-02, OBS-01, SEC-04)

```
Phase 1: CVE upstream + Gardes de démarrage prod   ← CURRENT
Phase 2: Observabilité par défaut + Alerting
Phase 3: Sauvegarde/Restauration durcie
Phase 4: systemd Boot + Runbook + Release
Phase 5: Dossier de preuve sécurité
```

---

## Performance Metrics

| Metric | Value |
|--------|-------|
| Phases total | 5 |
| Phases complete | 0 |
| Requirements total | 26 |
| Requirements done | 3 (✅ DEP-02, OBS-01, SEC-04) |
| Requirements partial | 13 |
| Requirements open | 10 |
| Plans created | 0 |

---

## Accumulated Context

### Re-baseline (2026-06-19)

Le dépôt local était **73 commits en retard** sur `origin/main` (POC initial bâti sur un snapshot périmé). Actions menées :
1. **Sync** — rebase des 6 commits `.planning/` sur `origin/main` (sans conflit ; `.planning/` non touché par origin).
2. **Re-map** — `.planning/codebase/` régénéré contre le vrai arbre (nouveau : module **Fabric**, boucle **Ralph**, refonte monitoring, `preflight-prod.sh`).
3. **Audit re-baseline** — les 26 reqs v1 vérifiées contre le code synchronisé : **3 DONE, 13 PARTIAL, 10 OPEN**. ROADMAP/REQUIREMENTS re-scopés en « finir/câbler/prouver ».

### Key Decisions Logged

| Decision | Rationale |
|----------|-----------|
| Re-baseline avant toute exécution | Le POC était sur une base périmée de 73 commits ; exécuter aurait dupliqué des PR fusionnées |
| DEP-01 reformulé | `cryptography` absent de la couche onix ; le vrai CVE est dans l'image Onyx upstream (à épingler/scanner ou accepter en risque résiduel) |
| Phase order conservé (1→5) | Toujours valide ; chaque phase passe de « construire » à « finir/prouver » |

### External Dependencies & Environment Gaps

- 🔑 **SEC-01** — tenant Azure/SharePoint+Fabric non-prod (révocation live). Escalader Jour 1 de Phase 5.
- 🤖 **SEC-02** — modèle de production pullé pour le red-team.
- 🐳 **Docker indisponible sur la machine de planification** — les critères dépendant du runtime (HARD-04 acceptance, BKP-01 restore-drill, exécutions live) ne peuvent pas être *prouvés* ici, seulement codés.

### Coordination / Risks

- 🤖 **Boucle Ralph active** (`ralph/loop.sh`, `ralph/ORCHESTRATION.md`) — `origin` est développé par un exécuteur autonome. **Ne pas lancer `/gsd-autonomous` en parallèle** sans coordination (scopes disjoints / pause Ralph), sinon conflits.
- ⚠️ **Mensonge doc↔code** — `docs/audit-reality/deploy-ops.md:58` prétend « pg_dump à chaud ✅ » alors que `backup.sh` fait un tar à froid du volume. À corriger (BKP-02) — viole « zéro mock présenté comme réel ».

### Known Constraints

- Timeline : < 1 mois. Machine unique Docker Compose (pas de K8s). Souveraineté : LLM 100 % local, télémétrie OFF.
- `make test` doit rester vert. FOSS vs EE : distinguer explicitement.

### Todos Across Phases

- [x] ~~Synchroniser `main` local avec `origin/main`~~ — fait (rebase, 2026-06-19)
- [ ] Décider : exécuter les phases ici (sans vérif Docker) vs. déléguer à la boucle Ralph vs. exécuter sur une machine Docker
- [ ] Escalader accès tenant non-prod SharePoint+Fabric (Phase 5)
- [ ] Corriger le mensonge doc↔code pg_dump (BKP-02) + `update-scope-docs` deploy-ops
- [ ] Confirmer identité de commit Git (`a.zeriri@gerep.fr`)

### Blockers

Aucun bloqueur dur. Décision en attente : modalité d'exécution (cf. todos).

---

## Session Continuity

**Last updated:** 2026-06-19 (re-baseline post-sync)
**Next action:** Décision utilisateur sur la modalité d'exécution, puis `/gsd-plan-phase 1` (ou délégation Ralph).

### Resume Context

Re-baseline complet : dépôt synchronisé sur `origin/main`, carte de code rafraîchie, 26 reqs auditées (3 faites). ROADMAP.md + REQUIREMENTS.md reflètent le travail réellement restant (23 reqs : 13 partiels, 10 ouverts). Exécution non démarrée — en attente de décision sur la modalité (Docker/Ralph).

---

*State re-baselined: 2026-06-19*
