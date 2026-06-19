# Roadmap: onix — go-live mono-poste sécurisé

**Milestone:** Production hardening & provable security go-live
**Timeline:** < 1 mois
**Granularity:** Standard
**Coverage:** 26/26 requirements mapped

---

## Phases

- [ ] **Phase 1: CVE Remediation + Credential & Preflight Guard** - Corrige les CVE corrigeables, bloque les identifiants par défaut et valide le preflight prod — les portes CI doivent être vertes avant que tout test ultérieur ait de la valeur
- [ ] **Phase 2: Observabilite Completion + Alerting** - Complète l'endpoint `/metrics` manquant, active le monitoring par défaut pour prod-local et arme les règles d'alerte ACL-sync / garde-fou / audit — génère des preuves opérationnelles pour les phases suivantes
- [ ] **Phase 3: Backup / Restore Hardening** - Livre un cycle de sauvegarde+restauration vérifié, chiffré AES-256 et planifié — la restauration est exercée, pas seulement documentée
- [ ] **Phase 4: systemd Boot Framing + Runbook** - Ancre le démarrage automatique au boot via systemd, intègre les timers de sauvegarde et produit la checklist d'acceptation go-live dans `docs/RUNBOOK.md`
- [ ] **Phase 5: Security Proof — ACL Live Test + Audit Trail + RGPD Evidence** - Agrège toutes les preuves en un dossier de sécurité auditable : test ACL SharePoint sur tenant réel, red-team sur le modèle de production, démonstration de l'audit-trail, effacement RGPD vérifié et dossier de preuve final

---

## Phase Details

### Phase 1: CVE Remediation + Credential & Preflight Guard
**Goal**: Les portes qualité CI sont vertes (0 CVE) et le démarrage production est bloqué immédiatement si des identifiants par défaut ou des préconditions manquantes sont détectées
**Depends on**: Rien (premier livrable, débloque toutes les phases suivantes)
**Requirements**: DEP-01, DEP-02, DEP-03, HARD-01, HARD-02, HARD-03, HARD-04
**Success Criteria** (what must be TRUE):
  1. `pip-audit --strict` retourne 0 CVE sur toutes les images — `cryptography >= 49.0.0` et `pypdf >= 6.13.3` sont épinglés
  2. `make up-local-prod` échoue immédiatement (exit non-nul, message fatal) si `POSTGRES_PASSWORD=password`, `MINIO_ROOT_PASSWORD=minioadmin` ou un mot de passe Redis faible est détecté
  3. Le preflight bloque le démarrage si `ONIX_AUDIT_HMAC_KEY` est absent ou fait moins de 32 caractères — aucun repli silencieux sur SHA-256 sans clé
  4. Le preflight bloque si `vm.max_map_count < 262144`, disque sous le seuil, ou daemon Docker absent
  5. `make test` reste vert (pytest + bandit + gitleaks + trivy + helm lint + compose config) après toutes les mises à jour de version
**Plans**: TBD

### Phase 2: Observabilite Completion + Alerting
**Goal**: La pile d'observabilité est opérationnelle par défaut pour prod-local, avec des alertes actives couvrant les pannes ACL-sync, les blocs garde-fou, les ruptures d'audit-trail et la pression disque/WAL
**Depends on**: Phase 1 (preflight vert, clé HMAC validée, gate pip-audit vert)
**Requirements**: OBS-01, OBS-02, OBS-03, OBS-04, OBS-05, SEC-04
**Success Criteria** (what must be TRUE):
  1. `GET /metrics` sur `onix-actions` retourne des métriques Prometheus valides avec des labels de route uniquement — aucune valeur utilisateur, aucun PII dans les labels
  2. Prometheus scrape `onix-actions` et `access-gateway` sans erreur ; Grafana affiche les dashboards au démarrage de `make up-local-prod`
  3. Une alerte se déclenche et est visible dans Grafana/Alertmanager quand `onix_acl_sync_failures_total > 0` pendant 5 minutes
  4. Une alerte se déclenche sur des blocs garde-fou soutenus (tentative d'injection/exfil détectée) et sur rupture de chaîne d'audit
  5. `GATEWAY_DOC_ACL_REFRESH_SECONDS=300` est fixé dans le `.env` prod — la staleness ACL est bornée à 5 minutes maximum
**Plans**: TBD

### Phase 3: Backup / Restore Hardening
**Goal**: La pile peut être restaurée à partir d'une sauvegarde chiffrée dans un état vérifié sain — la restauration est un acte démontrable, pas une assertion
**Depends on**: Phase 1 (`BACKUP_ENCRYPTION_KEY` généré par `gen-secrets.sh` et validé au preflight), Phase 2 (monitoring opérationnel pour observer la santé post-restauration)
**Requirements**: BKP-01, BKP-02, BKP-03, BKP-04
**Success Criteria** (what must be TRUE):
  1. `make restore-drill` ramène la pile à l'état sain et `make verify` retourne OK — la restauration est un test reproductible, pas une procédure manuelle
  2. `scripts/backup.sh` effectue un arrêt ordonné des services puis un `pg_dump --format=custom` à chaud — aucun volume Postgres tarré à chaud qui pourrait produire un cluster corrompu
  3. Chaque archive de sauvegarde est chiffrée AES-256-CBC avec la clé de `gen-secrets.sh` — la clé n'est jamais stockée dans le même répertoire que l'archive
  4. Un timer systemd (ou cron) déclenche la sauvegarde quotidiennement ; la procédure de copie hors-machine (NAS/USB chiffré) et la politique de rétention/purge sont documentées dans `docs/RUNBOOK.md`
**Plans**: TBD

### Phase 4: systemd Boot Framing + Runbook
**Goal**: La pile démarre automatiquement au boot via systemd, les sauvegardes sont planifiées via timer, et un opérateur peut conduire le go-live en suivant la checklist `docs/RUNBOOK.md` sans connaissance implicite
**Depends on**: Phase 1 (preflight script stable), Phase 3 (backup script stable et `make restore-drill` fonctionnel)
**Requirements**: OPS-01, OPS-02, CICD-01
**Success Criteria** (what must be TRUE):
  1. Après un redémarrage machine, `onix.service` repart automatiquement et `make verify` retourne OK sans intervention manuelle
  2. Le timer systemd de sauvegarde est actif (`systemctl is-active onix-backup.timer` retourne `active`) et le prochain déclenchement est dans les 24 heures
  3. `docs/RUNBOOK.md` contient une checklist go-live séquentielle (preflight → secrets → démarrage → healthcheck → compte admin → restore-drill → monitoring → démo audit) qu'un nouvel opérateur peut suivre du début à la fin, avec un rappel explicite de chiffrement du disque hôte (BitLocker/LUKS)
  4. Un pipeline de release reproductible (`cd.yml`) produit une image taguée, scannée trivy et signée cosign keyless avec SBOM — toutes les portes qualité vertes sont requises pour le tag
**Plans**: TBD

### Phase 5: Security Proof — ACL Live Test + Audit Trail + RGPD Evidence
**Goal**: La sécurité et la conformité sont prouvables à un auditeur externe : chaque menace est mappée à un chemin de code → test → porte CI, le cloisonnement ACL est vérifié contre un tenant réel, l'audit-trail est démontrable en direct et les droits RGPD sont exercés
**Depends on**: Phases 1–4 complètes (toutes les preuves amont sont produites avant que le dossier puisse être assemblé)
**Requirements**: SEC-01, SEC-02, SEC-03, SEC-05, RGPD-01, RGPD-02

> **Dependance externe (SEC-01) :** Le test ACL SharePoint live requiert l'accès à un tenant Azure/SharePoint non-production. Cette dependance est le seul item qui ne peut pas s'exécuter en isolation. **Escalader l'obtention de l'accès tenant en Jour 1.** Si bloqué > 2–3 jours, c'est un bloqueur go-live, pas une tache à reporter.

**Success Criteria** (what must be TRUE):
  1. Un test d'intégration ACL SharePoint s'exécute contre un tenant non-production réel : une révocation de permission dans SharePoint se traduit par un filtrage des citations dans la réponse API dans les 300 secondes — comportement attesté par log et trace de test
  2. `make rag-test-live` passe sur le modèle de production (cible : 21/21 garde-fous) — la couverture des garde-fous est vérifiée sur le modèle réellement déployé, pas seulement sur `qwen2.5:7b`
  3. Un auditeur peut exécuter `make audit-verify` et observer `verify_chain() → ok: true` avec des entrées de log réelles — la chaîne HMAC est démontrable en direct, pas seulement affirmée
  4. La procédure d'effacement RGPD est exercée sur des données PII réelles : après exécution du script d'effacement, les données PII sont absentes de Postgres et de l'historique de chat — résultat attesté par requête SQL de vérification
  5. Les TTL de rétention sont fixés à des valeurs numériques explicites par catégorie de données dans `.env` et documentés — "configurable" n'est pas une réponse de conformité
  6. Le dossier de preuve sécurité (`docs/SECURITY_PROOF.md`) mappe chaque menace → chemin de code mitigeant → test → porte CI, avec distinction FOSS-vs-EE explicite et risques résiduels acceptés documentés
**Plans**: TBD

---

## Progress Table

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. CVE Remediation + Credential & Preflight Guard | 0/? | Not started | - |
| 2. Observabilite Completion + Alerting | 0/? | Not started | - |
| 3. Backup / Restore Hardening | 0/? | Not started | - |
| 4. systemd Boot Framing + Runbook | 0/? | Not started | - |
| 5. Security Proof — ACL Live Test + Audit Trail + RGPD Evidence | 0/? | Not started | - |

---

## Coverage Map

| Requirement | Phase |
|-------------|-------|
| DEP-01 | Phase 1 |
| DEP-02 | Phase 1 |
| DEP-03 | Phase 1 |
| HARD-01 | Phase 1 |
| HARD-02 | Phase 1 |
| HARD-03 | Phase 1 |
| HARD-04 | Phase 1 |
| OBS-01 | Phase 2 |
| OBS-02 | Phase 2 |
| OBS-03 | Phase 2 |
| OBS-04 | Phase 2 |
| OBS-05 | Phase 2 |
| SEC-04 | Phase 2 |
| BKP-01 | Phase 3 |
| BKP-02 | Phase 3 |
| BKP-03 | Phase 3 |
| BKP-04 | Phase 3 |
| OPS-01 | Phase 4 |
| OPS-02 | Phase 4 |
| CICD-01 | Phase 4 |
| SEC-01 | Phase 5 |
| SEC-02 | Phase 5 |
| SEC-03 | Phase 5 |
| SEC-05 | Phase 5 |
| RGPD-01 | Phase 5 |
| RGPD-02 | Phase 5 |

**Total v1 requirements mapped: 26/26** ✓

---

*Roadmap created: 2026-06-19*
*Milestone: onix go-live mono-poste sécurisé*
