# Production-Readiness — verdict & preuves

**Date :** 2026-06-21 · **Méthode :** audit 7 dimensions (1 grader/dimension, preuves `fichier:ligne` + état CI + reqs/missions), bornage honnête de ce qui est **non vérifiable sans environnement** (Docker/tenant indisponibles ici).

## ⛔ VERDICT : NON-GO pour la production

**Score : 0 GO · 1 PARTIAL · 6 NO-GO.** onix n'est **pas** « tout monté, vérifié et robuste » pour un go-live. La couche logicielle est mature et bien testée *hors-ligne*, mais (a) la **CI du dépôt est ROUGE sur `main` aujourd'hui** (gate `pip-audit --strict` : CVE ouverte sur une dépendance épinglée), (b) plusieurs **P0 sécurité/fiabilité** restent ouverts, et (c) **la pile complète n'a jamais été démarrée ni exercée** dans cet environnement (pas de Docker) → « toutes les fonctionnalités sont up » est **non démontré**, pas prouvé.

## Tableau de bord

| # | Dimension | Note | En une phrase |
|---|-----------|------|---------------|
| 1 | Fonctionnalités up (boot & serve E2E) | 🟡 **PARTIAL** | Câblage compose valide, mais la pile complète n'a jamais bootée ni servi une requête RAG ici ; seul `onix-actions` est prouvé démarrer. |
| 2 | Tests & portes CI | ⛔ **NO-GO** | `pip-audit --strict` **ROUGE sur `main`** (CI échoue sur la branche par défaut) ; qualité RAG comparée à une baseline *synthétique* → « tout vert » est faux. |
| 3 | Sécurité prouvable (cœur de valeur) | ⛔ **NO-GO** | Audit inviolable **contournable** (M1), ACL Fabric **code mort** dans le filtre live (M3), frontière X-OIDC sans contrôle in-app (M7), repli SHA-256 silencieux (HARD-03), test ACL live jamais joué (SEC-01). |
| 4 | Fiabilité / backup / restore | ⛔ **NO-GO** | Backup = tar à froid (pas `pg_dump`), **non chiffré**, restore affirme « OK » sans condition et **jamais exercé** ; ordre healthcheck overlay-only, non validé runtime. |
| 5 | Observabilité / alerting | ⛔ **NO-GO** | Métriques OK mais **toutes les alertes vont dans un no-op** (M4) ; échec sync-ACL et rupture de chaîne d'audit **non monitorés** ; monitoring OFF par défaut en prod-local. |
| 6 | Conformité (RGPD) | ⛔ **NO-GO** | 4 **mensonges** non corrigés dans les docs DPO (chiffrement FOSS no-op vendu comme art.32 ; journal « qui-a-vu-quoi » jamais émis sur le chemin RAG) ; effacement Onyx art.17 FK-cassé, non outillé. |
| 7 | Supply chain / release | ⛔ **NO-GO** | Gate release ROUGE (CVE), images **non signées** (pas de cosign), base Docker non digest-pinnée, gitleaks téléchargé sans checksum. |

## Blocages — chemin vers GO

### A. Corrigeables sans environnement (code/docs — déjà catalogués)
- **CVE pip-audit** (gate ROUGE) — bumper la dépendance vulnérable (Dependabot a ouvert PRs #11-13) ou accepter formellement le CVE. *Bloquant immédiat.*
- **M1** audit HMAC algo-downgrade (P0) · **HARD-03** clé HMAC exigée au preflight (P0) · **M3** câbler `FabricDocACL` dans le filtre · **M7** contrôle in-app `X-OIDC` fail-closed · **SEC-03** cible `make audit-verify`.
- **M5b/BKP-02/03** `pg_dump` + chiffrement archives · **HARD-01/02** garde credentials + preflight prod.
- **M4** livraison d'alerte réelle (envsubst/url_file) + **OBS-03/05** métriques+alertes ACL-sync/audit-chain · **OBS-02** monitoring par défaut · **M16** sonde gateway.
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
