# HA_ACCEPTANCE — Recette « le chart se déploie VRAIMENT » (au-delà de `helm lint`)

Ce document consigne ce qui a été **réellement exécuté contre un vrai serveur d'API
Kubernetes** pour le chart `deploy/k8s/onix-ha`, les **preuves** obtenues, et —
en toute honnêteté — ce qui **n'a PAS pu être testé** dans cet environnement et
pourquoi.

Objectif : faire passer l'astérisque HA de « *code prouvé* » (tests applicatifs +
`helm lint`/`helm template`) à « *manifests validés server-side par l'API K8s* » +
« *multi-réplica stateless prouvé à l'exécution* ».

> TL;DR
> * **Validation server-side OK** : les 37 objets du chart par défaut, et **52**
>   objets avec tout le data-tier activé (y compris les CRD CloudNativePG
>   `Cluster`/`ScheduledBackup`), sont **acceptés par un vrai `kube-apiserver`**
>   via `kubectl apply --dry-run=server` (exit 0). Un **contre-exemple négatif**
>   prouve que la validation de schéma CRD est réellement appliquée.
> * **Multi-réplica stateless OK (runtime)** : 2 répliques de `onix-actions`
>   (image du chart) + un Postgres partagé prouvent le **partage d'état
>   inter-pods** : kill-switch posé via la réplique A → **403 servi par la
>   réplique B** (et inversement). L'état vit dans le Postgres partagé.
> * **NON testé ici** : ordonnancement réel des pods par un kubelet vivant
>   (kind), data-tier lourd (model-server ~13 Go), bascule HA des bases,
>   HPA sous charge CPU réelle. Cause documentée plus bas (hôte cgroup v1).

---

## 1. Environnement de recette

| Composant | Version | Rôle dans la recette |
|---|---|---|
| Helm | v3.16.3 | `helm lint` + `helm template` |
| kubectl | v1.36.2 | `apply --dry-run=server` |
| **kube-apiserver** | **v1.33.3** (binaire officiel) | **vrai serveur d'API** (validation server-side) |
| etcd | v3.5.16 (binaire officiel) | backing store du apiserver |
| kind | v0.30.0 | tentative de cluster complet (voir §5 — bloqué) |
| Docker | 29.3.1 | runtime du multi-réplica `onix-actions` + Postgres |
| Postgres | 15-alpine | état partagé (proxy du `Cluster` CNPG en prod) |
| Noyau hôte | 6.18.5, **cgroup v1 (hybride)**, PID 1 = `process_api` | contrainte (voir §5) |

Le serveur d'API est **réel** (mêmes binaires `kube-apiserver`/`etcd` qu'un cluster),
adossé à etcd. Il fournit la **découverte d'API**, la **validation OpenAPI** des
ressources intégrées, le **defaulting**, et — après installation des CRD — la
**validation de schéma des Custom Resources**. C'est précisément ce qu'exerce
`--dry-run=server`, contrairement à `helm lint`/`--dry-run=client` qui ne contactent
jamais l'API.

---

## 2. Validation server-side du chart (PREUVE 1)

### 2.1 Découverte d'API (le serveur connaît bien les types HA du chart)

```
$ kubectl api-resources | grep -E 'deployments|horizontalpodautoscalers|poddisruptionbudgets|cronjobs|statefulsets|ingresses'
deployments                 deploy   apps/v1                       true   Deployment
statefulsets                sts      apps/v1                       true   StatefulSet
horizontalpodautoscalers    hpa      autoscaling/v2                true   HorizontalPodAutoscaler
cronjobs                    cj       batch/v1                      true   CronJob
ingresses                   ing      networking.k8s.io/v1          true   Ingress
poddisruptionbudgets        pdb      policy/v1                     true   PodDisruptionBudget
```

### 2.2 Chart par défaut — 37 objets acceptés (exit 0)

```
$ helm template onix deploy/k8s/onix-ha -n onix --set secrets.create=true --set secrets.values.*=... \
    | kubectl apply --dry-run=server -n onix -f -
poddisruptionbudget.policy/onix-onix-ha-actions created (server dry run)
...
deployment.apps/onix-onix-ha-actions created (server dry run)
horizontalpodautoscaler.autoscaling/onix-onix-ha-actions created (server dry run)
statefulset.apps/onix-onix-ha-actions-broker created (server dry run)
cronjob.batch/onix-onix-ha-minio-mirror created (server dry run)
ingress.networking.k8s.io/onix-onix-ha created (server dry run)
job.batch/onix-onix-ha-migrations created (server dry run)
=== EXIT CODE: 0 ===
```

Décompte des objets rendus et validés (défaut) :

```
1 ConfigMap   2 CronJob   7 Deployment   7 HorizontalPodAutoscaler
1 Ingress     1 Job       8 PodDisruptionBudget   1 Secret
7 Service     2 StatefulSet                       (= 37 objets)
```

### 2.3 Data-tier COMPLET activé — 52 objets, dont les CRD CNPG (exit 0)

Les sous-charts data-tier sont désactivés par défaut (ils exigent des operators/CRD).
Pour les valider quand même, les **CRD CloudNativePG ont été installés dans le vrai
apiserver** (rendus depuis le sous-chart vendorisé `charts/cloudnative-pg-0.26.0.tgz`,
9/10 CRD enregistrées — `poolers` écartée par la limite d'annotation de 256 Ko de
`kubectl apply`, non utilisée par le chart) :

```
$ kubectl get crd | grep cnpg | wc -l
9
$ kubectl api-resources --api-group=postgresql.cnpg.io
clusters          postgresql.cnpg.io/v1   true   Cluster
scheduledbackups  postgresql.cnpg.io/v1   true   ScheduledBackup
... (backups, databases, imagecatalogs, publications, subscriptions, ...)
```

Puis rendu + dry-run server du chart avec `postgresql.cluster.enabled=true`,
`opensearch.enabled=true`, `minio.enabled=true` (**52 objets**) :

```
$ helm template onix deploy/k8s/onix-ha -n onix \
    --set postgresql.cluster.enabled=true --set opensearch.enabled=true --set minio.enabled=true ... \
    | kubectl apply --dry-run=server -n onix -f -
...
statefulset.apps/onix-minio created (server dry run)
statefulset.apps/opensearch-cluster-master created (server dry run)
cluster.postgresql.cnpg.io/onix-onix-ha-postgresql created (server dry run)
scheduledbackup.postgresql.cnpg.io/onix-onix-ha-postgresql-backup created (server dry run)
job.batch/onix-minio-post-job created (server dry run)
=== EXIT: 0 ===
```

→ le **`Cluster` CNPG** et le **`ScheduledBackup`** du chart sont **validés contre
le schéma RÉEL de la CRD** (pas seulement rendus en YAML).

### 2.4 Contre-exemple négatif (la validation de schéma est RÉELLE)

Pour prouver que le dry-run server ne « passe pas tout » :

```
$ kubectl apply --dry-run=server -f bad-cluster.yaml      # spec.instances: "three"
The Cluster "bad-cluster" is invalid:
* spec.instances: Invalid value: "string": spec.instances in body must be of type integer: "string"
```

→ une CR CNPG délibérément invalide est **rejetée** par l'apiserver. La validation
server-side du §2.3 a donc une vraie valeur probante.

### 2.5 Overlay de test reproductible

L'overlay [`deploy/k8s/onix-ha/values-kind-smoke.yaml`](../deploy/k8s/onix-ha/values-kind-smoke.yaml)
fige une configuration **légère, sans CRD** (CNPG/OpenSearch/MinIO/Redis operators
OFF), avec `actions.replicaCount=2` + HPA/PDB ON, et un Secret éphémère (CI only).
Il **ne touche pas** aux défauts HA du chart (`values.yaml` inchangé) :

```
$ helm lint deploy/k8s/onix-ha -f deploy/k8s/onix-ha/values-kind-smoke.yaml
1 chart(s) linted, 0 chart(s) failed
$ helm template onix deploy/k8s/onix-ha -n onix -f deploy/k8s/onix-ha/values-kind-smoke.yaml \
    | kubectl apply --dry-run=server -n onix -f -    # → exit 0
```

HPA de `onix-actions` rendu (plancher 2 répliques) :

```
kind: HorizontalPodAutoscaler
  name: onix-onix-ha-actions
  scaleTargetRef: { kind: Deployment, name: onix-onix-ha-actions }
  minReplicas: 2
  maxReplicas: 4
```

---

## 3. Multi-réplica stateless de `onix-actions` (PREUVE 2)

Le cœur de l'astérisque HA : `onix-actions` est **stateless** car son état
(kill-switch/flags `admin_state`, usage, audit chaîné, tâches) est **déporté** vers
Postgres au lieu du SQLite local (cf. `docs/STATELESS_ACTIONS.md`, `app/db.py`).
Deux répliques doivent voir le **même** état, sinon le scale-out est faux.

### 3.1 Montage (exactement le câblage du chart)

* Image : **`onix-actions:local`** — l'image que le chart déploie
  (`actions.image.repository:tag`), **reconstruite depuis la source** de ce dépôt
  (workaround CA du proxy d'egress pour `pip` ; voir §6). L'image pré-existante
  dans le daemon était **périmée** (sans `app/db.py` ni `psycopg`) — un vrai piège
  détecté et corrigé ici.
* 2 conteneurs `onix-actions-a` / `onix-actions-b` (= 2 pods), même réseau,
  pointant le **même Postgres** avec les variables que la ConfigMap/Secret du chart
  posent : `ONIX_DB_BACKEND=postgres`, `POSTGRES_HOST/USER/PASSWORD/DB`,
  `ONIX_ACTIONS_API_KEY`.

```
$ for c in onix-actions-a onix-actions-b; do docker exec $c python3 -c \
    "import app.db as db; print('backend='+db.backend(),'is_pg='+str(db.is_postgres()))"; done
onix-actions-a: backend=postgres is_pg=True
onix-actions-b: backend=postgres is_pg=True
```

### 3.2 Preuve du partage d'état inter-pods (kill-switch)

```
STEP 1 — état initial des DEUX pods :        pod-A global_enabled=True   pod-B global_enabled=True
STEP 2 — /audit avant kill-switch :          pod-A HTTP 200              pod-B HTTP 200
STEP 3 — POST /admin/control disable_global  sur POD-A UNIQUEMENT → result=applied
STEP 4 — PREUVE sur POD-B (n'a jamais reçu la commande) :
           pod-B /health global_enabled = False
           pod-B /audit -> HTTP 403
           pod-B /audit detail = "Le service onix-actions est temporairement suspendu par un administrateur."
           pod-A /audit -> HTTP 403
STEP 5 — RE-ENABLE via POD-B → pod-A /audit revient à HTTP 200   (bidirectionnel)
STEP 6 — l'état vit bien dans le Postgres PARTAGÉ :
           SELECT key,value FROM admin_state;  ->  global_enabled | true
```

→ Le kill-switch posé sur **une** réplique est **immédiatement effectif sur l'autre**,
parce que l'état transite par le Postgres partagé. C'est exactement le comportement
requis pour exploiter `onix-actions` en `replicas>=2` derrière un Service/HPA.

### 3.3 Objets HA acceptés

`HorizontalPodAutoscaler` (autoscaling/v2) et `PodDisruptionBudget` (policy/v1) du
service `actions` sont **acceptés par l'apiserver** (cf. §2.2/§2.3, exit 0). Le HPA
fixe `minReplicas: 2` (plancher 2 répliques) ; le PDB `minAvailable: 1`.

---

## 4. `helm lint` & gitleaks (régression / hygiène)

```
$ helm lint deploy/k8s/onix-ha
[INFO] Chart.yaml: icon is recommended
1 chart(s) linted, 0 chart(s) failed                 # 0 échec (inchangé)

$ gitleaks detect --no-banner --redact
INF 24 commits scanned.
INF no leaks found                                   # 0 fuite
```

Le seul artefact ajouté au dépôt est l'overlay de test (valeurs FACTICES `devpass`/
`devapikey`, jamais de vrai secret). L'overlay `secrets.create=true` est documenté
comme **CI/démo uniquement** — la prod utilise `secrets.existingSecret` (hors-chart).

---

## 5. Honnêteté : ce qui N'A PAS été testé ici (et pourquoi)

### 5.1 Cluster Kubernetes complet avec kubelet vivant (kind) — **BLOQUÉ (hôte)**

`kind create cluster` **échoue** sur cet hôte. Diagnostic mené jusqu'au bout :

1. `inotify` trop bas → relevé (`max_user_instances`, `max_user_watches`).
2. Hiérarchie cgroup `name=systemd` absente → **montée à la main** sur l'hôte ;
   le node systemd démarre alors.
3. Contrôleur **`cpuset` non monté** → monté ; kubelet passe la validation
   `ContainerManager` (l'erreur « *Cgroup subsystem not mounted: [cpuset]* » disparaît).
4. **Blocage final, irréductible sans droits hôte** : la création des
   *pod sandboxes* échoue dans le shim containerd —
   `runc create failed: unable to start container process: can't get final child's
   PID from pipe: EOF` (cgroup v1 + cgroupns + overlayfs imbriqués). Avec
   `--cgroupns=host`, l'erreur runc/cgroup disparaît mais un **second** blocage
   apparaît (`failed to mount rootfs component: invalid argument` — overlayfs
   imbriqué).

Cause racine : **hôte en cgroup v1 (hybride)**, **PID 1 = `process_api` (pas
systemd)** et **daemon Docker verrouillé en cgroup v1** — impossible de basculer
l'hôte en cgroup v2 (ce qui réglerait le nesting) sans privilèges hôte (reboot /
reconfig du daemon) que la recette n'a pas. kind documente explicitement cgroup v2
comme la configuration supportée.

**Conséquence assumée** : l'**ordonnancement réel des 2 pods `actions` par un kubelet
vivant** (`kubectl get pods` → 2/2 Running/Ready dans un vrai cluster) **n'a pas pu
être montré**. Il est **remplacé** par (a) la validation server-side des manifests
(PREUVE 1) et (b) le multi-réplica stateless à l'exécution via Docker (PREUVE 2),
qui couvrent ensemble « les manifests sont valides pour l'API » + « le service tient
réellement en multi-réplica à état partagé ». L'overlay `values-kind-smoke.yaml` est
prêt pour rejouer le déploiement sur un hôte cgroup v2.

### 5.2 Autres points explicitement non couverts

* **Data-tier lourd réel** (onyx-backend, **model-server ~13 Go**, OpenSearch,
  MinIO distribué, Ollama) : volontairement **non déployé** (disque limité ; hors
  périmètre). Seuls leurs **manifests** sont validés server-side (§2.3).
* **Bascule HA des bases** (failover primaire CNPG, quorum OpenSearch, sentinel
  Redis) : non exercée (nécessite les operators + plusieurs nœuds).
* **HPA sous charge CPU réelle** : l'objet HPA est accepté par l'API, mais aucun
  metrics-server ni montée en charge n'a déclenché un scale 2→N réel.
* **Migrations Alembic / Ingress-NGINX / cert-manager** : manifests validés
  (Job hook, Ingress `networking.k8s.io/v1`), mais non exécutés contre les
  contrôleurs réels.

---

## 6. Annexe — workaround CA (build de l'image)

Le daemon est derrière un proxy d'egress à CA auto-signée
(`/usr/local/share/ca-certificates/egress-gateway-ca-*`, `swp-ca-*`). `pip` échoue
sinon (`CERTIFICATE_VERIFY_FAILED: self-signed certificate in chain`). Build de
`onix-actions:local` en injectant le bundle CA de l'hôte
(`/etc/ssl/certs/ca-certificates.crt`) puis `update-ca-certificates` +
`pip install --cert ...`. Le **Dockerfile de prod n'est pas modifié** (build via un
overlay temporaire hors-dépôt). Validation : l'image reconstruite contient bien
`app/db.py` + `psycopg 3.3.4` et résout `backend=postgres`.

---

## 7. Reproduire

```bash
# 0) Image actions à jour (depuis la source ; workaround CA si proxy egress) :
docker build -t onix-actions:local actions/        # cf. §6 si pip casse sur la CA

# 1) Vrai apiserver + etcd (sans kubelet — suffit pour --dry-run=server) :
#    etcd --listen-client-urls http://127.0.0.1:2379 ...
#    kube-apiserver --etcd-servers=http://127.0.0.1:2379 --authorization-mode=AlwaysAllow ...
#    kubectl config set-cluster/credentials/context -> contexte 'local'

# 2) Validation server-side (défaut puis data-tier complet) :
helm template onix deploy/k8s/onix-ha -n onix -f deploy/k8s/onix-ha/values-kind-smoke.yaml \
  | kubectl apply --dry-run=server -n onix -f -          # exit 0

# 3) Multi-réplica stateless (Docker) :
docker network create onix-ha-net
docker run -d --name onix-pg --network onix-ha-net -e POSTGRES_PASSWORD=devpass \
  -e POSTGRES_USER=postgres -e POSTGRES_DB=onyx postgres:15-alpine
for n in a b; do docker run -d --name onix-actions-$n --network onix-ha-net \
  -e ONIX_DB_BACKEND=postgres -e POSTGRES_HOST=onix-pg -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=devpass -e POSTGRES_DB=onyx -e ONIX_ACTIONS_API_KEY=devapikey \
  -e ONIX_ACTIONS_ADMIN_KEY_OPTIONAL=true -p 1810${n/a/1}${n/b/2}:8100 onix-actions:local; done
# kill-switch sur A -> 403 servi par B (cf. §3.2)
```

> Sur un hôte **cgroup v2**, l'étape (1)-(2) se fait directement avec
> `kind create cluster` + `kind load docker-image onix-actions:local`, et l'étape
> (3) devient `kubectl scale/get pods` sur le Deployment `onix-onix-ha-actions`.
