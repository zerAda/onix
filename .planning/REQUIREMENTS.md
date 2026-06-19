# Requirements: onix — go-live mono-poste sécurisé

**Defined:** 2026-06-18
**Re-baselined:** 2026-06-19 — audité contre `origin/main` après synchronisation (73 commits fusionnés). Verdicts : **3 DONE · 13 PARTIAL · 10 OPEN**.
**Core Value:** La sécurité et la gouvernance doivent être *prouvables* à un auditeur (RBAC + ACL par-document + garde-fous déterministes + audit inviolable) — aucun cloisonnement franchi, aucune décision d'accès non journalisée.

> **Note de re-baseline.** La couche d'infrastructure existe déjà (stack monitoring, harnais e2e live SharePoint+Fabric, couche d'effacement RGPD, pipeline CI trivy+SBOM, scripts preflight). Le travail restant est de **finir / câbler / prouver** — pas de construire. Légende : ✅ fait · 🟡 partiel · ⬜ ouvert.

## Déjà satisfait (validé contre le code synchronisé, 2026-06-19)

- ✅ **DEP-02** : `pypdf==6.13.3` épinglé (`actions/requirements.txt:18`, commit `d0cf93f`) ; `pip-audit --strict` bloquant et vert.
- ✅ **OBS-01** : `onix-actions` expose `GET /metrics` (`actions/app/main.py:359-372`), labels = templates de route via `_metrics_mw` (`main.py:149-160`), zéro PII ; scrapé par Prometheus.
- ✅ **SEC-04** : `GATEWAY_GROUP_CACHE_TTL=300` en prod (`deploy/prod/env.prod.template:132`, défaut `config.py:243`) — rafraîchissement ACL de groupe ≤ 300 s.

## v1 Requirements — restant (23)

Périmètre engagé restant pour le go-live mono-poste. Chaque item indique son **statut** et le **delta** réellement à faire.

### Supply Chain / CVE (DEP)

- ⬜ **DEP-01** *(reformulé)* : `cryptography` **n'est pas** une dépendance de la couche onix (ni directe ni transitive — `PyJWT==2.13.0` sans extra `[crypto]`). Le CVE réel (cryptography 46.0.7, GHSA-537c-gmf6-5ccf, CVSS 7.5) vit **uniquement dans l'image Onyx upstream** (`onyx-backend`), non bâtie depuis nos requirements et **non scannée** par le trivy onix. *Reste à faire* : épingler l'image Onyx backend à un tag corrigé **et** ajouter un scan trivy de cette image, **ou** documenter le CVE upstream comme risque résiduel accepté dans le dossier de preuve (cf. SEC-05). L'ancienne formulation « épingler cryptography≥49 dans onix » est un no-op.
- 🟡 **DEP-03** : bandit étendu à `scripts/` ✅ (`ci.yml:144`). *Reste à faire* : bump `gitleaks` v8.18.2 → ≥ 8.21 (`ci.yml:68`, `.pre-commit-config.yaml:14`, `Makefile:452`) ; épingler `anchore/sbom-action` `@v0` → version/SHA fixe (`cd.yml:124,130`).

### Durcissement au démarrage prod (HARD)

- ⬜ **HARD-01** : aucune garde anti-credentials par défaut. *Reste à faire* : détecter en mode prod les valeurs bannies (`POSTGRES_PASSWORD=password`, MinIO `minioadmin`, Redis faible) et échouer fatalement avant montage de la pile (à câbler dans preflight-prod / entrypoint).
- 🟡 **HARD-02** : `scripts/preflight-local.sh` vérifie déjà secrets manquants / `vm.max_map_count` / disque / daemon (exit 1). *Reste à faire* : ces 4 contrôles ne sont **pas** câblés sur le chemin de démarrage prod (`up-prod`/`up-local-prod`) ; `preflight-prod.sh` ne couvre que TLS/OIDC/email. Câbler le gate sur le démarrage prod (ou fusionner dans `preflight-prod.sh`).
- ⬜ **HARD-03** : `actions/app/audit_log.py:139-145` conserve le repli silencieux SHA-256 sans clé (warning, jamais fatal). *Reste à faire* : exiger `ONIX_ACTIONS_AUDIT_HMAC_KEY` (présente, ≥ 32 chars) au preflight prod **et/ou** rendre le chemin fail-closed en prod.
- 🟡 **HARD-04** : câblage compose présent (`docker-compose.prod-local.yml` : `depends_on: service_healthy` + `restart: always`). *Reste à faire* : aucun **enregistrement d'acceptation runtime reproductible** pour la pile compose (le seul existant, `docs/HA_ACCEPTANCE.md`, couvre K8s). **Vérification Docker-dépendante.**

### Sauvegarde / Restauration (BKP)

- 🟡 **BKP-01** : restauration manuelle existante (`make restore` → `scripts/restore.sh`). *Reste à faire* : pas de cible `make restore-drill` ni d'assertion de santé post-restauration automatisée gateant le succès. **Exécution Docker-dépendante.**
- 🟡 **BKP-02** : arrêt ordonné fait (`backup.sh:48-49`). *Reste à faire* : la sauvegarde fait un **tar à froid du volume Postgres** (`backup.sh:51-55`), **pas** un `pg_dump` — l'anti-pattern exact interdit. Remplacer par un `pg_dump` logique (à chaud). ⚠️ Corriger aussi la doc fausse (`docs/audit-reality/deploy-ops.md:58` prétend « pg_dump à chaud ✅ »).
- ⬜ **BKP-03** : archives en clair, aucune clé de chiffrement de sauvegarde. *Reste à faire* : chiffrement AES-256 des archives, clé dédiée via `gen-secrets.sh`/`.env`, jamais stockée avec l'archive.
- 🟡 **BKP-04** : seulement un exemple cron commenté + prose advisory (`docs/PROD_LOCAL.md`). *Reste à faire* : timer systemd (ou cron.d) installable, procédure de copie hors-machine, politique de rétention/purge réelle.

### Observabilité (OBS)

- ⬜ **OBS-02** : la stack monitoring est **opt-in** (`make monitor-up`), absente du chemin `up-local-prod`. *Reste à faire* : inclure Prometheus/Grafana/Loki dans le démarrage prod-local (active par défaut).
- ⬜ **OBS-03** : ni le compteur ni l'alerte n'existent. *Reste à faire* : émettre `onix_acl_sync_failures_total` (gateway, sur échec refresh Graph/groupe/doc-ACL — **inclure le chemin Fabric**) + règle d'alerte `increase(...[5m]) > 0`.
- 🟡 **OBS-04** : métrique `onix_gateway_guardrail_total{blocked}` présente (`metrics.py:41-45`, `main.py:476`). *Reste à faire* : ajouter la règle d'alerte sur blocages soutenus (la métrique existe, l'alerte non).
- ⬜ **OBS-05** : aucune alerte. *Reste à faire* : (a) métrique d'intégrité de chaîne d'audit (`onix_audit_chain_ok` pilotée par `verify_chain()`) + alerte ; (b) alerte pression disque/WAL Postgres ciblée (l'alerte `HostLowDisk` générique ne suffit pas).

### Sécurité prouvable (SEC) — cœur de valeur

- 🟡 **SEC-01** : harnais e2e live réel (`access-gateway/tests/e2e/run_access_e2e.py`) couvrant **SharePoint (A) ET Fabric (B)**, grant/deny fail-closed. *Reste à faire* : (1) **scénario de révocation** (révoquer une permission live et re-vérifier la disparition de la citation) — non couvert ; (2) **exécution enregistrée** contre un tenant non-prod (aucune preuve de run vert capturée) ; (3) pas dans un gate CI. **Dépendance externe : tenant Azure/SharePoint+Fabric non-prod.**
- 🟡 **SEC-02** : suite red-team (20 vecteurs, 5 catégories OWASP) + 1 run manuel 21/21 sur `qwen2.5:7b`. *Reste à faire* : run **re-jouable et enregistré sur le modèle de production**, pas seulement un transcript statique hors CI. **Exécution modèle-dépendante.**
- ⬜ **SEC-03** : mécanisme HMAC + `verify_chain()` réels et exposés (`audit_log.py`, endpoint `main.py:876`). *Reste à faire* : la cible `make audit-verify` **n'existe pas** et il n'y a **aucun script de démo** ; les créer (démontrable par un auditeur).
- ⬜ **SEC-05** : matière première dispersée (`audit-reality/security-governance.md`, `PARITE_ENTREPRISE.md`, `RBAC.md §6`, gates `ci.yml`) mais **aucun dossier agrégé**. *Reste à faire* : construire `docs/SECURITY_PROOF.md` mappant chaque menace → code → test → porte CI, FOSS-vs-EE explicite, risques résiduels (dont DEP-01 upstream). Agrégateur terminal.

### Conformité RGPD (RGPD)

- 🟡 **RGPD-01** : effacement onix-actions complet et **testé** (SQLite + S3, identifiants hachés ; `retention.py:153-205`, tests `test_security_rgpd.py:527-707`). *Reste à faire* : l'effacement ciblé de la **couche Onyx Postgres (historique de chat / comptes)** n'est ni outillé ni vérifié (seulement admin UI manuel ou `make destroy` global). Outiller + prouver un effacement sujet ciblé côté Onyx.
- 🟡 **RGPD-02** : `ONIX_RETENTION_DAYS=365` explicite et appliqué par catégorie côté actions (`retention.py:36-102`). *Reste à faire* : pas de TTL numérique pour la couche Onyx (chat/index — « à piloter ») ni pour le journal d'audit (« à définir »). Fixer/documenter des valeurs explicites pour toutes les catégories.

### Prêt opérationnel (OPS)

- ⬜ **OPS-01** : `docs/RUNBOOK.md` n'a **pas** de checklist d'acceptation go-live (la seule checklist, `DEPLOY_PROD.md §12`, est prod-exposé Caddy/TLS, mauvais périmètre). *Reste à faire* : rédiger la checklist ordonnée (preflight → secrets → démarrage → santé → admin → test de restauration → monitoring → démo audit) dans `RUNBOOK.md`.
- 🟡 **OPS-02** : rappel chiffrement disque hôte présent dans `RGPD.md:83` / `DPIA_TEMPLATE.md`. *Reste à faire* : le porter comme item de la checklist OPS-01 (trivial une fois OPS-01 créée).

### Pipeline de release (CICD)

- 🟡 **CICD-01** : `cd.yml` build + tags + scan trivy bloquant + SBOM (SPDX+CycloneDX) + gates verts — tout présent. *Reste à faire* : **signature cosign keyless OIDC absente** (zéro occurrence `cosign` dans `.github/`, pas de `id-token:write`). Ajouter l'étape de signature du digest + attestation SBOM.

## v2 Requirements

Reconnu mais différé.

### Durcissement upstream Onyx
- **SSRF-01** : router les Custom Tool Onyx via `ssrf_safe_get()` (validation d'URL complète).
- **XSS-01** : validation MIME magic-bytes (`puremagic`) sur l'upload d'avatar.

### Conformité avancée
- **DPIA-01** : DPIA (AIPD) complétée et signée par le DPO (RGPD art.35).

### Observabilité / ops
- **OBS-PROMTAIL-01** : migration Promtail → Grafana Alloy (EOL février 2026).
- **ACLRT-01** : synchronisation ACL temps réel par webhook SharePoint.

## Out of Scope

| Feature | Reason |
|---------|--------|
| AKS / Kubernetes HA | Cible = machine unique ; chart Helm existe mais hors cycle |
| SAML SSO | OIDC/Entra suffit en interne ; risque planning |
| Admin UI self-service | CLI/config suffit au go-live |
| Nouveaux connecteurs (au-delà de SharePoint + Fabric existants) | Périmètre documentaire couvert |
| Multi-tenancy FOSS | Isolation = instances séparées ; client interne unique |
| Cloud LLM / API externe | Contraire à la souveraineté |
| Chiffrement secrets at-rest Postgres / trimming ACL à l'indexation | Fonctions Onyx EE ; mitigées en FOSS |
| Certification SOC 2 / ISO 27001 | « Défendable devant un auditeur », pas certification formelle |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| DEP-02 | — | ✅ Done |
| OBS-01 | — | ✅ Done |
| SEC-04 | — | ✅ Done |
| DEP-01 | Phase 1 | ⬜ Open (reframed) |
| DEP-03 | Phase 1 | 🟡 Partial |
| HARD-01 | Phase 1 | ⬜ Open |
| HARD-02 | Phase 1 | 🟡 Partial |
| HARD-03 | Phase 1 | ⬜ Open |
| HARD-04 | Phase 1 | 🟡 Partial (Docker-gated) |
| OBS-02 | Phase 2 | ⬜ Open |
| OBS-03 | Phase 2 | ⬜ Open |
| OBS-04 | Phase 2 | 🟡 Partial |
| OBS-05 | Phase 2 | ⬜ Open |
| BKP-01 | Phase 3 | 🟡 Partial (Docker-gated) |
| BKP-02 | Phase 3 | 🟡 Partial |
| BKP-03 | Phase 3 | ⬜ Open |
| BKP-04 | Phase 3 | 🟡 Partial |
| OPS-01 | Phase 4 | ⬜ Open |
| OPS-02 | Phase 4 | 🟡 Partial |
| CICD-01 | Phase 4 | 🟡 Partial |
| SEC-01 | Phase 5 | 🟡 Partial (tenant-gated) |
| SEC-02 | Phase 5 | 🟡 Partial (model-gated) |
| SEC-03 | Phase 5 | ⬜ Open |
| SEC-05 | Phase 5 | ⬜ Open |
| RGPD-01 | Phase 5 | 🟡 Partial |
| RGPD-02 | Phase 5 | 🟡 Partial |

**Coverage:**
- v1 requirements: 26 total — 3 ✅ done, 23 remaining (13 🟡 partial, 10 ⬜ open)
- Mapped to phases: 23 remaining mapped ✓ (3 done unmapped/closed)
- Unmapped: 0 ✓

---
*Requirements defined: 2026-06-18*
*Re-baselined: 2026-06-19 against origin/main (post-sync audit of 26 reqs)*
