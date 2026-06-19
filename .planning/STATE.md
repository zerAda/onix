# Project State: onix — go-live mono-poste sécurisé

---

## Project Reference

**Core Value:** La sécurité et la gouvernance doivent être *prouvables* à un auditeur (RBAC + ACL par-document + garde-fous déterministes + audit inviolable) — aucun cloisonnement franchi, aucune décision d'accès non journalisée.

**Current Focus:** Phase 1 — CVE Remediation + Credential & Preflight Guard

---

## Current Position

| Field | Value |
|-------|-------|
| Phase | 1 |
| Phase Name | CVE Remediation + Credential & Preflight Guard |
| Plan | None (planning not yet started) |
| Status | Not started |
| Milestone | onix go-live mono-poste sécurisé |

**Progress:** `[ ] [ ] [ ] [ ] [ ]` — 0/5 phases complete

```
Phase 1: CVE Remediation + Credential & Preflight Guard  ← CURRENT
Phase 2: Observabilite Completion + Alerting
Phase 3: Backup / Restore Hardening
Phase 4: systemd Boot Framing + Runbook
Phase 5: Security Proof — ACL Live Test + Audit Trail + RGPD Evidence
```

---

## Performance Metrics

| Metric | Value |
|--------|-------|
| Phases total | 5 |
| Phases complete | 0 |
| Requirements total | 26 |
| Requirements complete | 0 |
| Plans created | 0 |
| Plans complete | 0 |

---

## Accumulated Context

### Key Decisions Logged

| Decision | Rationale |
|----------|-----------|
| Phase ordering: CVE first | Gate pip-audit cassé = tout test ultérieur suspect |
| OBS avant BKP | Les alertes ACL-sync / garde-fou génèrent des preuves opérationnelles pour les phases 3–5 |
| SEC-04 (ACL refresh 300s) en Phase 2 | Dépendance observabilité : l'alerte ACL-sync est sans valeur si l'intervalle est 3600s |
| CICD-01 en Phase 4 | La release pipeline se raccorde naturellement au runbook go-live et aux artefacts de boot systemd |
| Phase 5 = agrégateur terminal | Ne peut être assemblée qu'après que toutes les preuves amont (CVE vert, ACL live, audit-trail, effacement) sont produites |

### External Dependencies (Escalade requise)

- **SEC-01 — SharePoint ACL live test** : requiert un tenant Azure/SharePoint non-production. Seul item ne pouvant s'exécuter en isolation. **Escalader en Jour 1 de Phase 5.** Si bloqué > 2–3 jours = bloqueur go-live.
- **SEC-02 — Red-team prod model** : requiert que le modèle de production soit pullé sur la machine cible avant l'exécution de `make rag-test-live`.

### Known Constraints

- Timeline : < 1 mois (go-live visé)
- Machine unique Docker Compose — pas de Kubernetes ce cycle
- Souveraineté : inférence 100 % locale, aucun appel cloud, `DISABLE_TELEMETRY=true`
- FOSS vs EE : distinguer explicitement dans tout artefact de preuve
- Qualité : `make test` doit rester vert en permanence

### Todos Across Phases

- [ ] Vérifier identité de commit Git (`a.zeriri@gerep.fr`) avant tout commit de phase
- [ ] Synchroniser `main` local avec `origin/main` (en retard de ~73 commits, fast-forward possible) avant Phase 1
- [ ] Escalader accès tenant non-prod SharePoint dès le début de Phase 5
- [ ] Planifier migration Promtail → Grafana Alloy pour le prochain milestone (Promtail EOL févr. 2026)

### Blockers

Aucun bloqueur actif. Dépendance externe SEC-01 à escalader en temps voulu.

---

## Session Continuity

**Last updated:** 2026-06-19 (roadmap creation)
**Next action:** `/gsd-plan-phase 1` — décomposer Phase 1 en tâches exécutables

### Resume Context

Roadmap créée à partir de :
- `.planning/PROJECT.md` — périmètre, contraintes, hors-scope
- `.planning/REQUIREMENTS.md` — 26 exigences v1 (DEP/HARD/BKP/OBS/SEC/RGPD/OPS/CICD)
- `.planning/research/SUMMARY.md` + `FEATURES.md` — structure 5 phases confirmée, ordre de build validé

Structure de phases adoptée telle que recommandée par la recherche (SUMMARY.md §Implications for Roadmap), avec SEC-04 déplacé en Phase 2 (dépendance observabilité naturelle) et CICD-01 en Phase 4 (raccordement runbook go-live).

---

*State initialized: 2026-06-19*
