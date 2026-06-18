# ORCHESTRATION.md — Industrialisation d'onix par boucles Ralph

> **Public** : agents IA (et humains) pilotant la montée au niveau *production-ready
> entreprise*. **Lis `AGENTS.md` (racine) avant ce fichier.** Voici la consigne
> brute de l'utilisateur, *étoffée comme une spec d'ingénieur data/plateforme* :
> méthode, contrats, critères d'acceptation, outillage, garde-fous.

---

## 0. Définition de « premium grade entreprise, production-ready » (mesurable)

Un scope est *production-ready* quand **les 7 axes** ci-dessous sont **objectivement
satisfaits et prouvés** (pas affirmés). C'est la grille d'acceptation de toutes les boucles.

| # | Axe | Critère d'acceptation **mesurable** | Preuve attendue |
|---|---|---|---|
| A1 | **Exactitude doc↔code** | 0 écart `❌` et 0 `🕳️` restant dans `docs/audit-reality/<scope>.md` ; chaque `🔇` soit documenté soit supprimé | rapport d'audit mis à jour, diff |
| A2 | **Tests** | Chemins critiques couverts ; suite offline verte ; cas limites/red-team ajoutés pour chaque correctif | `pytest` vert + nouveaux tests cités |
| A3 | **Sécurité** | 0 `bandit` medium+, 0 secret `gitleaks`, 0 CVE `pip-audit --strict`, 0 CVE fixable `trivy` ; fail-closed vérifié | sortie `make test` |
| A4 | **Observabilité** | Métriques + logs structurés + alertes actionnables pour le scope ; runbook à jour | config monitoring + `docs/RUNBOOK.md` |
| A5 | **Fiabilité** | Timeouts, retries idempotents, limites de taille/ressources, dégradation gracieuse, `runAsNonRoot` | code + manifests + tests |
| A6 | **Reproductibilité** | Toute commande/cible citée par la doc existe et marche ; IaC valide (`helm lint`, `compose config`, `bicep build`) ; zéro étape manuelle cachée | gates ops verts |
| A7 | **RGPD/gouvernance** *(si applicable)* | Audit-trail vérifiable, redaction PII testée, rétention/effacement effectifs, registre à jour | tests + `docs/REGISTRE_TRAITEMENTS.md` |

**Sentinelle de fin** : quand A1–A7 sont satisfaits pour un scope, écrire `RALPH_DONE`
en tête de `ralph/state/<scope>.md`. La boucle s'arrête alors pour ce scope.

---

## 1. Le prompt maître étoffé (rejoué à chaque itération)

> Gabarit paramétré. `loop.sh` injecte `{{SCOPE}}`, et l'agent lit les fichiers
> référencés. Les `ralph/scopes/<scope>.md` sont des instanciations concrètes de ce gabarit,
> amorcées avec les écarts réels de l'audit.

```text
RÔLE : Ingénieur·e {{DISCIPLINE}} senior, propriétaire du scope « {{SCOPE}} » d'onix.
Tu opères en BOUCLE : une itération = un incrément vérifié vers production-ready, puis tu t'arrêtes.

CONTEXTE OBLIGATOIRE À RELIRE :
  1. AGENTS.md (règles de jeu §5 surfaces disjointes, §7 pièges) + CLAUDE.md.
  2. ralph/ORCHESTRATION.md (cette grille A1–A7 + la Definition of Done).
  3. docs/audit-reality/{{SCOPE}}.md (les écarts réels doc↔code, priorisés P0/P1/P2).
  4. ralph/state/{{SCOPE}}.md (TON journal : ce qui est fait / en cours / reste). RELIS-LE EN PREMIER.

BOUCLE (une itération) :
  ÉTAPE 0 — Sync : `git pull` si besoin ; relis le journal d'état ; choisis LE prochain item
           non fait, par priorité P0 > P1 > P2, le plus à fort effet de levier.
  ÉTAPE 1 — Plan : décris en 3–6 lignes le correctif minimal et son critère d'acceptation (lequel des A1–A7).
  ÉTAPE 2 — Implémente : changement le plus petit qui ferme l'écart. Respecte stdlib-first,
           commentaires français, code voisin. NE casse aucun piège §7. Ajoute/maj les tests.
  ÉTAPE 3 — Prouve : lance les gates pertinents (au minimum la suite du scope ; idéalement `make test`).
           Si rouge → répare dans CETTE itération avant de continuer. Jamais de commit sur du rouge.
  ÉTAPE 4 — Réconcilie la doc : mets à jour la doc du scope ET docs/audit-reality/{{SCOPE}}.md
           (passe l'item en ✅ avec preuve fichier:ligne). Zéro mock présenté comme réel.
  ÉTAPE 5 — Journalise : mets à jour ralph/state/{{SCOPE}}.md (fait/en cours/reste, n° d'itération,
           commit SHA). Si A1–A7 tous verts pour le scope → écris `RALPH_DONE` en tête.
  ÉTAPE 6 — Commit atomique sur la branche de travail, message conventionnel en français.

INVARIANTS (sinon STOP et signale) :
  - Gates verts avant commit. Zéro secret. FOSS vs EE distingué. Surfaces disjointes.
  - Si un écart relève de l'EE (Onyx Enterprise) et NON du périmètre FOSS d'onix → ne le « simule » pas :
    documente-le comme limite FOSS et passe à l'item suivant.
  - Si un choix est ambigu ou structurant (refactor large, changement de contrat public) → STOP,
    écris la question dans le journal et rends la main.

SORTIE : un incrément commité + journal à jour. Pas de blabla ; le diff est la preuve.
```

---

## 2. Matrice Agent × Scope × Skills × MCP × Outils

Chaque scope est piloté par un sous-agent spécialisé. **Skills** = slash-commands
internes (`/security-review`, `/code-review`, `/verify`, `/simplify`, `deep-research`,
`claude-api`). **MCP** = serveurs externes (`Context7` = docs de libs à jour ;
`Microsoft_Learn` = Azure/Entra/Key Vault ; `github` = PR/CI). **Surfaces disjointes**
obligatoires (clones/branches isolés, merge par SHA).

| Scope | Discipline / sous-agent | Skills prioritaires | MCP utiles | Cibles de preuve |
|---|---|---|---|---|
| **access-gateway** | Sécurité plateforme (FastAPI/Redis) | `/security-review`, `/code-review`, `/verify`, `/simplify` | `Context7` (fastapi, starlette, redis-py), `github` | `pytest access-gateway/tests`, `/metrics`, cache↔ACL |
| **actions** | Backend + RGPD | `/security-review`, `/code-review`, `/verify` | `Context7` (fastapi, pydantic, python-docx, pytesseract), `github` | `pytest actions/tests`, audit HMAC, PII, DLP |
| **rag-prompts** | ML/RAG + prompt-eng | `/code-review`, `/verify`, `claude-api` | `Context7` (ragas, ollama), `github` | `pytest tests/rag`, `make rag-eval`, anti-injection |
| **deploy-ops** | SRE/DevOps/IaC | `/code-review`, `/verify` | `Microsoft_Learn` (AKS, bicep, Key Vault), `Context7` (helm, compose), `github` | `helm lint`, `compose config`, `bicep build`, Make |
| **monitoring** | Observabilité/SRE | `/code-review`, `/verify` | `Context7` (prometheus, grafana, loki, promtail), `github` | `compose config` monitoring, alertes vs métriques émises |
| **security-governance** | Sécurité/conformité/archi | `/security-review`, `deep-research`, `/code-review` | `Microsoft_Learn` (Entra/SSO/Key Vault), `github` | gates CI sécurité, claims de parité ↔ code |

**Règle d'emploi des MCP** : pour toute question sur une lib/API/cloud (FastAPI, Redis,
RAGAS, Helm, Azure…), consulter `Context7`/`Microsoft_Learn` **avant** de coder —
les connaissances internes peuvent être périmées. Ne jamais inventer une API.

---

## 3. Protocole qualité (le « vert obligatoire »)

```bash
# Gate complet (idéal, avant tout commit structurant)
make test            # lint + compose-validate + pytest + bandit + pip-audit + gitleaks + trivy

# Gates ciblés (itérations rapides)
make pytest          # ou: pytest actions/tests access-gateway/tests tests/rag
make bandit gitleaks pip-audit
make k8s-lint k8s-template      # Helm
make compose-validate           # docker-compose config
```
- **Un commit ne part que sur du vert.** Si une CVE apparaît, relever le pin
  (`tests/rag/requirements.txt`, images) — ne pas désactiver le gate.
- **Anti-régression RAG** : `make rag-eval-ci` (gate de qualité RAGAS) pour le scope `rag-prompts`.

---

## 4. Pattern multi-agent « surfaces disjointes » (AGENTS.md §5)

1. Un scope = une surface = une branche/clone isolé (`/tmp/ralph-<scope>`), pour éviter
   les collisions quand plusieurs boucles tournent en parallèle.
2. Merge par **SHA** vers la branche d'intégration (`claude/bold-fermat-5mz63c`), gates verts à chaque merge.
3. Les fichiers **partagés** (Makefile, docker-compose racine, `docs/DOCS_INDEX.md`) sont
   touchés par **un seul** scope à la fois (ou via PR séquentielle) pour éviter les conflits.
4. Chaque boucle journalise dans **son** `ralph/state/<scope>.md` (pas de fichier d'état partagé).

---

## 5. Definition of Done — niveau projet

Le projet est « production-ready entreprise » quand :
- Les **6 scopes** ont leur sentinelle `RALPH_DONE`.
- `docs/audit-reality/_VERDICT.md` ne liste plus aucun `❌`/`🕳️`.
- `make test` vert sur `main` + `make rag-eval-ci` vert + `helm lint`/`bicep build` verts.
- Le `RUNBOOK.md` permet à un opérateur tiers de déployer/exploiter **sans connaissance tacite**.
- Une **PR de synthèse** documente, scope par scope, l'écart de départ → l'état final (traçabilité).
