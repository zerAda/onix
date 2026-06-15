# Architecture — onix — Stack IA souveraine (Onyx + Ollama)

## 1. Vue d'ensemble

Assistant RAG auto-hébergé : **Onyx** orchestre l'ingestion, l'indexation, la
recherche et le chat ; **Ollama** fournit l'inférence LLM **en local**. Toute la
chaîne tourne dans un réseau Docker privé ; seul le frontend est exposé, et
uniquement sur `127.0.0.1`.

## 2. Composants

| Service | Image (épinglée) | Rôle | Port hôte |
|---|---|---|---|
| `nginx` | `nginx:1.27-alpine` | Reverse proxy, point d'entrée unique | `127.0.0.1:3000→80` |
| `web_server` | `onyxdotapp/onyx-web-server:4.1.1` | Frontend Next.js | — (interne) |
| `api_server` | `onyxdotapp/onyx-backend:4.1.1` | API FastAPI + migrations Alembic | — |
| `background` | `onyxdotapp/onyx-backend:4.1.1` | Workers (indexation, tâches) | — |
| `inference_model_server` | `onyxdotapp/onyx-model-server:4.1.1` | Embeddings + reranking (et indexation) | — |
| `opensearch` | `opensearchproject/opensearch:3.6.0` | Index vectoriel + lexical | — |
| `relational_db` | `postgres:15.2-alpine` | Métadonnées, comptes, config | — |
| `minio` | `minio/minio:RELEASE.2025-07-23…` | Stockage objet (fichiers) | — |
| `cache` | `redis:7.4-alpine` | Cache éphémère (tmpfs) | — |
| `ollama` | `ollama/ollama:0.30.8` | **LLM local** (génération) | — (interne) |

## 3. Flux de données

**Ingestion / indexation**
```
Document → api_server → background → inference_model_server (embeddings)
                                   → OpenSearch (index)  + MinIO (fichier brut)
```
**Requête / chat**
```
Navigateur → nginx → api_server
   ├─ recherche : OpenSearch (top-k) + reranking (inference_model_server)
   └─ génération : Ollama (http://ollama:11434) avec le contexte récupéré
            → réponse sourcée renvoyée à l'UI
```

Tous les liens inter-services empruntent le réseau interne `onix-net`.
Aucune sortie réseau vers un fournisseur LLM externe.

## 4. Ports & volumes

- **Port publié** : `nginx` uniquement, sur `127.0.0.1:${ONYX_HOST_PORT}`.
- **Volumes persistants** : `db_volume` (Postgres), `opensearch-data`,
  `minio_data`, `file-system` (fichiers Onyx), `ollama_data` (modèles),
  `model_cache_huggingface` (modèles d'embedding), + volumes de logs.
- Redis : `tmpfs` (non persistant, par conception).

## 5. Décisions d'architecture (et pourquoi)

| Décision | Justification |
|---|---|
| Ollama **conteneurisé**, sans port hôte | Isolation maximale ; LLM joignable seulement par Onyx en interne ; reproductible. |
| **nginx** maintenu (config maison) | Le frontend Next.js ne proxifie pas `/api` ; nginx route `/api`→backend, `/`→front. Config réécrite (~50 l.) pour l'auditabilité. |
| Bind `127.0.0.1` | Aucune exposition réseau par défaut (choix : poste local). |
| `code-interpreter` retiré | Montait le socket Docker = root hôte. Risque inacceptable par défaut. |
| 1 seul model server | Économie de RAM sur poste CPU ; indexation mutualisée. Scalable. |
| OpenSearch heap réduit + `memory_lock=false` | Fiabilité de démarrage sur postes hétérogènes (Docker Desktop, rootless). |
| Images **épinglées** (pas `latest`) | Déploiements reproductibles, mises à jour maîtrisées. |
| `DISABLE_TELEMETRY=true` | Confidentialité / souveraineté. |

## 6. Modèle de menace (synthèse)

| Menace | Atténuation |
|---|---|
| Exfiltration de données vers un LLM cloud | LLM 100 % local (Ollama), pas d'API externe, télémétrie off. |
| Accès réseau non autorisé | Bind localhost ; aucun port data exposé ; Ollama interne. |
| Compromission via socket Docker | `code-interpreter` retiré par défaut. |
| Secrets fuités dans Git | `.env` gitignoré + `chmod 600` + scan gitleaks. |
| Élévation par énumération de comptes | `USER_DIRECTORY_ADMIN_ONLY=true`. |
| Dérive / supply-chain | Images épinglées, compose unique auditable, pas de `curl\|bash`. |

Hors périmètre par défaut (à traiter si exposition) : TLS, SSO/MFA, durcissement
hôte, segmentation réseau — voir [`SECURITY.md`](SECURITY.md) §6.

## 7. Positionnement vs un assistant commercial cloud d'entreprise

Un assistant commercial cloud (type Copilot Studio) : intégration Microsoft 365,
RAG SharePoint, SSO Entra ID, services Azure. **onix** vise la **même finalité**
(RAG commercial sourcé sur SharePoint) mais avec **inférence et données 100 % sur
site**, **open-source et gratuit**. Philosophie commune : *réponses sourcées,
sécurité d'abord*. Pour aligner l'auth, brancher Onyx sur **Entra ID (OIDC)**
(SECURITY.md §6) ; pour la source documentaire, le **connecteur SharePoint**
(`connectors/SHAREPOINT.md`).
```
Cloud d'entreprise : Teams/Copilot → SharePoint → Entra ID → Azure
onix (local)       : Navigateur   → Onyx (RAG)  → OpenSearch/MinIO → Ollama
                     source : connecteur SharePoint · SSO : OIDC Entra ID
                     (souverain, hors-ligne, zéro transfert de données)
```
Détail de la parité fonctionnelle (et de ses limites honnêtes) :
[`PARITE_ENTREPRISE.md`](PARITE_ENTREPRISE.md).
