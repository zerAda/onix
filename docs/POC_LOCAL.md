# POC local — onix sur ta machine (64 Go) + SharePoint, pour 1-2 testeurs

> Faire tourner **toute la stack en local** (souverain, 0 € d'hébergement), **connectée
> à SharePoint** (indispensable), et l'**ouvrir à 1-2 testeurs**. Ta machine (64 Go +
> bon CPU) est **largement** au-dessus du minimum (16 Go). Embarquement : [`../AGENTS.md`](../AGENTS.md).

## 0. Ta machine suffit-elle ? OUI.
| Brique | RAM | Note |
|---|---|---|
| OpenSearch | ~4-8 Go | heap 2-4 Go |
| **Ollama** | 7b ≈ 6 Go · **14b ≈ 10 Go** | `make tune` choisit 14b sur 64 Go |
| Onyx (api+background+model-server) | ~5-8 Go | |
| Postgres+Redis+MinIO+gateway+actions+web | ~4-6 Go | |
| **Total (14b)** | **~25-35 Go** | il te reste ~30 Go |

CPU = le facteur de vitesse (pas la RAM). 7b ≈ 5-10 tok/s, 14b ≈ 2-5 tok/s sur bon CPU
(OK pour 1-2 testeurs ; le **cache** amortit les répétitions). **GPU NVIDIA → `make up GPU=1`**
= 5-10× plus rapide.

## 1. Prérequis
- **Docker + Docker Compose v2** (`docker compose version`).
- OS :
  - **Linux** (idéal) : Docker natif. Vérifie `sysctl vm.max_map_count` ≥ **262144** (OpenSearch) ;
    sinon `sudo sysctl -w vm.max_map_count=262144` (persistant : `/etc/sysctl.d/99-onyx.conf`). `make verify` le contrôle.
  - **Windows** : Docker Desktop + **WSL2** (mets le repo dans le FS WSL `~/…`, pas `/mnt/c`). GPU NVIDIA exploitable via WSL2.
  - **macOS** : Docker Desktop (CPU ; pas de GPU en conteneur → pour Metal, lancer Ollama natif, cf. [`RUNBOOK.md`](RUNBOOK.md) §8).
- **~25-30 Go de disque** (images + modèle + index).

## 2. Démarrer (4 commandes)
```bash
cd onix
make tune       # détecte 64 Go/CPU → écrit les réglages optimaux dans .env (modèle 14b)
make secrets    # secrets forts (.env, chmod 600)
make up         # démarre tout + pré-tire le modèle (1er run : plusieurs minutes)
make verify     # santé + câblage Onyx↔Ollama + test de génération
```
- Réponses **plus rapides** ? Avant `make up`, mets `OLLAMA_MODELS_TO_PULL=qwen2.5:7b-instruct` dans `.env`.
- GPU NVIDIA : `make up GPU=1`.
- Ouvre **http://localhost:3000** → **crée le compte admin IMMÉDIATEMENT** (1er compte = admin).
- À la 1ʳᵉ connexion, renseigne le LLM : **Provider = Ollama**, **URL = `http://ollama:11434`** (nom de service interne, pas `localhost`), **Modèle = `qwen2.5:14b-instruct`** (ou 7b).

## 3. 🎯 Connexion SharePoint (INDISPENSABLE) — pas-à-pas
Onyx indexe le site SharePoint via une **app Entra (app-only, Microsoft Graph)**.

### 3.a — Créer l'app Entra (2 voies)
**Voie script (rapide, sur ton poste az-connecté)** :
```bash
TENANT_ID=<ton-tenant> bash scripts/setup-sharepoint-app.sh
# imprime sp_client_id / sp_client_secret / sp_directory_id à coller dans Onyx
```
**Voie portail** (équivalent) :
1. Entra ID → **App registrations** → **New registration** : nom `onix-sharepoint`, *Single tenant*.
2. **API permissions** → Add → **Microsoft Graph** → **Application permissions** → **`Sites.Read.All`** → Add → **Grant admin consent** ✅.
3. **Certificates & secrets** → **New client secret** → copie la **Value** (non ré-affichée).
4. Note : **Application (client) ID** et **Directory (tenant) ID** (page Overview).

> RBAC : en **FOSS**, le connecteur **indexe** le site mais **ne réplique PAS les ACL
> par document** (permission-sync = Onyx **EE** + `Sites.FullControl.All` + **certificat**).
> Pour un POC à 1-2 testeurs c'est sans impact (ils voient le corpus indexé). Pour du
> cloisonnement par utilisateur, voir [`RBAC.md`](RBAC.md) (passerelle) ou l'EE.

### 3.b — Brancher dans Onyx
**Admin Panel → Connectors → SharePoint → New credential** :

| Champ | Valeur |
|---|---|
| `sp_client_id` | Application (client) ID de `onix-sharepoint` |
| `sp_client_secret` | la **Value** du secret client |
| `sp_directory_id` | Directory (tenant) ID |

Puis **config du connecteur** — champ **« Enter SharePoint sites »** :
```
https://gerep75008.sharepoint.com/sites/dev-assistant-client-360
```
- Donne l'**URL complète** du site (l'app n'a accès qu'à ce site). Sous-dossier possible :
  `…/dev-assistant-client-360/Documents%20partages/Clients`.
- Options : **indexer les bibliothèques/dossiers** (ON) ; **pages .aspx** (optionnel).
- (La résolution d'un site par *nom* ne couvre nativement que EN/ES/DE — en donnant
  l'**URL complète** on évite ce souci.)

### 3.c — Indexer + vérifier
- Lance l'indexation (le connecteur fait sites→drives→items via Graph, avec délta/retry).
- Suis l'avancement dans **Admin → Indexing**. Quand c'est à 100 %, **teste une question**
  sur un dossier client → la réponse doit **citer** les docs SharePoint.
- Échec d'auth ? Vérifie le **consentement admin** de `Sites.Read.All` et que le secret n'a pas expiré.

## 4. Tester l'assistant
Crée l'agent « Assistant Commercial 360 » (cf. [`AGENT_COMMERCIAL.md`](AGENT_COMMERCIAL.md))
ou pose directement des questions sourcées (résumé client, échéances, points d'attention).
Réponses **sourcées uniquement**, **un client à la fois**, **lecture seule**.

## 5. Ouvrir à 1-2 testeurs (sans exposer ta machine)
nginx est lié à `127.0.0.1` (sécurité). Deux options :
- **Tailscale Serve (RECOMMANDÉ — privé + TLS, zéro modif, souverain)** :
  ```bash
  # installe Tailscale, connecte-toi, puis :
  tailscale serve 3000
  ```
  Tes testeurs (ajoutés à ton **tailnet**) ouvrent `https://<ta-machine>.<ton-tailnet>.ts.net`.
  Rien d'exposé sur Internet, chiffré de bout en bout.
- **Même réseau (LAN)** : override fourni —
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.lan.yml up -d
  ```
  Testeurs : `http://<IP-LAN-de-ta-machine>:3000`. **Uniquement** réseau de confiance,
  `AUTH_TYPE=basic` + admin créé. (Ajoute `REQUIRE_EMAIL_VERIFICATION=false` en local.)

## 6. Qualité FR (optionnel pour le POC)
Pour de meilleures réponses sur corpus FR : applique [`PLAYBOOK_ONYX_RAG.md`](PLAYBOOK_ONYX_RAG.md)
(embedder `multilingual-e5-large`, reranker, analyseur `french`, `MAX_CHUNKS=8` — **un seul ré-index**).
Tu peux démarrer le POC avec les défauts et l'appliquer ensuite.

## 7. Dépannage
| Symptôme | Cause / fix |
|---|---|
| OpenSearch ne démarre pas (Linux) | `vm.max_map_count` < 262144 → `sudo sysctl -w vm.max_map_count=262144` |
| Port 3000 occupé | change `ONYX_HOST_PORT` dans `.env` |
| « Ollama injoignable » | URL = `http://ollama:11434` (pas localhost) ; `make verify` |
| 1ʳᵉ génération lente | normal (chargement modèle) ; `OLLAMA_KEEP_ALIVE` le garde chaud |
| SharePoint 401/403 | consentement admin `Sites.Read.All` manquant / secret expiré / mauvais tenant |
| Windows lent | mets le repo dans le FS **WSL2** (`~/…`), pas `/mnt/c` |
| Repartir propre | `make down` puis `make up` ; reset total `make destroy` (⚠ perd les données) |
| Voir les logs | `make logs` (ou `make ps` / `make stats`) |

## 8. Limites du POC local & passage en prod
1 machine = pas de HA ; vitesse = ton CPU (cache amorti) ; à laisser allumée pendant les
tests ; les testeurs dépendent de ta connexion. **C'est normal pour un POC.** Quand ça se
valide → on déplace **sans réécriture** sur **VM Azure** ([`DEPLOY_AZURE.md`](DEPLOY_AZURE.md))
ou **AKS** ([`HA_SCALING.md`](HA_SCALING.md)) — la même image, le même connecteur SharePoint,
la même couche onix. Tout est déjà prêt.
