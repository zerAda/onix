# Dev container onix (Codespaces / VS Code)

Permet de développer et d'exécuter onix **sans installer Docker en local** : tout
tourne dans un conteneur Docker-capable (Docker-in-Docker), ouvrable dans le
navigateur via **GitHub Codespaces** ou en local via **VS Code Dev Containers**.

## Ouvrir

- **Codespaces (navigateur, zéro install)** : sur GitHub → bouton `Code` → onglet
  `Codespaces` → `Create codespace on <branche>`.
- **VS Code local** : extension *Dev Containers* → `Reopen in Container`.

## Choisir la taille de machine (IMPORTANT)

| Tâche | RAM conseillée |
|------|----------------|
| Suites offline (`make rag-test`, pytest actions/gateway) | 4 Go (machine par défaut) |
| Pile complète (`make up-local-prod` : Onyx + Ollama + OpenSearch + Postgres + MinIO) | **≥ 16 Go** (4 cœurs) |

`devcontainer.json` déclare `hostRequirements: 16gb` ; à la création du codespace,
sélectionner une machine ≥ 16 Go si la pile complète est visée. Un compte aux
quotas limités peut ne proposer que 4 Go — dans ce cas, s'en tenir aux suites
offline (la CI GitHub Actions vérifie déjà les portes de qualité en cloud).

## Commandes utiles (une fois dans le conteneur)

```bash
# Pré-vol + secrets + démarrage de la pile (machine ≥ 16 Go)
make tune && make secrets && make up-local-prod && make verify

# Suites offline (petite machine suffit)
make rag-deps           # dépendances de test RAG
make rag-test           # garde-fous + dataset (hors-LLM)

# Observabilité (optionnel)
make monitor-up
```

## Répartition de la vérification

- **Portes de qualité** (`pytest`, `pip-audit --strict`, `bandit`, `gitleaks`,
  `trivy`, `compose config`, `helm lint`) → déjà exécutées **en cloud** par
  `.github/workflows/ci.yml` à chaque push/PR. Pas besoin de ce conteneur pour ça.
- **Runtime léger** (l'image `onix-actions` démarre et sert `/health` + `/metrics`)
  → `.github/workflows/runtime-smoke.yml` (cloud, sans la pile lourde).
- **Pile complète / restore-drill / tests tenant live** → ce dev container
  (Codespaces ≥ 16 Go) ou une VM Docker.
