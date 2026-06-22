# Production-Readiness — verdict & preuves

**Date :** 2026-06-21 (audit initial) · **MAJ 2026-06-22 (Cycles 1+2 landés)** · **Méthode :** audit 7 dimensions (1 grader/dimension, preuves `fichier:ligne` + état CI + reqs/missions), bornage honnête de ce qui est **non vérifiable sans environnement** (Docker/tenant indisponibles ici).

## ⛔ VERDICT : NON-GO pour la production (mais Cycles 1+2 réduisent nettement l'écart)

**Score : 0 GO · 7 PARTIAL · 0 NO-GO** (était 0/1/6 → 0/3/4 → 0/4/3 → 0/5/2 → **M20 + backup chiffré lèvent les 2 derniers NO-GO**). Plus aucune dimension *bloquante* ; le passage en GO de chaque dimension dépend désormais d'éléments **humains/infra** (revue DPO, GPU pour RAGAS, tenant pour SEC-01, runtime). onix n'est **pas** encore « tout monté, vérifié et robuste » pour un go-live, mais les **Cycles 1 (sécurité) + 2 (fiabilité/observabilité)** ont fermé **7 blocages P0** (preuves ci-dessous). La couche logicielle est mature et bien testée *hors-ligne* ; restent (a) la **compliance + backup chiffré + supply-chain release** (Cycles 4), (b) le **mur RAG #12** (modèle/GPU, Cycle 3), et (c) **la pile complète exercée runtime** (boot prouvé sain sur Azure, cf. RUNTIME-EVIDENCE.md).

### ✅ Cycle 2 — Fiabilité & observabilité : LANDÉ (branche `prod/cycle1-securite`, gates locaux verts)
| Blocker | Fix | Preuve |
|---|---|---|
| **M4** alertes no-op | Alertmanager : `alertmanager.yml.tmpl` + `entrypoint.sh` rend l'URL webhook **fail-closed** (refus de démarrer sans `ALERT_WEBHOOK_URL` http(s)) ; receivers/routes ont un webhook RÉEL (`send_resolved`) | `monitoring/alertmanager/entrypoint.sh` ; `scripts/check-alertmanager-config.py` **rc=0** (cible `make` l.511) |
| **#10** OOM 14B (`make tune` 12g) | `detect-hardware.sh`/`.ps1` : empreinte = PIC réel (poids+KV+prompt-cache), pas poids Q4 ; 14B→24g ; avert. fail-closed petite RAM | `scripts/tests/test_detect_hardware_mem.py` (**6 tests**) ; 64Go→24g, 32→14g, 16→5g, somme<RAM |
| **#9** provider LLM non seedé | `scripts/seed-provider.sh` (+ cible `seed-provider`) : enregistre le provider Ollama via API admin, **idempotent + fail-closed** (auth admin par env, jamais en repo) | `scripts/tests/test_seed_provider.py` (**5 tests**) ; revue : fail-closed sur santé API/auth/401/PUT |
| **#6** résilience restart | Invariants `restart: always` + `start_period` + ordre de démarrage **assertés en test** + documentés | `scripts/tests/test_restart_policy.py` (**4 tests**) |

Gates locaux Cycle 2 : `deploy-ops` **15 tests passed** · `check-alertmanager-config` **rc=0** · `bandit` **0** sur le nouveau Python · 0 secret en repo. *Runtime-only restant (dit honnêtement) : non-OOM à 24g sur pile réelle, persistance `llm_provider` en base, reprise Docker post-kill — voir `ralph/state/deploy-ops.md` §Runtime-only.*

### ✅ Cycle 1 — Sécurité applicative : LANDÉ (branche `prod/cycle1-securite`, gates locaux verts)
| Blocker | Fix | Preuve |
|---|---|---|
| **M1** audit algo-downgrade | `verify_chain()` fail-closed : clé présente ⇒ HMAC strict ; ligne `sha256` = downgrade = rupture | `actions/app/audit_log.py:185-207` ; `tests/test_audit_log.py` (vérif standalone + suite **90 passed**) |
| **M7** X-OIDC spoof | preuve proxy obligatoire (`X-OIDC-Proxy-Secret` == secret partagé, temps constant) avant tout claim ; 4 call-sites + proxy injecte/strip | `access-gateway/app/identity.py:129-176` ; suite **339 passed** |
| **M3** ACL Fabric citations | `FabricDocACL` câblé au filtre (deny-by-default) ; doc hors-périmètre exclu | `access-gateway/app/fabric_doc_acl.py` ; `test_fabric_doc_acl.py` |
| **SUPPLY** CVE pip-audit | `pytest 8.3.4 → 9.0.3` (CVE-2025-71176) + `requirements-dev.txt` ajouté à la boucle Makefile | `pip-audit --strict` = **0 CVE** |

Gates locaux : `actions` 90✅/5⏭ · `gateway` 339✅ · `bandit` 0 medium+ · `pip-audit --strict` 0 · `docs-check`/`docs-freshness` verts · 0 secret en repo. *(gitleaks/trivy/compose-validate = CI.)*

## Tableau de bord

| # | Dimension | Note | En une phrase |
|---|-----------|------|---------------|
| 1 | Fonctionnalités up (boot & serve E2E) | 🟡 **PARTIAL** | Boot complet **prouvé sain sur Azure** (RUNTIME-EVIDENCE) ; **#9** (seed provider) + **#10** (OOM 14B) fermés ⇒ deux blocages du chat levés. Reste : pile complète + **RAG E2E réel** gâté par le **modèle #12** (Cycle 3). |
| 2 | Tests & portes CI | 🟡 **PARTIAL** (était NO-GO) | `pip-audit --strict` **repassé vert** (SUPPLY : pytest 9.0.3) ; reste la qualité RAG comparée à une baseline *synthétique* (RAGAS réelle = Cycle 3). |
| 3 | Sécurité prouvable (cœur de valeur) | 🟡 **PARTIAL** (était NO-GO) | **M1 ✅ M3 ✅ M7 ✅ HARD-03 ✅** (préflight clé HMAC d'audit fail-closed) fermés et testés. Reste : **test ACL live** SharePoint/Fabric jamais joué (SEC-01, exige tenant) + le finding API-compat gateway↔Onyx 4.1.1 à vérifier. |
| 4 | Fiabilité / backup / restore | 🟡 **PARTIAL** (était NO-GO) | **#6** restart assertés · **BKP-02** backup **chiffré fail-closed** (openssl AES-256, restore déchiffre) · restore round-trip prouvé sain (Azure). Restent : `pg_dump` logique (backup online/portable), WAL, gate santé restore. |
| 5 | Observabilité / alerting | 🟡 **PARTIAL** (était NO-GO) | **M4 fermé** : alertes livrées pour de vrai (webhook rendu **fail-closed**, refus sans URL) ⇒ plus de no-op. Restent : couverture d'alertes sync-ACL/rupture-audit (OBS-03/05), monitoring OFF par défaut (OBS-02), livraison E2E `amtool` (runtime). |
| 6 | Conformité (RGPD) | 🟡 **PARTIAL** (était NO-GO) | **M20 ✅** : les 4 affirmations DPO trompeuses **corrigées** (caveat FOSS no-op sur `ENCRYPTION_KEY_SECRET` ; journal d'accès RAG non émis ; effacement `.docx` best-effort ; identité OFF par défaut). Docs désormais **honnêtes**. Restent : **revue DPO humaine**, chiffrement FOSS réel (disque/EE), câblage du trail de lecture RAG. |
| 7 | Supply chain / release | 🟡 **PARTIAL** (était NO-GO) | Gate CVE **repassé vert** (SUPPLY) ; images **signées cosign keyless** (CICD-01, `cd.yml`). Restent : épingler `cosign-installer` au SHA, base Docker digest-pinnée (M9), gitleaks checksum. |

## Blocages — chemin vers GO

### A. Corrigeables sans environnement (code/docs — déjà catalogués)
- ~~**CVE pip-audit** (gate ROUGE)~~ ✅ **FAIT (Cycle 1/SUPPLY)** : pytest 9.0.3, `pip-audit --strict` vert.
- ~~**M1** audit HMAC algo-downgrade~~ ✅ · ~~**M3** câbler `FabricDocACL`~~ ✅ · ~~**M7** contrôle in-app `X-OIDC` fail-closed~~ ✅ **(Cycle 1)** · ~~**HARD-03** clé HMAC exigée au preflight~~ ✅ **(préflight fail-closed `_lifespan`)**. Reste : **SEC-03** cible `make audit-verify`.
- **M5b/BKP-02/03** `pg_dump` + chiffrement archives · **HARD-01/02** garde credentials + preflight prod.
- ~~**M4** livraison d'alerte réelle~~ ✅ **(Cycle 2)** : `entrypoint.sh` rend le webhook fail-closed, `check-alertmanager-config` rc=0. · ~~**#10** OOM tune~~ ✅ · ~~**#9** seed provider~~ ✅ · ~~**#6** invariants restart~~ ✅ **(Cycle 2)**. Restent : **OBS-03/05** alertes ACL-sync/audit-chain · **OBS-02** monitoring par défaut · **M16** sonde gateway.
- **M20** corriger les 4 mensonges docs DPO (revue DPO) · **RGPD-01** outiller l'effacement Onyx ciblé.
- **CICD-01** signature cosign · **M9** digest-pin des bases · **M12-rest** checksum gitleaks.

### B. Non prouvables ici — exigent Docker / un tenant live (preuve runtime)
- **HARD-04** acceptation runtime du boot ordonné de la pile compose.
- **BKP-01** `make restore-drill` (restore round-trip vérifié sain).
- **SEC-01** test ACL **live** SharePoint+Fabric (grant/deny/**révocation**) sur tenant non-prod.
- **SEC-02** red-team rejoué sur le **modèle de prod** ; **baseline RAGAS réelle** (run nightly sain ≥7B).
- Livraison d'alerte E2E (`amtool`), reprise `restart:always` après reboot, requête RAG E2E réelle.

## Ce qui EST solide / déjà livré (honnêteté inverse)
- Suites **offline** réelles et vertes (actions/gateway/rag — chemins sécurité couverts) ; trivy/bandit/gitleaks/pytest verts ; **runtime-smoke** prouve que l'image `onix-actions` démarre + sert `/health`+`/metrics`.
- Mécanismes RBAC/ACL SharePoint, garde-fous déterministes, chaîne HMAC (mécanisme réel), effacement actions/S3 testé, TTL actions appliqué — **présents et testés hors-ligne**.
- **6 corrections livrées + CI-vertes cette boucle** (M2, M5a, M12, M13, M14, M19) ; actions SHA-pinnées + Dependabot ; pipeline build+trivy+SBOM solide.
- `audit-reality/` reste **honnête** ; la dérive est isolée aux docs *outward-facing* (DPO).

## Pourquoi je ne peux pas « boucler jusqu'au GO » ici
Atteindre GO exige : un **hôte Docker** (booter/valider la pile, restore-drill, alertes E2E), un **tenant Azure live** (SEC-01), un **modèle ≥7B** pour une baseline RAGAS réelle, le travail **code-de-scope** que possède la boucle **Ralph** (M1/M3/M15…), et une **revue DPO** (M20). Aucun n'est réalisable dans cet environnement. La boucle orchestrateur a bouclé jusqu'à la **preuve** (ce verdict) — pas jusqu'au GO, ce qui serait malhonnête.

## Estimation honnête
Périmètre go-live (REQUIREMENTS.md v1) : **3/26 fait, 13 partiel, 10 ouvert.** Combler A (code/docs) puis prouver B (runtime/tenant) = un effort de **semaines** sur une machine Docker + tenant + revue DPO. Le programme exact est dans [MISSIONS.md](MISSIONS.md) (20 missions) + [RALPH-HANDOFF.md](RALPH-HANDOFF.md) + [REQUIREMENTS.md](REQUIREMENTS.md).

---
*Audit production-readiness — 7 dimensions, preuves `fichier:ligne`. Refaire après remédiation : ré-exécuter l'audit + un `make verify` / `scripts/verify.sh` réel sur hôte Docker.*
