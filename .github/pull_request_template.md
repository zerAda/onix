<!-- Décris le QUOI et le POURQUOI. Le diff montre le COMMENT. -->

## Objectif

<!-- 1–3 lignes : que change cette PR et pourquoi. -->

## Checklist (qualité onix — cf. AGENTS.md §5, CLAUDE.md)

- [ ] **Doc-infra à jour** : si j'ai touché le code d'un **scope**, j'ai mis à jour
      son `docs/scopes/<scope>.md` (carte du code/commandes), son
      `docs/audit-reality/<scope>.md` (preuve `fichier:ligne`) et son
      `ralph/state/<scope>.md` — sinon dérogation justifiée `[docs-skip:<scope>]`.
      *(vérifié par `make docs-freshness`)*
- [ ] **Portes vertes** : `make test` (pytest + bandit + pip-audit + gitleaks + trivy
      + compose/helm + `docs-check`) au vert.
- [ ] **Honnêteté** : zéro mock présenté comme réel ; **FOSS vs EE** distingué.
- [ ] **Sécurité** : zéro secret en repo ; fail-closed respecté.

## Preuves

<!-- Sorties de tests/gates, captures, liens CI. -->
