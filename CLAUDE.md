# CLAUDE.md

Ce dépôt suit **[`AGENTS.md`](AGENTS.md) — lis-le en premier** (embarquement,
architecture, build/test/déploiement, règles de jeu, carte des scopes).

Rappels rapides pour Claude Code :
- **Qualité (doit rester vert)** : `make test` (pytest + bandit + pip-audit + gitleaks
  + trivy + compose/helm). Suites offline : `actions/tests`, `access-gateway/tests`, `tests/rag`.
- **Démarrage** : `make tune && make secrets && make up && make verify`.
- **Conventions** : commentaires **en français**, stdlib-first, **zéro secret en repo**, **zéro mock présenté comme réel**.
- **Contexte clé** : Onyx FOSS ≠ entreprise turnkey (RBAC/audit/chiffrement = EE/absent) →
  la couche `onix` comble en FOSS. Toujours distinguer **FOSS vs EE**. Cf. `docs/audit-onyx/00-VERDICT.md`.
- **Architecture** : [`ARCHITECTURE.md`](ARCHITECTURE.md) · **Sécurité** : [`SECURITY.md`](SECURITY.md) · **Index doc** : [`docs/DOCS_INDEX.md`](docs/DOCS_INDEX.md).
