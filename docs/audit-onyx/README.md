# Audit de production-readiness — Onyx v4.1.1 (open-source RAG, ex-Danswer)

> ⚠️ **AVERTISSEMENT DE PROVENANCE (à lire avant tout)** — Version Onyx auditée :
> **v4.1.1** (commit `33613e1`), audit daté du **2026-06-18**. Le code source
> d'Onyx **n'est PAS vendoré** dans ce dépôt : il a été analysé depuis un clone
> **externe** (`/tmp/onyx_v411`) **absent d'`onix`**. En conséquence, **toutes les
> citations `backend/…:ligne` / `ee/…:ligne` de ce dossier ne sont PAS
> re-vérifiables depuis ce dépôt** — elles renvoient à du code Onyx non présent
> ici. Traitez `docs/audit-onyx/*` comme une **source secondaire sourcée et
> interne-cohérente**, pas comme une preuve directe vérifiable localement. Les
> **mitigations `onix`** invoquées (audit HMAC, ACL de sortie, DLP, rétention…)
> sont, elles, **dans ce dépôt et vérifiables** (cf. `ARCHITECTURE.md`,
> `docs/SECURITY_RGPD_ACTIONS.md`, `docs/audit-reality/security-governance.md`).

> Source auditée : **`/tmp/onyx_v411`** — git tag **v4.1.1**, commit `33613e1`,
> origin `github.com/onyx-dot-app/onyx`. Échelle : ~**542 000 LOC Python** (2597
> fichiers) + **1783** fichiers TS/TSX. Objectif : déterminer si Onyx est une
> solution **prod-ready premium/entreprise** ou un **POC** — preuves à l'appui.
>
> Méthode : 7 agents spécialisés, chacun auditant une dimension sur le **code réel**
> (citations `chemin:ligne`), + docs/issues/CVE/releases réels. **Aucune donnée
> mockée. Aucune spéculation présentée comme un fait.** FOSS vs EE vs Cloud distingués.
>
> Barème par dimension (1-5) : 1=POC · 2=alpha · 3=utilisable-avec-réserves ·
> 4=prod-ready · 5=premium/entreprise.

## Sections
- `10-architecture-scalability.md` — architecture, data-tier, HA/scale (Helm), SPOFs
- `20-code-quality-tests-ci.md` — qualité du code, tests, CI/CD, maturité des releases
- `30-security.md` — authN/Z, RBAC, gating EE, secrets, conteneurs, CVE, OWASP
- `40-sharepoint-connector-integration.md` — connecteur SharePoint (byte-by-byte) + Graph réel + runbook live
- `50-rgpd-governance.md` — RGPD, résidence, PII, audit, rétention, multi-tenant
- `60-observability-runtime.md` — métriques/logs/traces, migrations, **boot réel** tenté
- `70-oss-health-licensing.md` — santé open-source, cadence, gouvernance, FOSS/EE/Cloud
- `00-VERDICT.md` — synthèse + scorecard pondéré + verdict honnête (orchestrateur)
