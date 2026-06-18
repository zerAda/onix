---
name: update-scope-docs
description: Met à jour la doc-infra agents d'onix pour un scope après une modif de code (dossier docs/scopes/<scope>.md + docs/audit-reality/<scope>.md + ralph/state/<scope>.md), régénère llms-full.txt si besoin et fait passer les gardes au vert. À utiliser dès qu'on a touché le code d'un scope (access-gateway, actions, rag-prompts, monitoring, deploy-ops, security-governance), quand `make docs-freshness` signale un drift, ou pour une revue périodique due (`make docs-check`).
---

# Mettre à jour la doc-infra d'un scope (onix)

Objectif : garder la doc-infra **vraie et non-divergente** (« à chaque action, on
vérifie »). Source de vérité : `docs/scopes/scopes.json`. Référence : `CLAUDE.md`
§ « Tenir cette doc-infra à jour », `docs/scopes/README.md`.

## 1. Identifier le(s) scope(s) concerné(s)
- Lis le registre `docs/scopes/scopes.json` : chaque scope a `code` (préfixes),
  `dossier`, `audit`, `state`, `owner`.
- Mappe les fichiers que tu as modifiés à leur scope via les préfixes `code`
  (ex. `access-gateway/...` → scope `access-gateway`). Plusieurs scopes possibles.
- Doute sur le périmètre ? lance `make docs-freshness` : il liste les drifts.

## 2. Mettre à jour les TROIS fichiers du scope (ne pas en sauter)
Pour chaque scope touché :
1. **Dossier** `docs/scopes/<scope>.md` — réaligne sur le code réel : §2 *Carte du
   code* (chemin → rôle, entrypoints), §3 *Commandes*, §4 *Tests & preuves*,
   §5 *Invariants* si un piège a changé. Garde le gabarit (sections `## 1.`…`## 10.`)
   et la ligne `**Owner**` intacts.
2. **Audit** `docs/audit-reality/<scope>.md` — ajoute/maj la preuve `fichier:ligne`
   de ce que tu affirmes (zéro mock présenté comme réel).
3. **Journal** `ralph/state/<scope>.md` — note fait/en cours/reste + le SHA.

Revue périodique (si `make docs-check` avertit « revue échue ») : relis le dossier,
corrige ce qui a dérivé, puis mets `last_reviewed` à la date du jour dans le registre.

## 3. Régénérer la carte embarquée si besoin
Si tu as modifié `AGENTS.md`, `CLAUDE.md`, `docs/scopes/README.md` ou un dossier de
scope :
```bash
make llms-full        # régénère llms-full.txt (sinon docs-check échoue)
```

## 4. Vérifier (obligatoire avant commit)
```bash
make docs-check       # registre + gabarit + owner + liens + llms-full (+ revue)
make docs-freshness   # anti-drift : code de scope ⇒ doc MAJ
```
Les deux doivent être **verts**. Sinon, corrige (le message dit quoi).

## 5. Cas particuliers
- **Nouveau scope** : ajoute une entrée dans `docs/scopes/scopes.json` (owner,
  last_reviewed, code, dossier, audit, state), crée `docs/scopes/<scope>.md` à partir
  du gabarit (`docs/scopes/README.md`), crée `docs/audit-reality/<scope>.md` et
  `ralph/state/<scope>.md`, puis `make llms-full` + `make docs-check`.
- **Dérogation légitime** (modif de code SANS impact doc, rare) : justifie-la avec
  `[docs-skip:<scope>]` dans le message de commit (tracé, audité).
- **Jamais** : laisser un drift silencieux, inventer une preuve, présenter une feature
  EE comme FOSS.

## 6. Commit
Message conventionnel en français ; le hook pre-commit rejoue docs-check + anti-drift.
