# CLAUDE.md

Ce dépôt suit **[`AGENTS.md`](AGENTS.md) — lis-le en premier** (embarquement,
architecture, build/test/déploiement, règles de jeu, carte des scopes).

Rappels rapides pour Claude Code :
- **Qualité (doit rester vert)** : `make test` (pytest + bandit + pip-audit + gitleaks
  + trivy + compose/helm). Suites offline : `actions/tests`, `access-gateway/tests`, `tests/rag`.
- **Démarrage** : `make tune && make secrets && make up && make verify`.
- **Conventions** : commentaires **en français**, stdlib-first, **zéro secret en repo**, **zéro mock présenté comme réel**.
- **Sécurité (non négociable)** : **fail-closed** ; **zéro secret en repo** (`.env` gitignoré) ; télémétrie OFF ; `runAsNonRoot` ; egress allowlisté ; **FOSS vs EE** distingué ; gates `bandit` (0 medium+) · `gitleaks` (0) · `pip-audit --strict` (0 CVE) · `trivy` **verts** avant commit. Modèle : [`SECURITY.md`](SECURITY.md) · scope gardien : [`docs/scopes/security-governance.md`](docs/scopes/security-governance.md).
- **Contexte clé** : Onyx FOSS ≠ entreprise turnkey (RBAC/audit/chiffrement = EE/absent) →
  la couche `onix` comble en FOSS. Toujours distinguer **FOSS vs EE**. Cf. `docs/audit-onyx/00-VERDICT.md`.
- **Architecture** : [`ARCHITECTURE.md`](ARCHITECTURE.md) · **Sécurité** : [`SECURITY.md`](SECURITY.md) · **Index doc** : [`docs/DOCS_INDEX.md`](docs/DOCS_INDEX.md).

## 🧭 Carte de navigation (sujet → doc)

> Pour intervenir : pars du **dossier de scope** (`docs/scopes/`), puis suis ses liens
> (code, commandes, tests, invariants, observabilité, docs de fond, journal). C'est
> l'**infra de doc pour agents**. Index des dossiers : [`docs/scopes/README.md`](docs/scopes/README.md).

| Tu cherches… | Va directement voir |
|---|---|
| RBAC / cache / streaming / ACL par-doc · SharePoint · Fabric | [`docs/scopes/access-gateway.md`](docs/scopes/access-gateway.md) |
| OCR / docgen / tâches · audit HMAC · PII · DLP · rétention · FinOps | [`docs/scopes/actions.md`](docs/scopes/actions.md) |
| Qualité RAG · garde-fous · RAGAS · prompt agent | [`docs/scopes/rag-prompts.md`](docs/scopes/rag-prompts.md) |
| Métriques / logs / alertes / SLO / dashboards | [`docs/scopes/monitoring.md`](docs/scopes/monitoring.md) |
| Compose / Helm HA / Azure / scripts / branding | [`docs/scopes/deploy-ops.md`](docs/scopes/deploy-ops.md) |
| Sécurité transverse · RGPD · **FOSS vs EE** · parité | [`docs/scopes/security-governance.md`](docs/scopes/security-governance.md) |
| Décision RBAC détaillée · e2e accès LIVE | [`docs/RBAC.md`](docs/RBAC.md) · [`docs/DECISION_RBAC.md`](docs/DECISION_RBAC.md) · [`docs/E2E_ACCESS_LIVE.md`](docs/E2E_ACCESS_LIVE.md) |
| Démarrer / déployer | [`docs/POC_LOCAL.md`](docs/POC_LOCAL.md) · [`docs/PROD_LOCAL.md`](docs/PROD_LOCAL.md) · [`docs/DEPLOY_AZURE.md`](docs/DEPLOY_AZURE.md) |
| Audit Onyx (verdict, dimensions) | [`docs/audit-onyx/00-VERDICT.md`](docs/audit-onyx/00-VERDICT.md) |
| Tout (index exhaustif) | [`docs/DOCS_INDEX.md`](docs/DOCS_INDEX.md) |

## 🔄 Tenir cette doc-infra à jour (à chaque action)

> Principe (« context engineering ») : *un agent n'est aussi bon que la dernière
> fois où son contexte a été revu.* La fraîcheur est donc **vérifiée, pas espérée**.

- **Règle (non négociable)** : tu touches le code d'un **scope** → tu mets à jour
  **son** [`docs/scopes/<scope>.md`](docs/scopes/) (carte du code/commandes), **son**
  `docs/audit-reality/<scope>.md` (preuve `fichier:ligne`) et **son**
  `ralph/state/<scope>.md` (journal). « Zéro mock présenté comme réel » vaut aussi pour la doc.
- **Commandes** :
  - `make docs-check` — STRUCTURE : registre [`docs/scopes/scopes.json`](docs/scopes/scopes.json),
    gabarit des dossiers, 0 lien mort. Inclus dans `make lint` → `make test` **et en CI**.
  - `make docs-freshness` — ANTI-DRIFT : refuse une modif de code de scope **sans** MAJ doc
    (gate CI sur PR ; dérogation justifiée `[docs-skip:<scope>]`).
  - `make hooks-install` — exécute ces deux gardes en **pre-commit** (à chaque commit).
- **Source de vérité / nouveau scope** : édite [`docs/scopes/scopes.json`](docs/scopes/scopes.json)
  (registre) + crée son `docs/scopes/<scope>.md` (gabarit : [`docs/scopes/README.md`](docs/scopes/README.md)).
- **Assistant guidé** : skill `/update-scope-docs` (met à jour dossier+audit+state, régénère
  `llms-full.txt`, fait passer les gardes au vert).
- **Cartes agent racine** : [`llms.txt`](llms.txt) (compacte) · `llms-full.txt` (contenu
  embarqué, **généré** : `make llms-full`).
