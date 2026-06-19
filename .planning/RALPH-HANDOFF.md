# Handoff Ralph — backlog go-live mono-poste sécurisé

**Date :** 2026-06-19
**Source :** re-baseline GSD post-sync (audit des 26 reqs v1 contre `origin/main`). Détail : [REQUIREMENTS.md](REQUIREMENTS.md) · [ROADMAP.md](ROADMAP.md).
**Statut audit :** 3 ✅ done (DEP-02, OBS-01, SEC-04) · 13 🟡 partiel · 10 ⬜ ouvert.

> **But.** Donner à la boucle Ralph un backlog prêt-à-traiter : chaque item est rattaché à **son scope** (`ralph/scopes/<scope>.md`, `docs/scopes/<scope>.md`), pointe le **fichier réel**, et décrit le **delta exact**. GSD ne touche pas au code des scopes (évite la collision avec la boucle). Règles du jeu inchangées : `make test` vert, FOSS-vs-EE distingué, **zéro mock présenté comme réel**, et `/update-scope-docs <scope>` après chaque modif (gate `make docs-freshness`).

## Garde-fous transverses (à respecter pour chaque item)
- `make test` doit rester vert (pytest + bandit + pip-audit --strict + gitleaks + trivy + compose/helm).
- Toute modif de code d'un scope → MAJ `docs/scopes/<scope>.md` + `docs/audit-reality/<scope>.md` + `ralph/state/<scope>.md`.
- Vérification **runtime** (Docker) requise pour : HARD-04, BKP-01, exécutions live — non prouvables sans Docker.
- Dépendances **externes** : SEC-01 (tenant Azure/SharePoint+Fabric non-prod), SEC-02 (modèle de prod pullé).

---

## Scope `deploy-ops` (le plus gros lot)

| Req | Statut | Fichier(s) | Delta à réaliser | Prio |
|-----|--------|-----------|------------------|------|
| **HARD-01** | ⬜ | `scripts/preflight-prod.sh` (+ entrypoint) | Détecter en mode prod les valeurs bannies (`POSTGRES_PASSWORD=password`, MinIO `minioadmin`, Redis faible/`<` seuil) → exit non-nul **avant** montage de la pile. | P0 |
| **HARD-02** | 🟡 | `Makefile` (`up-prod`/`up-local-prod`), `scripts/preflight-local.sh`, `preflight-prod.sh` | Câbler les 4 contrôles existants (secrets/`vm.max_map_count`/disque/daemon, déjà dans preflight-local) comme **prérequis bloquant** du démarrage prod, ou les fusionner dans `preflight-prod.sh`. | P0 |
| **HARD-04** | 🟡🐳 | `docker-compose.prod-local.yml`, nouveau `docs/COMPOSE_ACCEPTANCE.md` | Produire un **enregistrement d'acceptation runtime** (compose up → services healthy dans l'ordre → `docker stop`/reboot → recovery via `restart:always`). Nécessite Docker. | P1 |
| **DEP-01** | ⬜ | `.github/workflows/ci.yml` / `cd.yml`, `docs/audit-onyx/30-security.md` | Le CVE `cryptography==46.0.7` (CVSS 7.5) vit dans l'**image Onyx upstream** (pas dans nos requirements). Épingler l'image `onyx-backend` à un tag corrigé **+ ajouter un scan trivy de cette image**, OU documenter en risque résiduel accepté (→ SEC-05). | P1 |
| **DEP-03** | 🟡 | `ci.yml:68`, `.pre-commit-config.yaml:14`, `Makefile:452`, `cd.yml:124,130` | `gitleaks` v8.18.2 → ≥ 8.21 (3 endroits) ; `anchore/sbom-action` `@v0` → version/SHA fixe. | P2 |
| **BKP-02** | 🟡 | `scripts/backup.sh:51-55`, `docs/audit-reality/deploy-ops.md:58` | Remplacer le **tar à froid du volume `db_volume`** par un `pg_dump` logique (à chaud). ⚠️ **Corriger le mensonge doc↔code** : `deploy-ops.md:58` prétend « pg_dump à chaud ✅ » alors que c'est un tar. | P0 |
| **BKP-03** | ⬜ | `scripts/backup.sh`, `scripts/gen-secrets.sh`, `env.template` | Chiffrer les archives en AES-256 (`openssl enc -aes-256-cbc -pbkdf2`), clé `BACKUP_ENCRYPTION_KEY` générée par `gen-secrets.sh`, **jamais** stockée avec l'archive. | P1 |
| **BKP-01** | 🟡🐳 | `Makefile`, `scripts/restore.sh` | Cible `make restore-drill` : restaure vers une cible éphémère + **assertion de santé** gateante (échec = exit non-nul). Nécessite Docker pour la preuve. | P1 |
| **BKP-04** | 🟡 | `deploy/local-prod/` (nouveaux `.timer`/`.service`), `docs/RUNBOOK.md` | Timer systemd (ou cron.d) **installable** + procédure copie hors-machine + script/politique de rétention-purge. | P2 |
| **OPS-01** | ⬜ | `docs/RUNBOOK.md` | Rédiger la **checklist d'acceptation go-live** ordonnée : preflight → secrets → démarrage → santé → compte admin → **test de restauration** → monitoring → **démo audit**. | P1 |
| **OPS-02** | 🟡 | `docs/RUNBOOK.md` (depuis `docs/RGPD.md:83`) | Porter le rappel chiffrement disque hôte (BitLocker/LUKS) comme **item** de la checklist OPS-01. Trivial une fois OPS-01 créée. | P2 |
| **CICD-01** | 🟡 | `.github/workflows/cd.yml` | Ajouter la **signature cosign keyless OIDC** (`id-token:write`, `sigstore/cosign-installer`) sur le digest poussé + attestation SBOM. Le reste (tag/trivy/SBOM/gates) est déjà fait. | P1 |

## Scope `monitoring`

| Req | Statut | Fichier(s) | Delta | Prio |
|-----|--------|-----------|-------|------|
| **OBS-02** | ⬜ | `Makefile` (`up-local-prod`), `docker-compose.prod-local.yml`, `monitoring/docker-compose.monitoring.yml` | Inclure la stack monitoring dans le démarrage prod-local (active **par défaut**, plus de `make monitor-up` séparé). | P1 |
| **OBS-04** | 🟡 | `monitoring/prometheus/rules/onix-alerts.yml` | Règle d'alerte sur blocages garde-fou soutenus : `sum(rate(onix_gateway_guardrail_total{blocked="true"}[5m])) > seuil`. Métrique déjà émise. | P1 |
| **OBS-05** | ⬜ | `monitoring/prometheus/rules/onix-alerts.yml`, exporter Postgres | (a) alerte rupture de chaîne d'audit (métrique à créer côté actions, cf. OBS-05/actions) ; (b) alerte pression disque/WAL Postgres ciblée (custom query postgres-exporter ou alerte node sur le mount Postgres). | P1 |
| **OBS-03** (alerte) | ⬜ | `monitoring/prometheus/rules/onix-alerts.yml` | Règle `increase(onix_acl_sync_failures_total[5m]) > 0 for 5m` (métrique à créer côté access-gateway). | P1 |

## Scope `access-gateway`

| Req | Statut | Fichier(s) | Delta | Prio |
|-----|--------|-----------|-------|------|
| **OBS-03** (métrique) | ⬜ | `access-gateway/app/graph_acl.py`, `fabric_acl.py`, `metrics.py` | Émettre `onix_acl_sync_failures_total` sur échec de refresh Graph/groupe/doc-ACL — **inclure le chemin Fabric**. | P1 |
| **SEC-01** | 🟡🔑 | `access-gateway/tests/e2e/run_access_e2e.py` | Ajouter un **scénario de révocation** live (révoquer une permission SharePoint+Fabric, re-vérifier disparition de la citation ≤ 300 s) + **capturer un run enregistré** contre un tenant non-prod. Dépendance externe : tenant. | P0 |

## Scope `actions`

| Req | Statut | Fichier(s) | Delta | Prio |
|-----|--------|-----------|-------|------|
| **HARD-03** | ⬜ | `actions/app/audit_log.py:139-145` (+ preflight) | Supprimer/rendre fatal le repli silencieux SHA-256 sans clé en prod ; exiger `ONIX_ACTIONS_AUDIT_HMAC_KEY` (présente, ≥ 32 chars) au preflight prod. | P0 |
| **SEC-03** | ⬜ | `Makefile`, nouveau `scripts/audit-verify.sh` (ou démo) | Cible `make audit-verify` + script de démo montrant `verify_chain() → ok: true` (mécanisme déjà réel, endpoint `main.py:876`). Brancher dans `make verify`. | P0 |
| **OBS-05** (métrique) | ⬜ | `actions/app/audit_log.py`, `main.py` | Exposer `onix_audit_chain_ok` (gauge piloté par `verify_chain()`) pour alimenter l'alerte OBS-05/monitoring. | P1 |
| **RGPD-02** | 🟡 | `actions/app/retention.py`, `docs/RGPD.md`, `docs/REGISTRE_TRAITEMENTS.md` | Fixer/documenter des TTL **numériques explicites** pour **toutes** les catégories (Onyx chat/index, journal d'audit), pas seulement `ONIX_RETENTION_DAYS=365` côté actions. | P2 |

## Scope `rag-prompts`

| Req | Statut | Fichier(s) | Delta | Prio |
|-----|--------|-----------|-------|------|
| **SEC-02** | 🟡🤖 | `tests/rag/test_red_team.py`, `Makefile` (`rag-test-live`), CI | Rendre le red-team **re-jouable et enregistré sur le modèle de production** (pas un transcript statique hors CI). Dépendance : modèle prod pullé. | P1 |

## Scope `security-governance` (agrégateur)

| Req | Statut | Fichier(s) | Delta | Prio |
|-----|--------|-----------|-------|------|
| **RGPD-01** | 🟡 | couche Onyx Postgres, `docs/RGPD.md:52-57` | Outiller + **prouver** un effacement sujet ciblé côté **Onyx Postgres** (chat/comptes). L'effacement actions/S3 (`retention.py`) est déjà testé ; le côté Onyx ne l'est pas. | P1 |
| **SEC-05** | ⬜ | nouveau `docs/SECURITY_PROOF.md` | Agrégateur **terminal** : mapper chaque menace → chemin de code → test → porte CI, FOSS-vs-EE explicite, risques résiduels (dont DEP-01 upstream). Matière première dispersée (`audit-reality/security-governance.md`, `PARITE_ENTREPRISE.md`, `RBAC.md §6`). | P1 (après les autres) |

---

## Ordre suggéré (dépendances)
1. **P0 sécurité/correctness** : HARD-01, HARD-02, HARD-03, BKP-02 (+ fix doc), SEC-03.
2. **P1 preuves/obs** : DEP-01, BKP-03, BKP-01, OBS-02/03/04/05, CICD-01, OPS-01, SEC-01🔑, SEC-02🤖, RGPD-01, HARD-04🐳.
3. **P2 hygiène** : DEP-03, BKP-04, OPS-02, RGPD-02.
4. **Terminal** : SEC-05 (dossier de preuve) une fois les preuves amont produites.

## Hors périmètre (ne pas faire ce cycle) — cf. REQUIREMENTS.md §Out of Scope
AKS/K8s, SAML, Admin UI, nouveaux connecteurs, multi-tenancy, cloud LLM, chiffrement secrets at-rest Postgres (EE), trimming ACL à l'indexation (EE), certification SOC2/ISO.
