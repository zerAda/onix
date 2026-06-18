# VERDICT consolidé — Audit byte-by-byte doc ↔ réalité (onix)

> **Date** : 2026-06-18 · **Méthode** : 6 audits parallèles, un par scope, chaque
> affirmation de la doc confrontée au code/config avec preuve `fichier:ligne`
> (cf. [`README.md`](README.md) pour la légende). **Règle d'or** (AGENTS.md §1) :
> *« Honnêteté > esbroufe. Zéro mock présenté comme du réel. »*

## 1. Résultat global (≈ 380 affirmations tracées)
| Classe | Total | Lecture |
|---|--:|---|
| ✅ CONFORME | ~261 | La doc dit vrai, prouvé dans le code. |
| ⚠️ ÉCART MINEUR | ~52 | Doc imprécise/périmée, intention tenue. |
| ❌ ÉCART MAJEUR | ~8 | Doc affirme un comportement faux. |
| 🕳️ DOC-SANS-CODE | **~5** | Fonction documentée non implémentée. |
| 🔇 CODE-SANS-DOC | ~18 | Implémenté mais non/mal documenté. |
| ❔ NON VÉRIFIABLE | ~37 (+ `audit-onyx/*`) | Porte sur du code externe (Onyx non vendoré). |

**Conclusion d'intégrité** : sur ~380 affirmations, **~5 seulement** sont du « doc-sans-code »
(1,3 %). La règle n°1 **tient globalement** : le dépôt ne « vend » quasiment pas de fonctionnalité
fantôme. Les claims de parité entreprise (audit HMAC chaîné, DLP/anti-SSRF, redaction PII,
rétention/effacement, ACL par-doc) sont **réellement implémentés et branchés**. L'écart dominant
n'est pas le mensonge mais **la doc en avance sur le câblage HA/Azure** et **les preuves de
conformité non archivées**.

## 2. Verdict par scope
| Scope | Rapport | P0 | Verdict production-ready |
|---|---|--:|---|
| access-gateway | [access-gateway.md](access-gateway.md) | 0 | **Presque** — invariants RBAC/fail-closed/cache↔ACL implémentés ET testés ; 3 P1 d'honnêteté doc. |
| actions | [actions.md](actions.md) | ✅ 0 _(3 résolus)_ | **Mono-poste ✓ ; HA débloqué** — 3 P0 fermés (secrets WS2, object-store, erase S3) ; restent P1 (openapi, rate-limit HA). |
| rag-prompts | [rag-prompts.md](rag-prompts.md) | 0 | **Solide & honnête** — limites documentées ; fiabiliser les *preuves* live/baseline. |
| deploy-ops | [deploy-ops.md](deploy-ops.md) | 0 | **Mono-nœud ✓ / Azure ✗** — `prod-local`/`deploy/prod` aboutis ; 2 trous de câblage Azure. |
| monitoring | [monitoring.md](monitoring.md) | ✅ 0 _(1 résolu)_ | **POC ✓** — doc fausse `/metrics` corrigée ; restent P1 (dashboard/alerte gateway, SLO). |
| security-governance | [security-governance.md](security-governance.md) | 0 | **Code ✓ / preuves ✗** — contrôles réels ; conformité (DPIA/registre) = templates, garde-fous opérationnels manquants. |

## 3. Aptitude par palier de déploiement
| Palier | Apte ? | Bloquants |
|---|---|---|
| **Mono-poste / POC** (`make up`) | ✅ Oui | Aucun bloquant fonctionnel. Corriger la doc fausse monitoring (honnêteté). |
| **Prod machine-unique** (`up-local-prod`) | ✅ Oui | Pièce la plus aboutie ; appliquer P1 honnêteté + garde-fou Grafana. |
| **Prod exposée** (`deploy/prod`) | ✅ Oui | Anti-spoofing/fail-closed câblés ; corriger `backup.sh` (surcouche prod). |
| **HA Kubernetes / Azure AKS** | ⏳ **Quasi prêt** | P0 ✅ + P1 ✅ (secrets WS2, object-store, erase S3, `ENCRYPTION_KEY_SECRET`, TLS Redis/PG Azure, securityContext généralisé). **Reste** : forward-auth/anti-spoofing ingress AKS = TODO recette documenté (route chat→gateway templatisée OPT-IN) ; durcissement P2 (`readOnlyRootFS`/`NetworkPolicy`). |

## 4. Backlog consolidé priorisé (vers production-ready entreprise)
### P0 — bloquants HA _(tous résolus — boucles Ralph)_
1. ✅ **RÉSOLU [actions]** Secrets WS2 injectés par le chart (helper `onix.actionsSecretEnv`, `secretKeyRef` dans `actions.yaml`+`actions-queue.yaml`, noms alignés sur le code). *(`0fc8893`)*
2. ✅ **RÉSOLU [actions]** `ONIX_OBJECT_STORE` câblé dans le ConfigMap HA (rend `"s3"`). *(`b205d31`)*
3. ✅ **RÉSOLU [actions]** Effacement RGPD S3 branché (`delete_subject_docx`/`delete_jobs_older_than` dans `retention`) + 4 tests. *(`bc7f9a6`)*
4. ✅ **RÉSOLU [monitoring]** Doc « `/metrics` actions n'existe pas » corrigée (6 fichiers). *(`961978f`)*

### P1 — exactitude, sécurité, conformité _(tous traités — boucles Ralph)_
- ✅ **[access-gateway]** `explicit_admin_bypass`/fail-loud-vs-fail-safe/`_READ_ROLES` réconciliés au réel (doc). *(`e886648`)*
- ✅ **[deploy-ops]** TLS Redis/PG Onyx (Azure) + `backup.sh` prod-aware livrés ; ingress chat→gateway templatisé OPT-IN (forward-auth = TODO recette documenté). *(`dfb7891`)*
- ✅ **[security-gov]** `ENCRYPTION_KEY_SECRET` câblé (compose+Helm) ; hook gitleaks pre-commit posé ; `RGPD.md` réaligné ; `securityContext` généralisé. *(`09a19a0` + `.pre-commit-config.yaml`)*
- ✅ **[monitoring]** Dashboard `onix-gateway` + alertes + SLO/recording rules + garde-fou anti `admin/admin`. *(`4b70d32`)*
- ✅ **[rag-prompts]** Résultats live encadrés « indicatif » + baseline RAGAS reproductible byte-level. *(`878de86`)*

### P2 — dette documentaire & durcissement
- ✅ Compteurs de tests réconciliés (gateway 52→267, actions →90/85/5, rag 21 cas).
- ✅ « SSE »→NDJSON (gateway + monitoring) ; ✅ RUNBOOK `indexing_model_server`.
- ✅ `securityContext` généralisé (seccomp partout, non-root où l'image le permet) ; ✅ DPIA/registre squelette factuel ; ✅ provenance `audit-onyx/*`.
- ✅ Durcissement Helm **OPT-IN** livré : `NetworkPolicy` (default-deny, défaut OFF) + `readOnlyRootFilesystem` (access-gateway, défaut OFF) — rendu par défaut inchangé. *(`58ba627`)*
- ✅ Durcissement stack monitoring (M5 : `no-new-privileges`/`cap_drop`/`read_only`) + visualisation/alertes OpenSearch. *(`26f8ce8`)*
- ⬜ **Restant (différé — nécessite cluster/live)** : forward-auth oauth2-proxy ingress AKS (décision reportée) ; extension red-team multi-langue (R3, à valider en live).

## 5. État des boucles Ralph
Les 6 boucles ont exécuté leurs itérations **P0 + doc-truth/P1** (gates verts, poussées) :
**actions** (P0 HA + P1) · **monitoring** (P0 doc + P1 dashboards/SLO) · **access-gateway** (P1/P2) ·
**rag-prompts** (P1/P2) · **deploy-ops** (P1 Helm/Azure) · **security-gov** (P1/P2). Restent les
**P2/suite** ci-dessus (non bloquants). Runner `ralph/loop.sh`, prompts `ralph/scopes/*.md`, journaux
`ralph/state/*.md` ; critère de fin = grille A1–A7 de [`ralph/ORCHESTRATION.md`](../../ralph/ORCHESTRATION.md).
