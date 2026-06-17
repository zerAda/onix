# onix — Stack IA souveraine auto-hébergée (Onyx + Ollama)

![Déploiement](https://img.shields.io/badge/D%C3%A9ploiement-Docker%20Compose-blue)
![LLM](https://img.shields.io/badge/LLM-Ollama%20local-black)
![RAG](https://img.shields.io/badge/RAG-Onyx%20v4.1.1-green)
![Données](https://img.shields.io/badge/Donn%C3%A9es-100%25%20locales-success)

> Assistant IA **100 % auto-hébergé et souverain** : **Onyx** (recherche RAG +
> chat sur vos documents) propulsé par **Ollama** (LLM exécuté localement).
> **Aucune donnée ne quitte la machine** — aucun appel à un fournisseur cloud,
> aucune télémétrie. Conçu pour un poste local, durci pour un usage exigeant.

Ce module est un **kit clé en main, auto-contenu et épinglé** : un seul fichier
`docker-compose.yml` auditable, images figées, secrets générés localement, et un
`Makefile` qui pilote tout. Pas de `curl | bash`, pas de dépendance cachée.

---

## Niveau entreprise — readiness (après remédiation par 6 workstreams)

onix vise la **parité+ entreprise** avec un assistant commercial cloud (type
Copilot Studio sur SharePoint), **en local et souverain**. Re-scoring : **8/8
dimensions au vert** (3 avec astérisque honnête — détails et preuves :
[`docs/PARITE_ENTREPRISE.md`](docs/PARITE_ENTREPRISE.md)).

- 🔐 **Sécurité / RGPD** : authz par appel, redaction PII, DLP + anti-SSRF, audit HMAC, rétention/effacement art.17 — [`docs/SECURITY_RGPD_ACTIONS.md`](docs/SECURITY_RGPD_ACTIONS.md)
- 🏢 **Prod / TLS** : `deploy/prod/` (Caddy HTTPS auto + OIDC Entra forcé + démarrage défaut-sûr) — [`docs/DEPLOY_PROD.md`](docs/DEPLOY_PROD.md)
- ☸️ **HA / scale** : `deploy/k8s/onix-ha/` (Helm — OpenSearch/Postgres/MinIO/Redis HA, HPA, file Celery) — [`docs/HA_SCALING.md`](docs/HA_SCALING.md)
- 🛡️ **RBAC** : `access-gateway/` (groupes Entra → Document Sets, deny-by-default) **+ filtre ACL par-document (FOSS) auto-synchronisé des permissions SharePoint via Graph** — [`docs/RBAC.md`](docs/RBAC.md)
- ⚡ **Cache RBAC-safe** : `access-gateway/app/cache.py` (clé HMAC par périmètre → 0 fuite ; Redis ou LRU ; bypass write/no-store) — **coût tokens + latence ↓** — [`docs/CACHE.md`](docs/CACHE.md)
- 🌊 **Streaming SSE** : `access-gateway/app/streaming.py` (relais token-par-token, **latence perçue ÷10** ; garde DUR incrémental + override final ; ACL appliquée au flux) — [`docs/STREAMING.md`](docs/STREAMING.md)
- 📊 **Observabilité** : `monitoring/` (Prometheus/Grafana + alertes) + **`/metrics` qualité de la passerelle** (citation, no-context, garde-fous, cache, P95) + CI bloquante (pytest/bandit/pip-audit/trivy) — [`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md)
- 🧪 **Garde-fous** : `tests/rag/` (red-team + éval + anti-régression du prompt) — [`docs/QA_GUARDRAILS.md`](docs/QA_GUARDRAILS.md)
- 🎚️ **Optimisation RAG / Ollama** (audit consultant : `num_ctx` câblé, embedding FR, reranker, capacité *mesurée*) — [`docs/RAG_OPTIMIZATION.md`](docs/RAG_OPTIMIZATION.md)
- 🛠️ **Playbook Onyx RAG** (embedder FR, reranker, analyseur, **ré-index unique**) — [`docs/PLAYBOOK_ONYX_RAG.md`](docs/PLAYBOOK_ONYX_RAG.md) · **Éval RAGAS souveraine** `make rag-eval` — [`docs/RAG_EVAL.md`](docs/RAG_EVAL.md)
- 🏁 **Comparatif vs Microsoft Copilot & AC360** (par secteur, honnête) — [`docs/COMPARATIF_COPILOT_AC360.md`](docs/COMPARATIF_COPILOT_AC360.md)
- 🔬 **Audit en profondeur d'Onyx v4.1.1** (7 dimensions, code réel, preuves byte-level : prod-ready premium **mais** RBAC/audit/chiffrement = EE-payant → justifie la couche `onix`) — [`docs/audit-onyx/00-VERDICT.md`](docs/audit-onyx/00-VERDICT.md)

> Validation : **red-team E2E 21/21 (qwen2.5:7b, post-filtre déployé dans access-gateway) · éval RAGAS `make rag-eval` (juge LOCAL) · `/metrics` qualité passerelle · HA multi-réplica prouvée + manifests validés server-side (vrai kube-apiserver) · pip-audit 0 CVE · bandit 0 · gitleaks 0 · helm lint 0 · caddy validate OK**. Clôture des 3 réserves : [`docs/PARITE_ENTREPRISE.md`](docs/PARITE_ENTREPRISE.md).

---

## Architecture

```
        Navigateur (http://localhost:3000)
                │   (lié à 127.0.0.1 uniquement)
                ▼
        ┌───────────────┐
        │     nginx     │  point d'entrée unique, config maison
        └───┬───────┬───┘
            │       │
       /api │       │ /
            ▼       ▼
   ┌────────────┐  ┌────────────┐
   │ api_server │  │ web_server │   (Onyx — FastAPI + Next.js)
   └─────┬──────┘  └────────────┘
         │  réseau Docker privé (onix-net) — rien d'exposé
         ├──────────────┬───────────────┬──────────────┐
         ▼              ▼               ▼              ▼
   ┌──────────┐  ┌────────────┐  ┌───────────┐  ┌──────────┐
   │ Postgres │  │ OpenSearch │  │   MinIO   │  │  Redis   │
   └──────────┘  └────────────┘  └───────────┘  └──────────┘
         │              ▲
         ▼              │ embeddings / reranking
   ┌──────────────┐     │
   │ background   │─────┘   ┌──────────────────────────────┐
   │ (indexation) │         │ inference_model_server       │
   └──────────────┘         └──────────────────────────────┘
                                       
   ┌───────────────────────────────────────────────────────┐
   │  ollama  (LLM LOCAL — http://ollama:11434, INTERNE)    │
   │  aucun port publié sur l'hôte ; modèles dans un volume │
   └───────────────────────────────────────────────────────┘
```

Seul **nginx** publie un port, et **uniquement sur `127.0.0.1`**. Ollama et
toutes les briques de données restent sur le **réseau Docker interne**.

---

## Prérequis

| Élément | Détail |
|---|---|
| Docker | Docker Engine + **Docker Compose v2** (`docker compose`) |
| RAM | **16 Go minimum** (24 Go+ confortable). `make detect` calibre tout. |
| Disque | ~20 Go (images + modèles + index) |
| Linux | `vm.max_map_count >= 262144` (OpenSearch) — voir Dépannage |
| GPU | Optionnel (NVIDIA). Par défaut **CPU**. |

---

## Démarrage rapide (4 étapes)

```bash
cd onix

make tune       # 1. Détecte CPU/RAM/GPU et ÉCRIT les réglages optimaux dans .env
make secrets    # 2. Génère des secrets forts (chmod 600)
make up         # 3. Démarre la stack + pré-télécharge les modèles Ollama
                #    (GPU=1 si GPU NVIDIA ; PERF=1 si machine confortable)
make verify     # 4. Contrôle santé + câblage Onyx↔Ollama + test de génération
```

> `make tune` exploite au mieux votre matériel (plus gros modèle qui tient,
> limites proportionnelles à la RAM, Flash Attention + cache KV q8_0) tout en
> gardant une marge OS. Détails et compromis : [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md).
> Aperçu sans rien écrire : `make detect`.

Puis ouvrez **http://localhost:3000**. Le **premier compte créé devient
administrateur**.

> ⚠ **Créez ce compte admin IMMÉDIATEMENT après `make up`** (avant toute autre
> personne ayant accès à la machine). Tant qu'aucun compte n'existe, n'importe
> quel visiteur de l'instance peut s'inscrire en premier et **devenir admin**
> (prise de contrôle d'une instance vierge). En accès strictement `127.0.0.1`
> le risque est limité à la machine ; **dès que vous quittez localhost**, activez
> en plus `REQUIRE_EMAIL_VERIFICATION=true` et `VALID_EMAIL_DOMAINS=…`
> (cf. [`docs/SECURITY.md`](docs/SECURITY.md) §3 & §6).

> 1er lancement : prévoir plusieurs minutes (téléchargement des images, des
> modèles Ollama et des modèles d'embedding). `make verify` confirme l'état.

### Connexion Onyx ↔ Ollama (assistant de 1ère connexion)

À la première connexion, Onyx demande le fournisseur de modèle. Renseignez :

| Champ | Valeur |
|---|---|
| Provider | **Ollama** |
| API Base URL | **`http://ollama:11434`** *(nom de service interne, pas `localhost`)* |
| Modèle | `llama3.2:3b` *(ou celui recommandé par `make detect`)* |

Le câblage réseau est déjà en place et **vérifié par `make verify`** : il ne
reste que ces 3 champs. Détails et capture pas-à-pas : [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

---

## Sécurité (résumé)

- **Localhost only** : nginx lié à `127.0.0.1`. Rien d'accessible depuis le réseau.
- **Ollama isolé** : aucun port hôte ; joignable uniquement par Onyx en interne.
- **Secrets** : générés localement, `.env` gitignoré + `chmod 600`. Zéro secret en repo.
- **Surface réduite** : `code-interpreter` (montait le **socket Docker**), `certbot`
  et `mcp_server` **retirés par défaut**.
- **Zéro fuite** : `DISABLE_TELEMETRY=true`, aucun LLM cloud, données 100 % locales.
- **Auth** : comptes locaux (`basic`) ; énumération des comptes réservée aux admins.

Baseline complète et checklist : [`docs/SECURITY.md`](docs/SECURITY.md).

---

## Exploitation

| Action | Commande |
|---|---|
| État des services | `make ps` |
| Consommation ressources | `make stats` |
| Journaux | `make logs` |
| Mettre à jour (tag épinglé) | `make update` |
| Sauvegarder / restaurer | `make backup` / `make restore DIR=backups/…` |
| Tout arrêter | `make down` |
| (Re)calibrer pour le matériel | `make tune` — voir [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md) |
| Profil GPU NVIDIA / haut débit | `make up GPU=1` / `make up PERF=1` |

Runbook détaillé (upgrade, incidents, scaling, Ollama natif) : [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

---

## Arborescence

```
onix/
├── docker-compose.yml        # stack complète durcie (fichier unique auditable)
├── docker-compose.gpu.yml    # override GPU NVIDIA (optionnel)
├── docker-compose.performance.yml  # override haut débit (indexation dédiée)
├── env.template              # gabarit → copié en .env (gitignoré)
├── Makefile                  # pilotage une-commande
├── nginx/onyx.conf           # reverse proxy maison (localhost)
├── scripts/                  # detect-hardware(.sh/.ps1), gen-secrets, pull-models, verify, backup/restore
├── prompts/                  # prompt système de l'agent + exemples de questions
└── docs/
    ├── AGENT_COMMERCIAL.md   # assistant commercial RAG sourcé (cas d'usage)
    ├── connectors/SHAREPOINT.md  # connexion SharePoint + RBAC
    ├── PARITE_ENTREPRISE.md  # parité vs assistant cloud d'entreprise (honnête)
    ├── ARCHITECTURE.md · SECURITY.md · PERFORMANCE.md · RUNBOOK.md · RGPD.md
```

---

## Assistant commercial sur SharePoint (le cœur fonctionnel)

onix est l'**équivalent open-source et souverain d'un assistant commercial cloud
d'entreprise** (type Microsoft Copilot Studio) : un agent qui fait du **RAG sourcé
sur vos documents clients SharePoint**, **un client à la fois**, **en lecture seule**,
**sans rien inventer**, et **100 % en local** (inférence Ollama, données sur site).

- 🔌 **Connexion SharePoint** : [`docs/connectors/SHAREPOINT.md`](docs/connectors/SHAREPOINT.md)
  (app Entra ID, permissions Graph, RBAC par utilisateur).
- 🧠 **Agent « Assistant Commercial 360 »** : [`docs/AGENT_COMMERCIAL.md`](docs/AGENT_COMMERCIAL.md)
  (résumé client, préparation RDV, points d'attention, brouillon mail, recherche
  documentaire/juridique… — réponses sourcées).
- 📊 **Parité fonctionnelle honnête** (ce qui est natif / config / EE / roadmap) :
  [`docs/PARITE_ENTREPRISE.md`](docs/PARITE_ENTREPRISE.md).

> ⚠️ Le **trimming d'accès par utilisateur** (RBAC fin par document) repose sur la
> *permission sync* SharePoint d'Onyx, **réservée à l'édition Cloud/Enterprise**.
> En édition gratuite (FOSS), prévoir un index à accès uniforme ou des connecteurs
> par groupe (détails et stratégies dans le guide SharePoint).
