# `ralph/` — Boucles Ralph d'industrialisation d'onix

> **Objectif ultime** : amener chaque scope d'`onix` au niveau **« premium grade
> entreprise, production-ready »**, de façon *mesurable*, *reproductible* et
> *sans jamais casser les portes qualité*.

## C'est quoi une « boucle Ralph » ici
Une **boucle Ralph** (d'après la technique du même nom) = on redonne **le même
prompt borné** à un agent, **itération après itération**, sur **une surface de
travail isolée**, jusqu'à ce qu'un **critère de fin objectif** (Definition of Done)
soit atteint et que **toutes les portes qualité soient vertes**. Entre deux
itérations, l'agent **relit son journal d'état** (`ralph/state/<scope>.md`) pour ne
**jamais refaire** ce qui est déjà fait et **reprendre** là où il s'est arrêté.

Ce dossier fournit, conformément au pattern multi-agent éprouvé du dépôt
(`AGENTS.md` §5 — *surfaces disjointes*) :

| Fichier | Rôle |
|---|---|
| [`ORCHESTRATION.md`](ORCHESTRATION.md) | **Le prompt maître étoffé** + la matrice *Agent × Scope × Skills × MCP × Outils* + la Definition of Done + le protocole qualité. **À lire en premier.** |
| [`loop.sh`](loop.sh) | Le **runner** de boucle Ralph, borné. **Durci (M13)** : commit **par chemins du scope** (pas `git add -A`), portes = **tests du scope + bandit + gitleaks + pip-audit**, **disjoncteurs** (gates rouges consécutifs / stagnation sans diff), **refus sur arbre sale** + **verrou** de concurrence. Journalise dans `state/`. |
| [`scopes/<scope>.md`](scopes/) | Le **PROMPT.md par scope** (la consigne rejouée à chaque itération), amorcé avec les écarts réels issus de l'audit [`../docs/audit-reality/`](../docs/audit-reality/). |
| [`state/<scope>.md`](state/) | Le **journal d'état** par scope (rempli par l'agent : fait / en cours / reste / sentinelle `RALPH_DONE`). |

## Lancer une boucle
```bash
# Une itération unique (dry-run de la consigne, recommandé pour démarrer)
./ralph/loop.sh access-gateway 1

# Boucle bornée (max 8 itérations) sur un scope
./ralph/loop.sh actions 8

# Tous les scopes, séquentiel (surfaces disjointes, gates verts entre chaque)
for s in access-gateway actions rag-prompts deploy-ops monitoring security-governance; do
  ./ralph/loop.sh "$s" 6
done
```

Le runner s'arrête sur un scope dès que son journal `state/<scope>.md` contient la
sentinelle **`RALPH_DONE`**, ou que le plafond d'itérations est atteint.

## Invariants non-négociables (rappel `AGENTS.md`)
- **Gates verts obligatoires** : `make test` (lint + compose-validate + pytest +
  bandit + pip-audit + gitleaks + trivy) doit rester vert. Un commit ne part que sur du vert.
  - **Ce que `loop.sh` applique *par itération*** (M13, honnêteté doc↔code) : tests du
    scope + `bandit` + `gitleaks` + `pip-audit` (offline). `trivy` (scan d'image, exige
    Docker), `lint` et la `compose-validate` complète restent du ressort de la **CI**
    (`.github/workflows/ci.yml`), qui re-valide tout sur la PR/`main`. La boucle ne
    prétend PAS exécuter `make test` complet — elle exécute le sous-ensemble offline
    suffisant pour qu'un commit « gates verts » ne soit pas un mensonge.
  - **Isolation** : l'isolation par *worktree* (`ORCHESTRATION.md §4`) reste
    **aspirationnelle** ; `loop.sh` la remplace pour l'instant par un **verrou de
    concurrence** (`flock`) + un **refus de tourner sur un arbre sale** + un **commit
    scopé** (jamais `git add -A`).
- **Zéro mock présenté comme réel**, **zéro secret en repo**, **commentaires en français**, **stdlib-first**, **FOSS vs EE** toujours distingué.
- **Ne pas casser les pièges** `AGENTS.md` §7 (Ollama par nom de service, `num_ctx` câblé, ordre cache↔ACL, Redis Azure TLS/noeviction, perm-sync EE).
