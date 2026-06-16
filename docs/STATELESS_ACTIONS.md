# onix-actions STATELESS — multi-réplica (HA) sans casser le mono-poste

> **But.** Lever l'astérisque « HA côté code » de `onix-actions` : rendre le
> microservice **stateless** pour qu'il puisse tourner en **plusieurs répliques**
> (scale-out, HPA, bascule) en partageant le même état — **sans rien changer au
> mode mono-poste par défaut**.
>
> **Comment.** Pattern « **défaut conservé + backend opt-in** » : chaque source
> d'état local (SQLite, disque, traitement dans la requête) reçoit un backend
> **partagé** activable par variable d'environnement. Par défaut, le comportement
> historique (SQLite local, disque local, audit synchrone) est **strictement
> inchangé**.

Ce document décrit ce qui est livré côté **code** (le hook attendu par
[`docs/HA_SCALING.md` §7](HA_SCALING.md)), comment l'activer, et ce qui reste à
la **recette cluster réel**.

---

## 1. Les trois SPOF d'état levés

| SPOF (mono-poste) | Avant | Après (opt-in) | Effet HA |
|---|---|---|---|
| **Persistance** (kill-switch, flags, usage, audit, tâches) | SQLite local (`admin_state.py`, `tasks.py`, `usage_tracker.py`, `audit_log.py`) | **Postgres partagé** (`ONIX_DB_BACKEND=postgres`) | Toutes les répliques voient le **même** état |
| **Fichiers `.docx`** générés (`docgen.py`) | disque local `ONIX_JOBS_DIR` | **S3/MinIO** (`ONIX_OBJECT_STORE=s3`) | `GET /download/{id}` marche depuis **n'importe quelle** réplique |
| **Traitements longs** (OCR gros PDF / lot) | dans la requête HTTP (bloquant) | **file Celery** (`ONIX_QUEUE_ENABLED=true`) | L'API reste réactive ; pool de workers **scale** (HPA 2→12) |

> **Principe directeur.** Aucune de ces évolutions n'est activée par défaut : sans
> variable d'environnement, `onix-actions` reste un service mono-instance à
> persistance SQLite locale — **comportement identique** à avant ce chantier.

---

## 2. Persistance partagée (Postgres opt-in)

### 2.1 Module d'accès factorisé — `actions/app/db.py`
Toute la persistance passe désormais par une **couche d'accès unique** :

```python
from app import db
with db.connect() as conn:           # sqlite3.Connection OU adaptateur Postgres
    conn.execute("SELECT ... WHERE key=?", (k,))   # dialecte SQLite, traduit si PG
    conn.commit()
```

* `db.backend()` → `"sqlite"` (défaut) ou `"postgres"` selon `ONIX_DB_BACKEND` ;
* `db.connect()` renvoie une connexion **utilisable en context manager**, avec la
  **même interface** que `sqlite3.Connection` (`execute` / `fetchone` / `fetchall`
  / `rowcount` / `commit` / `rollback`), quel que soit le backend ;
* les requêtes restent écrites **en dialecte SQLite** ; un **adaptateur** les
  traduit à la volée vers Postgres :
  * placeholders `?` → `%s`, `:nom` → `%(nom)s` ;
  * `INSERT OR REPLACE INTO t(...)` → `INSERT ... ON CONFLICT (<pk>) DO UPDATE` ;
  * `PRAGMA table_info(t)` / `sqlite_master` → `information_schema` (nom de table
    **passé en paramètre lié**, pas interpolé) ;
  * littéraux `%` (ex. `LIKE 'blocked_user:%'`) **doublés** pour psycopg.

**Surface de changement minimale.** `admin_state` **réexporte** `_connect`/`_lock`
depuis `db`. Les modules historiques (`tasks.py`, `usage_tracker.py`,
`audit_log.py`, `retention.py`) qui font `from .admin_state import _connect,
_lock` continuent de fonctionner **sans modification**.

### 2.2 Activation
```bash
# Mode Postgres : DSN complet…
export ONIX_DB_BACKEND=postgres
export ONIX_DB_URL=postgresql://user:pwd@postgres-rw:5432/onyx
# …OU dérivé des variables du chart (si ONIX_DB_URL est vide) :
#   POSTGRES_HOST / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB / POSTGRES_PORT
# Schéma dédié optionnel :
export ONIX_DB_SCHEMA=onix_actions     # -> search_path
```
Sans `ONIX_DB_BACKEND` (ou `=sqlite`) : **SQLite local, inchangé**.

### 2.3 Chaîne d'audit HMAC vérifiable en Postgres
Le journal `admin_audit` est **append-only chaîné** (chaque ligne porte
`prev_hash` + `entry_hash = HMAC(clé, prev_hash || contenu)`). En Postgres :

* `append_audit` lit le dernier maillon **et** insère le suivant **dans la même
  transaction** ;
* un **verrou d'avis transactionnel** (`pg_advisory_xact_lock`) **sérialise** ce
  cycle **entre répliques** : deux répliques ne peuvent pas écrire deux `seq=N+1`
  concurrents qui casseraient la chaîne. Le verrou est relâché au COMMIT.

> **Clé HMAC stable.** La vérification recalcule avec la clé courante
> (`ONIX_ACTIONS_AUDIT_HMAC_KEY`). Si la clé **change** entre deux écritures, les
> anciennes lignes ne se revérifient plus (c'est attendu : changer la clé d'un
> journal scellé invalide les sceaux antérieurs). En production, **fixer la clé**
> (Secret K8s) une fois pour toutes.

---

## 3. Stockage objet partagé (S3/MinIO opt-in)

### 3.1 Module — `actions/app/objstore.py`
* `ONIX_OBJECT_STORE=s3` → `docgen.generate_fiche` téléverse le `.docx` sur
  S3/MinIO sous la clé `jobs/<job_id>/<filename>` (bucket `ONIX_S3_BUCKET`,
  défaut `onyx-file-store-bucket`) ;
* `GET /download/{job_id}` relit l'objet depuis S3 (`docgen.read_download`) →
  **n'importe quelle réplique** sert le fichier, même si elle ne l'a pas généré ;
* sans `ONIX_OBJECT_STORE` (ou `=local`) : **disque local, inchangé**.

Variables (alignées `docker-compose` / chart) : `S3_ENDPOINT_URL`,
`S3_AWS_ACCESS_KEY_ID`, `S3_AWS_SECRET_ACCESS_KEY`, `ONIX_S3_BUCKET`,
`S3_REGION`. Client **boto3** (compatible MinIO, path-style), importé
**paresseusement** (le mode local n'a aucune dépendance nouvelle).

> **Rétention RGPD en mode S3.** La purge/effacement (`retention.py`) opère sur la
> copie **locale** (toujours écrite) et la base ; pour les objets S3, coupler au
> miroir/cycle de vie du bucket (cf. `backups.minioMirror` du chart). La copie
> locale d'une réplique éphémère est purgée avec le pod.

---

## 4. File asynchrone (Celery opt-in)

### 4.1 Module — `actions/app/celery_app.py`
L'objet applicatif s'appelle **`celery`** et est importable comme
`app.celery_app.celery` → **exactement** la commande du chart WS4 :

```
celery -A app.celery_app.celery worker --loglevel=info --concurrency=4
```

* tâche **`audit_file_async`** : réutilise la logique synchrone
  (`ocr.extract` → `extract_canonical_fields` → `audit_engine.audit`) ;
* broker via `ONIX_BROKER_URL` (AMQP RabbitMQ en prod ; Redis en dev) ;
* backend de résultats via `ONIX_RESULT_BACKEND` (ex. `db+postgresql://…` ou
  Redis) — à défaut, dérivé du broker Redis, sinon `rpc://`.

### 4.2 Endpoints (gated `ONIX_QUEUE_ENABLED=true`)
| Endpoint | Rôle |
|---|---|
| `POST /audit/file/async` | valide l'upload, **enfile** la tâche, renvoie **`202 Accepted`** + `task_id` (l'API ne bloque pas) |
| `GET /jobs/{task_id}` | statut Celery (`PENDING`/`STARTED`/`SUCCESS`/`FAILURE`) + `result` (verdict) en SUCCESS |

Sans `ONIX_QUEUE_ENABLED` : ces routes renvoient `503` ; l'audit **synchrone**
(`POST /audit/file`) reste disponible et inchangé.

**Mode EAGER** (`ONIX_QUEUE_EAGER=true`) : exécution synchrone en process (tests /
CI sans worker ni broker) ; les résultats sont stockés (`task_store_eager_result`)
pour rester lisibles via `GET /jobs/{id}`.

---

## 5. Variables d'environnement (récapitulatif)

| Variable | Défaut | Effet |
|---|---|---|
| `ONIX_DB_BACKEND` | `sqlite` | `postgres` ⇒ persistance partagée |
| `ONIX_DB_URL` | — | DSN Postgres (sinon dérivé de `POSTGRES_*`) |
| `ONIX_DB_SCHEMA` | `public` | `search_path` dédié (optionnel) |
| `ONIX_OBJECT_STORE` | `local` | `s3` ⇒ `.docx` sur S3/MinIO |
| `ONIX_S3_BUCKET` | `onyx-file-store-bucket` | bucket des fichiers |
| `S3_ENDPOINT_URL` / `S3_AWS_ACCESS_KEY_ID` / `S3_AWS_SECRET_ACCESS_KEY` | — | endpoint + creds S3 |
| `ONIX_QUEUE_ENABLED` | `false` | `true` ⇒ endpoints async |
| `ONIX_BROKER_URL` | — | broker Celery (AMQP/Redis) |
| `ONIX_RESULT_BACKEND` | dérivé | backend de résultats Celery |
| `ONIX_QUEUE_EAGER` | `false` | exécution synchrone (tests) |
| `ONIX_ACTIONS_AUDIT_HMAC_KEY` | — | clé de chaînage du journal d'audit (à fixer en prod) |

Le chart [`deploy/k8s/onix-ha`](../deploy/k8s/onix-ha/) câble déjà
`ONIX_DB_BACKEND=postgres`, `ONIX_QUEUE_ENABLED`, le broker et les `POSTGRES_*` /
`S3_*` ; `values.yaml` documente les clés alignées (`actions.config.dbBackend`,
`dbUrl`, `objectStore`, `s3Bucket`, `actionsQueue.resultBackend`).

---

## 6. Validation

### 6.1 Mode par défaut (mono-poste) — **inchangé**
```bash
pytest actions/tests -q        # 71 passed, 4 skipped (PG/S3 skip hors env dédié)
```
La suite historique reste **verte**. Les nouveaux tests Postgres/S3
(`test_stateless_backends.py`) **skippent** proprement sans conteneur.

### 6.2 Mode Postgres + S3 (conteneurs réels)
```bash
docker run -d --name pg   -e POSTGRES_PASSWORD=… -e POSTGRES_USER=… \
                          -e POSTGRES_DB=onix_actions -p 55432:5432 postgres:15-alpine
docker run -d --name mio  -e MINIO_ROOT_USER=… -e MINIO_ROOT_PASSWORD=… \
                          -p 59000:9000 minio/minio server /data

ONIX_TEST_PG_URL=postgresql://…@127.0.0.1:55432/onix_actions \
ONIX_TEST_S3=1 S3_ENDPOINT_URL=http://127.0.0.1:59000 \
S3_AWS_ACCESS_KEY_ID=… S3_AWS_SECRET_ACCESS_KEY=… \
  pytest actions/tests/test_stateless_backends.py -v   # 13 passed
```
Scénario **multi-réplica** prouvé (deux processus `uvicorn`, même PG + MinIO) :

* kill-switch posé sur la réplique A ⇒ la réplique B renvoie `403` (état partagé) ;
* tâche/usage créés sur B ⇒ lus par A (Postgres partagé) ;
* `.docx` généré par A (→ S3) ⇒ **téléchargé par B** alors que son répertoire
  local n'existe même pas (stockage objet partagé) ;
* chaîne d'audit HMAC **vérifiée** en Postgres (`ok=true`), et **altération
  détectée** (`broken_at`) après un `UPDATE` direct en base.

### 6.3 Mode Celery (worker réel)
```bash
celery -A app.celery_app.celery worker --loglevel=info --concurrency=2   # worker
# POST /audit/file/async -> 202 + task_id ; GET /jobs/{id} -> SUCCESS + verdict
```
Job réel dispatché (broker Redis), traité par le worker, résultat
`verdict=CONFORME` relu via `GET /jobs/{id}`.

### 6.4 Sécurité & qualité
* `bandit -r actions/app` → **0 High / 0 Medium** ;
* `pip-audit --strict -r actions/requirements.txt` → **0 CVE** (urllib3 épinglé
  2.7.0 pour purger PYSEC-2026-141/142 tirées par botocore) ;
* `gitleaks` (config dépôt) → **0 secret**.

---

## 7. Dans quelle mesure l'astérisque HA « code » est levé

**Levé (côté code, validé hors-cluster) :**
- état applicatif **déporté** vers Postgres partagé → cohérence inter-répliques
  (kill-switch, flags, usage, audit chaîné, tâches) ;
- fichiers générés **déportés** vers S3/MinIO → `GET /download` multi-réplica ;
- traitements longs **mis en file** Celery → API non bloquante + scale-out workers.
- **Aucune régression** du mode mono-poste (défaut SQLite/local/synchrone).

**Reste à la recette cluster réel (hors périmètre code) :**
- comportement **HPA** sous charge (scale up/down) — `metrics-server` + test de charge ;
- **bascule HA** Postgres (CNPG : tuer le primaire), perte d'un nœud MinIO/Redis ;
- débit réel de la file Celery sous pic, retries/idempotence à l'échelle ;
- **PodDisruptionBudgets** lors d'un `kubectl drain` ;
- rétention des objets S3 (cycle de vie bucket) en complément de la purge locale.

> **Honnêteté.** Ce chantier livre le **hook code** rendant `onix-actions`
> stateless et **prouve** le partage d'état sur conteneurs réels (Postgres +
> MinIO + Celery). La validation de **charge** et de **bascule HA réelle**
> requiert un cluster Kubernetes — c'est la recette d'intégration WS4.
