# Roadmap: onix — go-live mono-poste sécurisé

**Milestone:** Production hardening & provable security go-live
**Timeline:** < 1 mois
**Granularity:** Standard
**Re-baselined:** 2026-06-19 (post-sync audit — 23 reqs restants ; 3 déjà faits : DEP-02, OBS-01, SEC-04)

> **Re-scope.** L'infrastructure existe ; chaque phase passe de « construire » à **finir / câbler / prouver**. Items marqués 🐳 = vérification dépend de Docker (indisponible sur cette machine) ; 🔑 = dépend d'un tenant Azure live ; 🤖 = dépend du modèle de production.

---

## Phases

- [ ] **Phase 1: CVE upstream + Gardes de démarrage prod** — Câbler la garde anti-credentials, l'exigence de clé HMAC, et le gate preflight sur le chemin prod ; traiter le CVE cryptography upstream ; finir l'hygiène CI (gitleaks/sbom-action)
- [ ] **Phase 2: Observabilité par défaut + Alerting** — Monitoring actif par défaut pour prod-local ; ajouter les règles d'alerte manquantes (ACL-sync, garde-fou soutenu, rupture d'audit, WAL) + métriques sous-jacentes
- [ ] **Phase 3: Sauvegarde/Restauration durcie** — `pg_dump` à chaud (fin du tar à froid), chiffrement AES-256 des archives, `make restore-drill`, planification systemd + rétention
- [ ] **Phase 4: systemd Boot + Runbook + Release** — Timers de sauvegarde, checklist d'acceptation go-live dans RUNBOOK, signature cosign keyless dans le pipeline release
- [ ] **Phase 5: Dossier de preuve sécurité** — Révocation ACL live (SharePoint+Fabric), red-team sur modèle prod, `make audit-verify` + démo, effacement RGPD côté Onyx, dossier de preuve agrégé

---

## Phase Details

### Phase 1: CVE upstream + Gardes de démarrage prod
**Goal**: Le démarrage prod est fail-closed sur identifiants faibles / clé HMAC manquante / préconditions manquantes, et le CVE cryptography upstream est traité ou documenté.
**Depends on**: Rien (premier livrable)
**Requirements**: DEP-01 (reformulé), DEP-03, HARD-01, HARD-02, HARD-03, HARD-04
**Success Criteria**:
  1. ⬜ HARD-01 — `make up-*prod` échoue fatalement si `POSTGRES_PASSWORD=password`, MinIO `minioadmin`, ou Redis faible
  2. ⬜ HARD-03 — preflight prod exige `ONIX_ACTIONS_AUDIT_HMAC_KEY` (≥ 32 chars) ; le repli SHA-256 sans clé est supprimé ou rendu fatal en prod
  3. 🟡 HARD-02 — les 4 contrôles preflight (secrets/max_map_count/disque/daemon) deviennent un prérequis bloquant du démarrage prod
  4. 🟡 DEP-03 — `gitleaks` ≥ 8.21 partout, `anchore/sbom-action` épinglé ; gates verts
  5. ⬜ DEP-01 — image Onyx backend épinglée + scannée trivy, **ou** CVE upstream documenté comme risque résiduel accepté (→ SEC-05)
  6. 🐳 HARD-04 — enregistrement d'acceptation runtime de l'ordre healthcheck + `restart: always` (à produire quand Docker dispo)
**Plans**: TBD

### Phase 2: Observabilité par défaut + Alerting
**Goal**: La pile d'observabilité tourne par défaut en prod-local et alerte sur les pannes ACL-sync, garde-fous soutenus, ruptures d'audit et pression WAL.
**Depends on**: Phase 1 (clé HMAC validée pour la métrique d'intégrité d'audit)
**Requirements**: OBS-02, OBS-03, OBS-04, OBS-05
**Success Criteria**:
  1. ⬜ OBS-02 — monitoring inclus dans `up-local-prod` (plus de `make monitor-up` séparé)
  2. ⬜ OBS-03 — compteur `onix_acl_sync_failures_total` (incluant Fabric) + alerte 5 min
  3. 🟡 OBS-04 — règle d'alerte sur `onix_gateway_guardrail_total{blocked="true"}` soutenu (métrique déjà présente)
  4. ⬜ OBS-05 — métrique `onix_audit_chain_ok` + alerte de rupture ; alerte pression disque/WAL Postgres ciblée
**Plans**: TBD

### Phase 3: Sauvegarde / Restauration durcie
**Goal**: La sauvegarde est correcte (pg_dump), chiffrée, planifiée, et la restauration est un acte démontrable.
**Depends on**: Phase 1 (`BACKUP_ENCRYPTION_KEY` via `gen-secrets.sh`)
**Requirements**: BKP-01, BKP-02, BKP-03, BKP-04
**Success Criteria**:
  1. 🟡 BKP-02 — `backup.sh` fait un `pg_dump` logique (fin du tar à froid du volume) ; doc audit-reality corrigée
  2. ⬜ BKP-03 — archives chiffrées AES-256, clé via `gen-secrets.sh`, jamais avec l'archive
  3. 🟡🐳 BKP-01 — cible `make restore-drill` restaure vers un état vérifié sain (assertion santé gateante)
  4. 🟡 BKP-04 — timer systemd installable + copie hors-machine documentée + politique de rétention/purge
**Plans**: TBD

### Phase 4: systemd Boot + Runbook + Release
**Goal**: Démarrage planifié des sauvegardes, checklist go-live opérable, et release signée.
**Depends on**: Phase 1 (preflight stable), Phase 3 (script backup stable)
**Requirements**: OPS-01, OPS-02, CICD-01
**Success Criteria**:
  1. ⬜ OPS-01 — checklist d'acceptation go-live ordonnée dans `docs/RUNBOOK.md`
  2. 🟡 OPS-02 — rappel chiffrement disque hôte porté comme item de cette checklist
  3. 🟡 CICD-01 — signature cosign keyless OIDC (+ attestation SBOM) ajoutée à `cd.yml` ; gates verts
**Plans**: TBD

### Phase 5: Dossier de preuve sécurité
**Goal**: La sécurité/conformité est prouvable à un auditeur : révocation ACL démontrée, audit démontrable, RGPD exercé, dossier agrégé.
**Depends on**: Phases 1–4 (preuves amont produites)
**Requirements**: SEC-01, SEC-02, SEC-03, SEC-05, RGPD-01, RGPD-02

> **Dépendances externes :** 🔑 SEC-01 (tenant Azure/SharePoint+Fabric non-prod) — escalader Jour 1. 🤖 SEC-02 (modèle de production pullé). 🐳 exécution live générale.

**Success Criteria**:
  1. 🟡🔑 SEC-01 — scénario de **révocation** live (SharePoint + Fabric) prouvant la disparition de la citation ≤ 300 s, run enregistré contre un tenant non-prod
  2. 🟡🤖 SEC-02 — red-team re-jouable et **enregistré** sur le modèle de production (pas un transcript statique)
  3. ⬜ SEC-03 — cible `make audit-verify` + script de démo montrant `verify_chain() → ok: true`
  4. 🟡 RGPD-01 — effacement sujet ciblé **côté Onyx Postgres** (chat/comptes) outillé et vérifié (l'effacement actions/S3 est déjà prouvé)
  5. 🟡 RGPD-02 — TTL numériques explicites pour toutes les catégories (Onyx chat/index, journal d'audit), pas seulement actions
  6. ⬜ SEC-05 — `docs/SECURITY_PROOF.md` : menace → code → test → porte CI, FOSS-vs-EE explicite, risques résiduels (dont DEP-01 upstream)
**Plans**: TBD

---

## Progress Table

| Phase | Reqs restants | Status | Completed |
|-------|----------------|--------|-----------|
| 1. CVE upstream + Gardes démarrage | 6 (2🟡 / 3⬜ / 1🐳) | Not started | - |
| 2. Observabilité par défaut + Alerting | 4 (1🟡 / 3⬜) | Not started | - |
| 3. Sauvegarde/Restauration durcie | 4 (3🟡 / 1⬜) | Not started | - |
| 4. systemd Boot + Runbook + Release | 3 (2🟡 / 1⬜) | Not started | - |
| 5. Dossier de preuve sécurité | 6 (4🟡 / 2⬜) | Not started | - |

**Déjà satisfait (hors phases) :** DEP-02 ✅, OBS-01 ✅, SEC-04 ✅

---

## Coverage Map

| Requirement | Phase |
|-------------|-------|
| DEP-01, DEP-03, HARD-01, HARD-02, HARD-03, HARD-04 | Phase 1 |
| OBS-02, OBS-03, OBS-04, OBS-05 | Phase 2 |
| BKP-01, BKP-02, BKP-03, BKP-04 | Phase 3 |
| OPS-01, OPS-02, CICD-01 | Phase 4 |
| SEC-01, SEC-02, SEC-03, SEC-05, RGPD-01, RGPD-02 | Phase 5 |
| DEP-02, OBS-01, SEC-04 | ✅ Done (closed) |

**Total : 23 reqs restants mappés + 3 faits = 26** ✓

---

*Roadmap created: 2026-06-18 · Re-baselined: 2026-06-19 after origin sync*
*Milestone: onix go-live mono-poste sécurisé*
