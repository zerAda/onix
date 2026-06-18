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
| actions | [actions.md](actions.md) | **3** | **Mono-poste ✓ / HA ✗** — code solide ; le branchement Helm trahit la doc (secrets WS2, object-store, erase S3). |
| rag-prompts | [rag-prompts.md](rag-prompts.md) | 0 | **Solide & honnête** — limites documentées ; fiabiliser les *preuves* live/baseline. |
| deploy-ops | [deploy-ops.md](deploy-ops.md) | 0 | **Mono-nœud ✓ / Azure ✗** — `prod-local`/`deploy/prod` aboutis ; 2 trous de câblage Azure. |
| monitoring | [monitoring.md](monitoring.md) | 1 | **POC ✓ / régulé ✗** — stack réelle ; doc fausse sur `/metrics` actions + pas de dashboard/alerte gateway, pas de SLO. |
| security-governance | [security-governance.md](security-governance.md) | 0 | **Code ✓ / preuves ✗** — contrôles réels ; conformité (DPIA/registre) = templates, garde-fous opérationnels manquants. |

## 3. Aptitude par palier de déploiement
| Palier | Apte ? | Bloquants |
|---|---|---|
| **Mono-poste / POC** (`make up`) | ✅ Oui | Aucun bloquant fonctionnel. Corriger la doc fausse monitoring (honnêteté). |
| **Prod machine-unique** (`up-local-prod`) | ✅ Oui | Pièce la plus aboutie ; appliquer P1 honnêteté + garde-fou Grafana. |
| **Prod exposée** (`deploy/prod`) | ✅ Oui | Anti-spoofing/fail-closed câblés ; corriger `backup.sh` (surcouche prod). |
| **HA Kubernetes / Azure AKS** | ❌ **Non** | **3 P0 actions** (secrets WS2 Helm, `ONIX_OBJECT_STORE`, erase S3) + 2 trous Azure (ingress RBAC, TLS Redis/PG Onyx) + `ENCRYPTION_KEY_SECRET`. |

## 4. Backlog consolidé priorisé (vers production-ready entreprise)
### P0 — bloquants HA (à traiter en premier)
1. **[actions]** Secrets WS2 (`ONIX_ACTIONS_ADMIN_KEY`/`AUDIT_HMAC_KEY`/`CALLER_HMAC_SECRET`) non injectés par le chart → `/admin/*` en 403, audit dégradé en SHA-256. *(scope `actions` + `deploy-ops`)*
2. **[actions]** `ONIX_OBJECT_STORE=s3` non câblé → `.docx` non partagés, `GET /download` casse en multi-réplica.
3. **[actions]** Effacement RGPD S3 incomplet (`objstore.delete_job` jamais appelé) → art.17 non exhaustif.
4. **[monitoring]** `OBSERVABILITY.md` affirme faussement que `/metrics` actions n'existe pas → **doc fausse** (règle n°1).

### P1 — exactitude, sécurité, conformité
- **[access-gateway]** `explicit_admin_bypass` inerte ; contradiction fail-loud/fail-safe du cache ; `_READ_ROLES` partiel.
- **[deploy-ops]** Ingress Azure chat→gateway + anti-spoofing non templatisé ; TLS Redis/PG Onyx non livrés ; `backup.sh` ignore la surcouche prod.
- **[security-gov]** `ENCRYPTION_KEY_SECRET` jamais posé ; hook gitleaks pre-commit inexistant ; `RGPD.md` périmé ; `securityContext` actions absent.
- **[monitoring]** Dashboard+alertes gateway absents ; pas de SLO/SLI ; garde-fou anti `admin/admin`.
- **[rag-prompts]** Transcripts live non archivés ; baseline RAGAS non reproductible ; red-team mono-langue.

### P2 — dette documentaire & durcissement
- Compteurs de tests faux (gateway 52→267, actions 58/71→86, rag 20/21).
- « SSE » vs NDJSON (gateway) ; `inference_`/`indexing_model_server` (RUNBOOK).
- Durcissement Helm généralisé (non-root/seccomp/readOnlyRootFS/NetworkPolicy) ; durcissement stack monitoring.
- DPIA/registre à remplir ; avertissement de provenance sur `audit-onyx/*`.

## 5. Suite — boucles Ralph
Le backlog ci-dessus alimente les **boucles Ralph** par scope (`ralph/scopes/*.md`,
runner `ralph/loop.sh`, état `ralph/state/*.md`). Critère de fin par scope = grille A1–A7
de [`ralph/ORCHESTRATION.md`](../../ralph/ORCHESTRATION.md). Ordre recommandé :
**actions (P0) → monitoring (doc fausse) → deploy-ops (Azure) → security-gov → access-gateway → rag-prompts.**
