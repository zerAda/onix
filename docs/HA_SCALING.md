# Haute disponibilité, puissance & scalabilité — onix sur Kubernetes

> **But.** Faire passer la dimension « Architecture, puissance & scalabilité » au
> **vert** : haute disponibilité (HA), scale-out horizontal, élasticité
> automatique (HPA), suppression des points uniques de défaillance (SPOF) et
> continuité d'activité **sans interruption** — de niveau entreprise.
>
> **Comment.** Un chart Helm dédié — [`deploy/k8s/onix-ha/`](../deploy/k8s/onix-ha/)
> — qui **reprend la structure du chart Helm Onyx OFFICIEL**
> ([`deployment/helm/charts/onyx`](https://github.com/onyx-dot-app/onyx/tree/main/deployment/helm/charts/onyx),
> v0.6.0) et **inverse ses défauts** : là où l'amont livre `replicaCount: 1`,
> `autoscaling.enabled: false` et un socle de données mono-nœud, **onix-ha**
> impose `replicas >= 2`, **HPA partout**, un **socle de données multi-nœuds**,
> des **PodDisruptionBudgets**, des **requests/limits** et la **continuité à
> chaud**.

Ce document décrit l'**architecture cible**, le **dimensionnement**,
l'**élasticité**, la **suppression des SPOF**, les **procédures**, le **hook code
attendu côté `onix-actions`**, et ce qui **reste à valider sur cluster réel**.

---

## 1. Pourquoi cette dimension passe au vert « by design »

| Exigence entreprise | Réponse onix-ha | Où |
|---|---|---|
| **HA des services applicatifs** | api, web, background, model servers (inférence + indexation) et **onix-actions** en `replicas >= 2`, étalés sur des nœuds distincts (anti-affinité) | `values.yaml` + Deployments |
| **Élasticité automatique** | **HPA** (CPU + mémoire) sur tous les services stateless, `minReplicas: 2`, `maxReplicas` 6→12 | `templates/_helpers.tpl` (`onix.hpa`) |
| **Scale-out des traitements longs** | **File asynchrone Celery** (worker + broker RabbitMQ) pour OCR de gros PDF / audits par lot, scalable indépendamment | `templates/actions-queue.yaml` |
| **HA du socle de données** | **OpenSearch >= 3 nœuds** (réplica de shard >= 1), **Postgres HA** (CloudNativePG, 3 instances), **MinIO distribué** (4 nœuds, erasure coding), **Redis HA** (operator, réplication/sentinel) | sous-charts officiels + `postgres-cluster.yaml` |
| **Pas d'interruption en maintenance** | **PodDisruptionBudgets** sur chaque composant (`minAvailable: 1`) | `templates/_helpers.tpl` (`onix.pdb`) |
| **Point d'entrée chiffré** | **Ingress + TLS** (cert-manager) | `templates/ingress.yaml` |
| **Continuité / reprise** | **Snapshots OpenSearch à chaud**, **PITR Postgres** (WAL continu + base backups CNPG), **miroir MinIO** — tous **sans arrêt** | CronJobs + CNPG `ScheduledBackup` |
| **Maîtrise des ressources** | `requests`/`limits` explicites sur **tous** les conteneurs | `values.yaml` |

**Suppression des SPOF** (cf. §5) : chaque brique mono-instance du
`docker-compose` (un seul `api`, un seul OpenSearch `single-node`, un seul
Postgres, un seul MinIO, un Redis, un SQLite local dans `actions`) devient
**redondée et/ou répliquée**, et l'état local de `onix-actions` est **déporté**
vers le socle partagé.

---

## 2. Architecture cible

```
                          Internet
                             │  (TLS, cert-manager)
                    ┌────────▼─────────┐
                    │   Ingress nginx  │  /api,/openapi.json → api ;  / → web
                    └────────┬─────────┘
        ┌────────────────────┼───────────────────────────┐
        │                    │                            │
   ┌────▼────┐          ┌────▼─────┐                 ┌────▼─────────┐
   │ web x2+ │          │ api x2+  │  (HPA 2→10)     │ onix-actions │ (HPA 2→8)
   │ (HPA)   │          │ (FastAPI)│                 │   x2+        │
   └─────────┘          └────┬─────┘                 └────┬─────────┘
                             │                            │  enqueue
   ┌─────────────┐     ┌─────▼──────┐               ┌─────▼─────────┐
   │ background  │     │ model srv  │               │ actions-worker│ (HPA 2→12)
   │ x2+ (HPA)   │     │ inférence  │ x2+ (HPA)      │ Celery        │
   │ indexation  │     │ + indexation                └─────┬─────────┘
   └──────┬──────┘     └─────┬──────┘                      │ AMQP
          │                  │                       ┌─────▼─────────┐
          │                  │                       │ broker (RMQ)  │
          │                  │                       └───────────────┘
   ┌──────▼──────────────────▼──────────────────────────────────────────┐
   │                       SOCLE DE DONNÉES HA                           │
   │  OpenSearch >=3 nœuds   Postgres CNPG 3 inst.   MinIO distribué x4  │
   │  (réplica shard >=1)    (1 primaire+2 réplicas) (erasure coding)    │
   │  Redis HA (operator, réplication/sentinel)      Ollama (LLM, PVC)   │
   └─────────────────────────────────────────────────────────────────────┘
        │ snapshot S3        │ WAL + base backup        │ mc mirror
   ┌────▼────────────────────▼──────────────────────────▼──────────────┐
   │           CONTINUITÉ — sauvegardes À CHAUD (sans arrêt)            │
   │  CronJob snapshot OS   CNPG ScheduledBackup/PITR   CronJob MinIO   │
   └────────────────────────────────────────────────────────────────────┘
```

**Fidélité à l'amont.** Les noms de services (`api`, `webserver`,
`background`, `inferenceCapability`/`indexCapability`) et les **sous-charts**
data-tier (`cloudnative-pg`, `opensearch`, `redis-operator` + `redis`, `minio`)
sont **ceux du chart Onyx officiel**. onix-ha ajoute ce que l'amont ne couvre
pas : **onix-actions** (Deployment + Service + HPA + PDB), la **file Celery**,
l'**Ingress applicatif** (routes `/api` vs `/`), et les **CronJobs de
continuité**.

---

## 3. Dimensionnement (replicas, HPA, ressources)

Valeurs **par défaut** du chart (`deploy/k8s/onix-ha/values.yaml`) — point de
départ raisonnable pour une PME ; à ajuster selon la charge réelle.

| Composant | replicas (min→max HPA) | requests (cpu/mem) | limits (cpu/mem) | Cible HPA |
|---|---|---|---|---|
| `api` | 2 → 10 | 500m / 1Gi | 2 / 3Gi | CPU 70 % + mem 80 % |
| `webserver` | 2 → 8 | 200m / 512Mi | 1 / 1Gi | CPU 70 % |
| `background` | 2 → 8 | 1 / 2Gi | 4 / 5Gi | CPU 75 % |
| `inference-model-server` | 2 → 6 | 2 / 3Gi | 4 / 10Gi | CPU 75 % |
| `index-model-server` | 2 → 6 | 2 / 3Gi | 6 / 6Gi | CPU 75 % |
| `onix-actions` | 2 → 8 | 250m / 256Mi | 1 / 512Mi | CPU 70 % |
| `actions-worker` (Celery) | 2 → 12 | 500m / 512Mi | 2 / 1Gi | CPU 70 % |

**Socle de données** (sous-charts, overrides HA fournis dans `values.yaml`) :

| Composant | Réplication | Stockage | Notes |
|---|---|---|---|
| OpenSearch | **3 nœuds** | 100Gi/nœud | réplica de shard >= 1 (redondance données) |
| Postgres (CNPG) | **3 instances** (1 primaire + 2 réplicas) | 50Gi/instance | bascule auto, réplication streaming |
| MinIO | **4 nœuds** distribués | 100Gi/nœud | erasure coding (tolère 1–2 pertes) |
| Redis | **3 répliques** (operator) | 5Gi | réplication / sentinel |
| Ollama | 1 (scalable) | 50Gi (modèles) | recharge modèles par réplique ; PDB |

> **Capacité = `replicas × requests`.** Provisionner les nœuds du cluster pour la
> somme des `requests` à `minReplicas`, et prévoir la marge jusqu'à `maxReplicas`
> (autoscaler de nœuds recommandé : Cluster Autoscaler / Karpenter).

---

## 4. Élasticité (comment ça scale)

1. **Horizontal Pod Autoscaler (HPA)** — chaque service stateless porte un HPA
   `autoscaling/v2` qui ajoute/retire des pods selon l'utilisation CPU (et
   mémoire pour l'API). Sous charge, l'API passe de 2 à 10 pods ; à vide, retour
   à 2. **Pré-requis cluster : `metrics-server`** installé.
2. **File asynchrone** — les traitements **longs** de `onix-actions` (OCR de gros
   PDF scannés, audits par lot) sont **mis en file** (Celery + RabbitMQ) au lieu
   d'être traités dans la requête HTTP. Le pool `actions-worker` scale
   indépendamment (2 → 12) selon la profondeur de file / le CPU. L'API reste
   réactive (pas de requête bloquée plusieurs minutes).
3. **Scale-out manuel / programmé** — `kubectl scale deploy/<svc> --replicas=N`,
   ou ajuster `maxReplicas` dans `values.yaml` puis `helm upgrade`.
4. **Scale du socle de données** — OpenSearch et MinIO ajoutent des nœuds ;
   Postgres ajoute des réplicas (lecture) via CNPG ; Redis via l'operator.

---

## 5. Suppression des points uniques de défaillance (SPOF)

| SPOF dans `docker-compose` (poste local) | Résolution onix-ha |
|---|---|
| `api` unique | `replicas >= 2` + HPA + PDB ; migrations sorties dans un **Job** (pas de course entre répliques) |
| `web` / `background` / model servers uniques | `replicas >= 2` + HPA + PDB |
| OpenSearch **`discovery.type=single-node`** | **cluster >= 3 nœuds**, réplica de shard >= 1 |
| Postgres **mono-conteneur** | **CloudNativePG 3 instances**, bascule automatique du primaire |
| MinIO **mono-nœud** | **MinIO distribué 4 nœuds**, erasure coding |
| Redis **mono-conteneur** | **Redis HA** (operator, réplication/sentinel) |
| `onix-actions` avec **SQLite local** (état non partagé) | État **déporté** vers Postgres partagé (cf. §7) → toutes les répliques voient le même état |
| Maintenance = arrêt (drain de nœud) | **PodDisruptionBudget** `minAvailable: 1` par composant → jamais zéro pod pendant un drain |
| Sauvegarde = **arrêt de la stack** (`scripts/backup.sh`) | **Sauvegardes à chaud** (snapshots OS, PITR Postgres, miroir MinIO) **sans interruption** (§6) |
| Nœud unique | **anti-affinité** (étale les répliques sur des nœuds différents) |

> Note d'honnêteté : le `scripts/backup.sh` existant (mono-poste) **arrête**
> la stack pour archiver les volumes. En production K8s, on n'utilise plus cette
> approche : la continuité passe par les mécanismes **à chaud** ci-dessous.

---

## 6. Continuité d'activité — sauvegardes À CHAUD (sans interruption)

Toutes les sauvegardes s'exécutent **cluster en ligne**, sans couper le service.

### 6.1 OpenSearch — snapshots à chaud (S3/MinIO)
`templates/cronjob-opensearch-snapshot.yaml` (`backups.opensearchSnapshot`) :
- enregistre (idempotent) un **repository `s3`** pointant sur MinIO ;
- déclenche un **snapshot incrémental** horodaté (`wait_for_completion`) ;
- **purge** les snapshots plus vieux que `retentionDays` (14 j par défaut).
- Les snapshots OpenSearch sont **conçus pour le hot backup** : le cluster
  continue d'indexer et de répondre pendant l'opération.
- Pré-requis : **plugin `repository-s3`** actif sur les nœuds OS + endpoint S3
  configuré (`opensearch.config.s3SnapshotEndpoint`).

### 6.2 Postgres — PITR (WAL continu + base backups)
Géré **nativement par CloudNativePG** (`postgresql.cluster.backup`) :
- **WAL archiving continu** vers MinIO/S3 → **Point-In-Time Recovery** à la
  seconde près entre deux base backups ;
- **base backups planifiés** (`ScheduledBackup`, quotidien par défaut, format
  cron CNPG **6 champs**) ;
- **rétention** par fenêtre de récupération (`retentionPolicy: 30d`).
- Aucune interruption : Barman opère sur un réplica / via streaming.

### 6.3 MinIO — miroir à chaud
`templates/cronjob-minio-mirror.yaml` (`backups.minioMirror`) : `mc mirror`
réplique le bucket de fichiers Onyx vers un bucket de sauvegarde, **à chaud**.
Pour le **3-2-1** (copie hors-site), pointer l'alias destination vers un autre
endpoint S3.

### 6.4 Procédures de restauration (résumé)
- **OpenSearch** : `POST /_snapshot/<repo>/<snap>/_restore` (réindex ciblé ou
  global, cluster en ligne).
- **Postgres** : `kubectl cnpg` / CRD de **recovery** CNPG → nouveau Cluster
  restauré à un instant T (PITR) depuis l'object store.
- **MinIO** : `mc mirror` inverse (bucket de sauvegarde → bucket de prod).

---

## 7. Hook code attendu côté `onix-actions` (pour l'intégrateur)

> **Périmètre WS4 :** ce chantier **ne modifie pas** `actions/app/`. Les
> changements ci-dessous sont **décrits** pour l'intégrateur ; le chart est déjà
> prêt à les consommer (variables d'env, worker Celery, broker, backend Postgres).

`onix-actions` est aujourd'hui **mono-instance par conception locale** : il
persiste dans un **SQLite** (`actions/app/admin_state.py`, `tasks.py`,
`usage_tracker.py`) et traite l'OCR **dans la requête HTTP** (`ocr.py`,
`main.py:audit_file_endpoint`). Pour le rendre **stateless + scale-out**, deux
évolutions :

### 7.1 Déporter l'état (stateless) — SPOF SQLite
- **Quoi.** Remplacer le backend SQLite par **PostgreSQL partagé** (le même
  cluster CNPG, base/schéma dédié `onix_actions`).
- **Où.** `admin_state._connect()` est le **point central** (importé par
  `tasks.py`). Introduire une couche d'accès paramétrée par `ONIX_DB_BACKEND`
  (`sqlite` par défaut local ; `postgres` en K8s) — le chart fournit déjà
  `ONIX_DB_BACKEND=postgres` et les variables `POSTGRES_*` via ConfigMap/Secret.
  Utiliser SQLAlchemy ou `psycopg` ; conserver le schéma actuel (tables
  `admin_state`, `admin_audit`, `tasks`, table d'usage).
- **Effet.** Toutes les répliques de `onix-actions` partagent le **même** état
  (kill-switch, blocages, tâches, usage) → cohérence en HA.
- **Alternative à l'écriture.** Les fichiers `.docx` générés (`docgen.py`,
  `ONIX_JOBS_DIR=/data/jobs`) doivent aller sur un **stockage partagé** :
  écrire dans **MinIO** (déjà disponible) plutôt qu'un volume local, ou monter
  un PVC **ReadWriteMany**. Sinon `GET /download/{job_id}` peut tomber sur une
  réplique qui n'a pas le fichier.

### 7.2 File asynchrone (scale-out des traitements longs)
- **Quoi.** Ajouter un module **`actions/app/celery_app.py`** définissant
  l'app Celery (le chart lance déjà `celery -A app.celery_app.celery worker`) :
  ```python
  # actions/app/celery_app.py  (hook à AJOUTER côté code)
  import os
  from celery import Celery
  celery = Celery(
      "onix_actions",
      broker=os.environ["ONIX_BROKER_URL"],          # fourni par le chart
      backend=os.environ.get("ONIX_RESULT_BACKEND",  # ex: db+postgresql://...
                             "rpc://"),
  )

  @celery.task(name="audit_file_async")
  def audit_file_async(file_bytes: bytes, filename: str, reference, opts: dict):
      # Réutilise la logique existante: ocr.extract(...) -> extract_canonical_fields
      # -> audit_engine.audit(...). Renvoie le verdict (persisté en base).
      ...
  ```
- **Quoi (API).** Ajouter des endpoints **`POST /audit/file/async`** (et idem
  OCR par lot) qui : (1) `validate_upload`, (2) `audit_file_async.delay(...)`,
  (3) renvoient `202 Accepted` + `task_id` ; plus **`GET /tasks/{task_id}`**
  pour le statut/résultat. Ajouter un flag `ONIX_QUEUE_ENABLED` (déjà posé par
  le chart) pour activer le mode async sans casser l'API synchrone existante.
- **Dépendance.** Ajouter `celery` (+ `redis`/`amqp` selon broker) à
  `actions/requirements.txt`.
- **Effet.** Les audits/OCR longs ne bloquent plus l'API ; le pool
  `actions-worker` absorbe les pics et **scale** (HPA 2 → 12). Idempotence et
  retries gérés par Celery.

> Le chart est **conçu pour ces hooks** : variables `ONIX_BROKER_URL`,
> `BROKER_PASSWORD`, `ONIX_DB_BACKEND`, `ONIX_QUEUE_ENABLED`, le **worker** et le
> **broker** sont déjà déployés. Tant que le hook code n'est pas intégré, mettre
> `actionsQueue.enabled=false` et `ONIX_DB_BACKEND=sqlite` (mono-réplique).

---

## 8. Installation (production)

```bash
# 1. (Optionnel) Rafraîchir les sous-charts officiels vendorisés (CNPG, OpenSearch,
#    Redis, MinIO) depuis Chart.lock. Ils sont DÉJÀ présents dans charts/*.tgz
#    (chart auto-porté, installable hors-ligne) ; cette commande ne sert qu'à les
#    mettre à jour si les versions de Chart.yaml/Chart.lock changent.
helm dependency build deploy/k8s/onix-ha

# 2. Créer le Secret applicatif HORS-CHART (jamais de secret dans Git).
#    NB: POSTGRES_PASSWORD doit valoir le mot de passe de l'utilisateur applicatif
#    Postgres (cf. secret CNPG à l'étape 2bis) pour qu'Onyx s'y connecte.
kubectl create namespace onix
kubectl -n onix create secret generic onix-secrets \
  --from-literal=POSTGRES_PASSWORD='…'        --from-literal=OPENSEARCH_ADMIN_PASSWORD='…' \
  --from-literal=REDIS_PASSWORD='…'           --from-literal=S3_AWS_ACCESS_KEY_ID='…' \
  --from-literal=S3_AWS_SECRET_ACCESS_KEY='…' --from-literal=SECRET='…' \
  --from-literal=USER_AUTH_SECRET='…'         --from-literal=ONIX_ACTIONS_API_KEY='…' \
  --from-literal=BROKER_PASSWORD='…'

# 2bis. (Optionnel mais recommandé) Secret CNPG d'identifiants applicatifs
#    (format basic-auth : clés `username`/`password`) pour MAÎTRISER le mot de
#    passe Postgres et le faire correspondre à POSTGRES_PASSWORD ci-dessus.
#    Sans ce Secret, CNPG génère un mot de passe aléatoire (à récupérer ensuite).
kubectl -n onix create secret generic onix-pg-app \
  --type=kubernetes.io/basic-auth \
  --from-literal=username='postgres' --from-literal=password='…'  # == POSTGRES_PASSWORD

# 3. Activer le socle de données HA + référencer les Secrets, puis déployer.
helm upgrade --install onix deploy/k8s/onix-ha -n onix \
  --set secrets.existingSecret=onix-secrets \
  --set postgresql.operator.enabled=true --set postgresql.cluster.enabled=true \
  --set postgresql.cluster.appSecret=onix-pg-app \
  --set opensearch.enabled=true \
  --set redisOperator.enabled=true --set redis.enabled=true \
  --set minio.enabled=true \
  --set ingress.host=onix.mondomaine.fr
```

> En **GitOps** (Argo CD / Flux), gérer le Secret via un *SealedSecret* / *External
> Secrets Operator* — jamais en clair dans le dépôt.

---

## 9. Validation

### Validable **sans cluster** (fait dans ce chantier)
- `helm lint deploy/k8s/onix-ha` → **0 erreur** (avec les sous-charts officiels
  vendorisés).
- `helm template …` rend des YAML valides, re-parsés par PyYAML **sans erreur**,
  tous avec `kind` + `apiVersion` + `metadata.name` :
  - **36 documents** avec les défauts (data-tier off) ;
  - **39 documents** avec le data-tier activé (nos CRD : CNPG `Cluster`,
    `ScheduledBackup`) ;
  - **47 documents** data-tier activé **+ sous-charts officiels réels** (CNPG,
    OpenSearch, MinIO StatefulSet, Redis operator se rendent réellement).
  HPA `minReplicas: 2` sur les 7 services stateless ; **8 PodDisruptionBudgets**.
- `gitleaks` → **0 secret** (aucune valeur sensible dans le chart : tout en Secret K8s).

### Exige un **cluster réel** (NON couvert ici — à valider en recette)
- Comportement **HPA** sous charge réelle (scale up/down, seuils) — nécessite
  `metrics-server` + un cluster + un test de charge (k6/Locust).
- **Bascule HA** réelle : tuer le primaire Postgres (CNPG) et vérifier la
  promotion ; perdre un nœud OpenSearch / MinIO et vérifier la continuité.
- **PodDisruptionBudgets** lors d'un `kubectl drain` (rolling node upgrade).
- **Restauration** effective : PITR Postgres, restore de snapshot OpenSearch,
  re-mirror MinIO.
- Intégration du **hook code `onix-actions`** (§7) et débit réel de la file
  Celery.
- Dimensionnement fin (requests/limits) calibré sur la charge cliente.

> **Honnêteté.** Ce chantier livre un déploiement **vert by-design** et
> **validé statiquement** (lint + template + parse + gitleaks). La validation de
> **charge** et de **bascule HA** **réelle** requiert un cluster Kubernetes et
> des tests de chaos/charge : c'est la recette d'intégration, hors périmètre de
> la validation hors-cluster.

---

## 10. Fichiers livrés

```
deploy/k8s/onix-ha/
  Chart.yaml                  # métadonnées + dépendances = subcharts Onyx officiels
  Chart.lock                  # versions verrouillées des sous-charts
  values.yaml                 # HA par défaut (replicas>=2, HPA, data-tier, continuité)
  .helmignore
  charts/                     # sous-charts OFFICIELS vendorisés (*.tgz) — auto-porté/hors-ligne
  templates/
    _helpers.tpl              # labels, anti-affinité, HPA, PDB, env secrets, broker URL
    configmap.yaml            # câblage data-tier + flags (non-secret)
    secret.yaml               # Secret de DÉMO (create=true) — prod: existingSecret
    api.yaml                  # Deployment + Service + HPA + PDB
    webserver.yaml            # Deployment + Service + HPA + PDB
    background.yaml           # Deployment + HPA + PDB
    model-servers.yaml        # inférence + indexation (Deploy + Svc + HPA + PDB)
    actions.yaml              # onix-actions (Deploy + Svc + HPA + PDB)
    actions-queue.yaml        # worker Celery (HPA + PDB) + broker RabbitMQ (StatefulSet)
    ollama.yaml               # StatefulSet (PVC) + Service + PDB
    migrations-job.yaml       # Alembic en Job (hook Helm pre-install/upgrade)
    postgres-cluster.yaml     # CRD CloudNativePG Cluster + ScheduledBackup (PITR)
    ingress.yaml              # Ingress + TLS (cert-manager)
    cronjob-opensearch-snapshot.yaml  # snapshot OS à chaud (S3/MinIO)
    cronjob-minio-mirror.yaml         # miroir MinIO à chaud
    NOTES.txt
docs/HA_SCALING.md            # ce document
Makefile                      # bloc « # --- WS4 --- » (lint/template/validation)
```
