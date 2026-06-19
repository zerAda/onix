# Requirements: onix — go-live mono-poste sécurisé

**Defined:** 2026-06-18
**Core Value:** La sécurité et la gouvernance doivent être *prouvables* à un auditeur (RBAC + ACL par-document + garde-fous déterministes + audit inviolable) — aucun cloisonnement franchi, aucune décision d'accès non journalisée.

## v1 Requirements

Périmètre engagé de ce cycle (go-live mono-poste, < 1 mois). Chaque exigence est mappée à une phase de la roadmap.

### Supply Chain / CVE (DEP)

- [ ] **DEP-01**: `cryptography` est relevé à un pin sans CVE (≥ 49.0.0) ; `pip-audit --strict` retourne 0 CVE
- [ ] **DEP-02**: `pypdf` est relevé à ≥ 6.13.3 ; `pip-audit --strict` reste vert
- [ ] **DEP-03**: l'outillage CI est rafraîchi (gitleaks ≥ 8.21, `anchore/sbom-action` épinglé à une version fixe) ; toutes les portes restent vertes

### Durcissement au démarrage prod (HARD)

- [ ] **HARD-01**: en mode production, le démarrage échoue immédiatement (fatal) si un identifiant par défaut/faible est détecté (`POSTGRES_PASSWORD=password`, MinIO `minioadmin`, mot de passe Redis faible)
- [ ] **HARD-02**: le preflight bloque le démarrage prod si secrets manquants, `vm.max_map_count < 262144`, disque sous seuil, ou daemon Docker absent
- [ ] **HARD-03**: la clé HMAC d'audit est exigée au preflight (présente, ≥ 32 caractères) — aucun repli silencieux vers SHA-256 sans clé
- [ ] **HARD-04**: l'ordre de démarrage par santé (`depends_on … service_healthy`) et `restart: always` sont validés dans un enregistrement d'acceptation reproductible

### Sauvegarde / Restauration (BKP)

- [ ] **BKP-01**: `make restore-drill` restaure la pile à un état vérifié sain à partir d'une sauvegarde (la restauration est exercée, pas seulement la sauvegarde)
- [ ] **BKP-02**: la sauvegarde effectue un arrêt ordonné et un `pg_dump` logique à chaud (pas de cluster Postgres corrompu)
- [ ] **BKP-03**: les archives de sauvegarde sont chiffrées au repos (AES-256) ; la clé provient de `.env`/`gen-secrets.sh` et n'est jamais stockée avec l'archive
- [ ] **BKP-04**: sauvegarde planifiée (timer systemd) + procédure de copie hors-machine documentée + politique de rétention/purge des archives

### Observabilité (OBS)

- [ ] **OBS-01**: `onix-actions` expose un endpoint `/metrics` (labels = templates de route uniquement, jamais de PII/valeurs utilisateur)
- [ ] **OBS-02**: la pile de monitoring (Prometheus/Grafana/Loki) est active par défaut pour `prod-local`
- [ ] **OBS-03**: une règle d'alerte se déclenche sur échec de synchronisation ACL (`onix_acl_sync_failures_total > 0` sur 5 min)
- [ ] **OBS-04**: une règle d'alerte se déclenche sur blocages garde-fou soutenus (tentative d'injection/exfil en cours)
- [ ] **OBS-05**: des alertes couvrent la rupture de chaîne d'audit et la pression disque/WAL Postgres

### Sécurité prouvable (SEC) — cœur de valeur

- [ ] **SEC-01**: un test d'intégration ACL SharePoint *live* (tenant non-prod) prouve que les citations sont filtrées après une révocation de permission réelle
- [ ] **SEC-02**: la suite red-team des garde-fous est ré-exécutée sur le modèle de production et passe
- [ ] **SEC-03**: démontrabilité de l'audit-trail — un script de démo + `make audit-verify` montrent une chaîne inviolable valide (`verify_chain() → ok: true`)
- [ ] **SEC-04**: l'intervalle de rafraîchissement ACL est fixé à ≤ 300 s en production
- [ ] **SEC-05**: le dossier de preuve sécurité mappe chaque menace → chemin de code mitigeant → test → porte CI, avec distinction FOSS-vs-EE explicite et risques résiduels acceptés/documentés

### Conformité RGPD (RGPD)

- [ ] **RGPD-01**: la procédure de droit à l'effacement est vérifiée contre des données réelles (PII supprimées de Postgres + historique de chat)
- [ ] **RGPD-02**: les TTL de rétention sont fixés à des valeurs numériques explicites par catégorie de données et documentés

### Prêt opérationnel (OPS)

- [ ] **OPS-01**: une checklist d'acceptation go-live (preflight → secrets → démarrage → santé → compte admin → test de restauration → monitoring → démo audit) est dans `docs/RUNBOOK.md`
- [ ] **OPS-02**: un rappel de chiffrement du disque hôte (BitLocker/LUKS) figure comme item de la checklist go-live

### Pipeline de release (CICD)

- [ ] **CICD-01**: un pipeline de release reproductible produit une image taguée, scannée (trivy) et signée (cosign keyless OIDC) avec SBOM, toutes portes vertes

## v2 Requirements

Reconnu mais différé — non inclus dans la roadmap de ce cycle (premier post-go-live ou milestone suivant).

### Durcissement upstream Onyx

- **SSRF-01**: router les appels Custom Tool d'Onyx via `ssrf_safe_get()` (validation d'URL complète, pas seulement l'hôte de base)
- **XSS-01**: validation MIME par magic-bytes (`puremagic`) sur l'upload d'avatar (anti stored-XSS)

### Conformité avancée

- **DPIA-01**: DPIA (AIPD) complétée et signée par le DPO (RGPD art.35 — traitement à risque élevé)

### Observabilité / ops

- **OBS-PROMTAIL-01**: migration Promtail → Grafana Alloy (Promtail EOL février 2026)
- **ACLRT-01**: synchronisation ACL temps réel par webhook SharePoint (réduit la fenêtre de staleness sous la seconde)

## Out of Scope

Exclusions explicites (anti-features) — documentées pour empêcher la dérive de périmètre.

| Feature | Reason |
|---------|--------|
| AKS / Kubernetes HA | Décision explicite : cible = machine unique ; le chart Helm existe mais hors cycle |
| SAML SSO | OIDC/Entra suffit en interne ; nouvelle surface d'auth + risque planning sur < 1 mois |
| Admin UI self-service | Non nécessaire pour prouver la sécurité ; CLI/config suffit au go-live |
| Nouveaux connecteurs | SharePoint couvre le périmètre documentaire du go-live ; nouvelle surface ACL à tester |
| Multi-tenancy FOSS | Exclu par conception ; isolation = instances séparées ; client interne unique |
| Cloud LLM / API externe | Contraire à la souveraineté ; tout reste local (Ollama) |
| Chiffrement secrets at-rest Postgres | Fonction Onyx EE ; mitigé en FOSS par chiffrement disque hôte + durcissement réseau/DB |
| Trimming ACL par-document à la récupération | Fonction Onyx EE/Cloud ; mitigé par filtre de sortie passerelle (déjà implémenté) |
| Certification SOC 2 / ISO 27001 | Objectif = « défendable devant un auditeur », pas certification formelle ce cycle |

## Traceability

Mappage phase ↔ exigence. Rempli à la création de la roadmap (2026-06-19).

| Requirement | Phase | Status |
|-------------|-------|--------|
| DEP-01 | Phase 1 | Pending |
| DEP-02 | Phase 1 | Pending |
| DEP-03 | Phase 1 | Pending |
| HARD-01 | Phase 1 | Pending |
| HARD-02 | Phase 1 | Pending |
| HARD-03 | Phase 1 | Pending |
| HARD-04 | Phase 1 | Pending |
| OBS-01 | Phase 2 | Pending |
| OBS-02 | Phase 2 | Pending |
| OBS-03 | Phase 2 | Pending |
| OBS-04 | Phase 2 | Pending |
| OBS-05 | Phase 2 | Pending |
| SEC-04 | Phase 2 | Pending |
| BKP-01 | Phase 3 | Pending |
| BKP-02 | Phase 3 | Pending |
| BKP-03 | Phase 3 | Pending |
| BKP-04 | Phase 3 | Pending |
| OPS-01 | Phase 4 | Pending |
| OPS-02 | Phase 4 | Pending |
| CICD-01 | Phase 4 | Pending |
| SEC-01 | Phase 5 | Pending |
| SEC-02 | Phase 5 | Pending |
| SEC-03 | Phase 5 | Pending |
| SEC-05 | Phase 5 | Pending |
| RGPD-01 | Phase 5 | Pending |
| RGPD-02 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 26 total
- Mapped to phases: 26 ✓
- Unmapped: 0 ✓

---
*Requirements defined: 2026-06-18*
*Last updated: 2026-06-19 — traceability filled after roadmap creation*
