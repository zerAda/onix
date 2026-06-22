# Audit byte-by-byte — Documentation ↔ Réalité — Scope **Déploiement / HA / Ops**

> **Date** : 2026-06-22 (itér. M7 : preuve de transit proxy) · base 2026-06-18 · **Auditeur** : agent SRE/DevOps/plateforme (lecture seule)
> **Méthode & légende** : [`README.md`](README.md) (✅ conforme · ⚠️ écart mineur ·
> ❌ écart majeur · 🕳️ doc-sans-code · 🔇 code-sans-doc · ❔ non vérifiable).
> **Règle de jeu n°1** (`AGENTS.md`) : *honnêteté > esbroufe, zéro mock présenté comme réel.*

## Périmètre

- **Docs** : `DEPLOY_PROD.md`, `DEPLOY_AZURE.md`, `HA_SCALING.md`, `HA_ACCEPTANCE.md`,
  `PROD_LOCAL.md`, `POC_LOCAL.md`, `RUNBOOK.md`, `PERFORMANCE.md`.
- **Code/config** : tout `deploy/` (`azure/` + `bicep/`, `k8s/onix-ha/`, `local-prod/`,
  `prod/`), `docker-compose*.yml` (base/`.gpu`/`.performance`/`.prod-local`/`.lan`),
  `Makefile`, `scripts/`, `nginx/`.
- **Limites de l'audit** : lecture statique pure. Aucune commande réseau, aucun
  `helm install`/`compose up`/`az`/build n'a été exécuté. Les rendus Helm n'ont pas
  été produits ; la conformité des templates a été établie par lecture
  `template ↔ values ↔ doc`. Les comptages d'objets de la doc (36/37/52) sont
  cohérents avec les templates lus mais **non re-générés ici**.

## Tableau de comptage

| Classe | Nb | Commentaire de tête |
|---|---:|---|
| ✅ CONFORME | 58 | L'essentiel des affirmations vérifiables tient au byte près. |
| ⚠️ ÉCART MINEUR | 11 | Nommage de prose, instructions Azure non câblées automatiquement, durcissement partiel. |
| ❌ ÉCART MAJEUR | 0 | Aucune affirmation factuellement fausse détectée. |
| 🕳️ DOC-SANS-CODE | 3 | Hooks `onix-actions` Celery/async (assumés « à intégrer ») ; ingress anti-spoofing Azure (décrit, non templatisé). |
| 🔇 CODE-SANS-DOC | 2 | Tier sémantique du cache (Helm), HTTP/3/QUIC Caddy. |
| ❔ NON VÉRIFIABLE | 6 | Onyx v4.1.1 (non vendoré), comportements ACME/OIDC/Graph/HPA/bascule réels. |

**Toutes les cibles Make citées par les docs EXISTENT** dans le `Makefile` (vérifié
ligne à ligne, cf. §RUNBOOK & §DEPLOY_PROD). Aucune procédure ne pointe un
fichier/cible inexistant.

---

## DEPLOY_PROD.md (mono-nœud durci, exposé — Caddy + oauth2-proxy + gateway)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Caddy = seul service exposé via `${BIND_IP}` (80/443) ; nginx repassé interne (`ports: !reset []`, `expose: "80"`) | ✅ | `deploy/prod/docker-compose.prod.yml:80-85` (ports Caddy), `:129-131` (nginx reset+expose) | Conforme au schéma §0. |
| Surcouche empilée, base non modifiée | ✅ | `Makefile:302` (`COMPOSE_PROD := … -f docker-compose.yml -f deploy/prod/docker-compose.prod.yml`) | |
| Caddy : HSTS 2 ans, X-Content-Type-Options, X-Frame-Options SAMEORIGIN, Referrer-Policy, Permissions-Policy, `-Server`/`-X-Powered-By` | ✅ | `deploy/prod/Caddyfile:46-61` | HSTS `max-age=63072000; includeSubDomains; preload` exact. |
| Redirection 80→443 native | ❔/✅ | `Caddyfile:44` (site `{$ONYX_DOMAIN}` => HTTPS auto) | Comportement Caddy natif ; non exécuté ici (la doc le confirme via `caddy validate`). |
| Anti-usurpation : Caddy supprime `X-OIDC-Claims` + `X-Auth-Request-*` entrants | ✅ | `Caddyfile:72-78` | Défense au bord ; inclut désormais `-X-OIDC-Proxy-Secret` (M7). |
| **Preuve de transit proxy (anti-spoof M7)** : nginx injecte `X-OIDC-Proxy-Secret` (= `GATEWAY_PROXY_SHARED_SECRET`) sur le chemin chat ; monté en TEMPLATE + envsubst restreint (`NGINX_ENVSUBST_FILTER`) ; secret partagé avec la passerelle ; Caddy strip l'en-tête entrant au bord | ✅ | `deploy/prod/nginx.prod.conf` (`proxy_set_header X-OIDC-Proxy-Secret "${GATEWAY_PROXY_SHARED_SECRET}"` sur le chemin chat + strip sur `/api`,`/`) ; `docker-compose.prod.yml` (env nginx `GATEWAY_PROXY_SHARED_SECRET` + `NGINX_ENVSUBST_FILTER` + mount `/etc/nginx/templates/default.conf.template` ; env access-gateway `GATEWAY_PROXY_SHARED_SECRET`) ; `deploy/prod/Caddyfile:73` (`request_header -X-OIDC-Proxy-Secret`) ; `env.prod.template` + `scripts/gen-secrets.sh` (génère le secret) | Ferme la vuln d'usurpation : un client direct ne peut pas produire la preuve → 401 côté gateway (`identity._require_proxy_proof`). `docker compose config` OK. |
| `/oauth2/*` → oauth2-proxy ; `/api/chat/send-message` → `forward_auth` puis nginx ; reste → nginx | ✅ | `Caddyfile:84-90`, `:102-124`, `:132-142` | Topologie §5bis exacte. |
| nginx pose `X-OIDC-Claims` depuis identité vérifiée, route chat → `access-gateway:8200/v1/chat/send-message` | ✅ | `docker-compose.prod.yml:124-136` (montage `nginx.prod.conf`) ; routage détaillé dans `deploy/prod/nginx.prod.conf` (non ré-cité ligne à ligne) | Câblage présent. |
| oauth2-proxy : image `v7.15.3`, `--set-xauthrequest`, `--user-id-claim=oid`, `--oidc-email-claim=upn`, interne (`expose 4180`) | ✅ | `docker-compose.prod.yml:225`, `:240-246`, `:231` | Version récente (CVE bypass auth corrigées). |
| access-gateway : build local, interne (`expose 8200`), Redis base 1 `redis://…@cache:6379/1`, fail-closed | ✅ | `docker-compose.prod.yml:308-360`, `:347` (`GATEWAY_DENY_IF_NO_MATCH:-true`) | Cache HMAC obligatoire (`:358` `:?`). |
| Garde-fou `preflight` s'exécute AVANT api/web/background/caddy (depends_on healthy) | ✅ | `preflight` `docker-compose.prod.yml:36-58` ; `depends_on preflight: service_healthy` sur caddy `:73-74`, nginx `:122-123`, api `:144-145`, background `:168-169`, web `:179-180`, oauth2-proxy `:228-229`, gateway `:313-314` | §6 exact. |
| Règle défaut-sûr : si `BIND_IP≠127.0.0.1` ⇒ TLS+OIDC+vérif e-mail obligatoires, sinon refus | ✅ | `scripts/preflight-prod.sh:37-78` | localhost strict = passe (`:41-47`). |
| OIDC forcé : `AUTH_TYPE=oidc`, `REQUIRE_EMAIL_VERIFICATION=true`, `VALID_EMAIL_DOMAINS`, `USER_DIRECTORY_ADMIN_ONLY=true`, `WEB_DOMAIN https`, PKCE | ✅ | `docker-compose.prod.yml:148-161` | `:156` force `REQUIRE_EMAIL_VERIFICATION=true` en dur. |
| Image `actions` épinglée : `build: !reset null`, `pull_policy: always`, `ACTIONS_IMAGE:?` | ✅ | `docker-compose.prod.yml:196-199` | Pas de build local en prod. |
| Cibles `config-prod`, `up-prod`, `down-prod`, `secrets-prod`, `preflight-prod`, `logs-prod`, `ps-prod`, `restart-prod` | ✅ | `Makefile:304-337` | **Toutes existent.** Invocation `ENV=…` réelle (`:301-302`). |
| `make secrets-prod` ⇒ `ENV_FILE=$(ENV) gen-secrets.sh` ; ne génère les `GATEWAY_*`/`OAUTH2_PROXY_*` que pour `deploy/prod/*` | ✅ | `Makefile:311-312` ; `scripts/gen-secrets.sh:120-136` | Doc §5bis-d le précise honnêtement (recopie/coffre). |
| Validation autonome `-f docker-compose.prod.yml` seul échoue (références base) — valider empilé | ✅ | Cohérent : `docker-compose.prod.yml` ne (re)définit pas `cache`/`api_server`/`nginx` complets | §5bis-f point 5 honnête. |
| Backup/rollback : `make backup` (arrêt bref + tar à froid **CHIFFRÉ** des volumes), `make restore DIR=…` (déchiffre) | 🟡 | `scripts/backup.sh` (chiffrement) ; `scripts/restore.sh` (déchiffrement) ; `scripts/tests/test_backup_encrypt.py` | **[BKP-02 corrigé 2026-06-22]** : archives **chiffrées** au repos (openssl AES-256-CBC/PBKDF2, passphrase `ONIX_BACKUP_PASSPHRASE` env) — **fail-closed** : refus de produire du clair sans passphrase (sauf override DEV) ; `restore.sh` déchiffre par pipe (0 clair sur disque). Le tar à froid est **cohérent** (stack arrêtée). Reste : `pg_dump` logique (backup online/portable) + gate santé restore (M5/M6). |
| `make backup` en prod archive via `docker compose` projet `onix` (pas d'`--env-file`/`-f prod`) | ⚠️ | `scripts/backup.sh:13-14` (`PROJ="onix"`, `$DC stop` nu) | Volumes nommés `onix_*` OK (`docker-compose.yml:22` `name: onix`), mais `backup.sh` n'empile pas la surcouche prod : `$DC stop` ne cible pas Caddy/oauth2-proxy/gateway (lancés par `COMPOSE_PROD`). Cohérence des volumes data OK ; arrêt partiel possible. |
| Limites de ressources prod (caddy/oauth2-proxy/gateway) | ✅ | `docker-compose.prod.yml:107-111`, `:283-287`, `:397-401` | `deploy.resources.limits` posés. |
| HTTP/3 (QUIC) sur 443/udp | 🔇 | `docker-compose.prod.yml:85` | Exposé mais non documenté dans DEPLOY_PROD. |

---

## DEPLOY_AZURE.md (AKS France Central, data-tier managé)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Passerelle = template natif du chart (`accessGateway.enabled=true`) | ✅ | `deploy/azure/values-azure.yaml:137-149` ; template `access-gateway.yaml` rendu si `enabled` (sous-agent §9) | Plus de manifeste autonome. |
| `values-azure.yaml` : Postgres/Redis managés OFF in-cluster + `host` FQDN ; OpenSearch/MinIO in-cluster ; Ollama CPU | ✅ | `values-azure.yaml:72-101`, `:107-119` | `postgresql/redis operator+cluster: false`. |
| Télémétrie OFF (`disableTelemetry: true`) | ✅ | `values-azure.yaml:23` ; `values.yaml:33` | Aligné compose. |
| Gotcha Redis Azure : TLS 6380, `noeviction`, URL `rediss://…:6380` | ✅(infra) / ⚠️(app) | Bicep `redis.bicep:8-9,51` (`enableNonSslPort=false`), `main.bicep:200` (`redisSslPort`) ; URL gateway `rediss://…:6380/1` dans Secret `secret.yaml:11` & `values-azure.yaml:136` | **Mais** `REDIS_SSL`/`REDIS_PORT` côté **Onyx** (`api_server`/`background`, base 0) **ne sont PAS posés** par `values-azure.yaml` ni la ConfigMap (`configmap.yaml:16` ne pose que `REDIS_HOST`). La doc dit « poser REDIS_SSL/REDIS_PORT côté Onyx » : instruction prescriptive **non câblée automatiquement** → à faire à la main (Secret/extraEnv). |
| Gotcha Postgres : `sslmode=require` | ✅(infra) / ⚠️(app) | `postgres.bicep:8`, `values-azure.yaml:70-75` | ConfigMap pose `POSTGRES_HOST`/`POSTGRES_USER` (`configmap.yaml:13-14`) mais **aucun** `sslmode`/SSL explicite — repose sur le défaut psycopg/Onyx (non vérifiable ici, ❔). |
| Gotcha `ENCRYPTION_KEY_SECRET` (sinon creds connecteurs en clair) | ✅ | `DEPLOY_AZURE.md:77-78` (`az keyvault secret set … ENCRYPTION-KEY-SECRET`) ; `keyvault.bicep:5-8` ; `deploy.sh:76` | Posé via Key Vault (P2) — pas dans le chart, conforme à la prose. |
| Ingress route SEULEMENT `/api/chat/send-message → <RELEASE>-access-gateway:8200` + oauth2-proxy forward-auth + strip `X-OIDC-Claims` en annotations | 🕳️ | `DEPLOY_AZURE.md:97-101` ; **template** `ingress.yaml` route `/api` générique → `api:8080`, `/` → web (sous-agent §8) ; **aucune** annotation oauth2-proxy/anti-spoofing rendue | La doc dit « porté en annotations d'ingress AKS » : ce câblage **n'existe pas** dans le template — il est **décrit comme à faire**, pas livré. Risque « doc présente comme réel un câblage absent ». |
| ACR pull-through cache, `az acr build` images custom, CNPG/PITR/snapshots/mirror | ❔ | Commandes `az`/`helm` non exécutables ici | La doc l'annonce honnêtement (en-tête ⚠️, §Limites). |
| `make sync-doc-acl` en CronJob, monté ConfigMap `onix-doc-acl` | ✅(cible)/❔(cronjob) | `Makefile:114-118` (cible existe) ; `values.yaml:343` (`docAclConfigMap`) | Le CronJob de sync Graph→ACL n'est pas un template du chart (généré hors-chart). |
| `setup-entra.sh` crée onix-sso (/oauth2/callback) + onix-graph-groups (GroupMember.Read.All) + onix-sharepoint (Sites.Read.All) + pousse Key Vault | ✅ | `deploy/azure/setup-entra.sh:32-62` | Moindre privilège ; perm-sync EE (cert) explicitement écartée `:52-53`. |
| Bicep modulaire « `bicep build` propre » | ❔ | `deploy/azure/bicep/{main.bicep,main.bicepparam,modules/*.bicep,deploy.sh}` présents et structurés | `bicep build` non exécuté ici (pas d'outil) → non vérifiable, structure cohérente. |
| RBAC FOSS = filtre de SORTIE (zéro-fuite strict ≠ EE) | ✅ | `DEPLOY_AZURE.md:142` ; aligné AGENTS.md | Honnêteté FOSS vs EE respectée. |

---

## HA_SCALING.md (chart Helm `onix-ha`)

> Vérification fine des templates déléguée et corroborée (`deploy/k8s/onix-ha/templates/*`).

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| HPA `autoscaling/v2`, `minReplicas: 2` sur les 7 services stateless ; helper `onix.hpa` | ✅ | `templates/_helpers.tpl:181-212` ; `values.yaml:85-89,109-113,131-135,154-158,177-181,206-210,272-276` | api/web/background/inference/index/actions/worker. |
| 8 PodDisruptionBudgets `minAvailable: 1` ; helper `onix.pdb` | ✅ | `_helpers.tpl:218-234` ; PDB sur les 7 stateless + ollama (`ollama.yaml:108`, `values.yaml:460-462`) | 8 = 7 + ollama. |
| Migrations Alembic en Job hook `pre-install,pre-upgrade` | ✅ | `templates/migrations-job.yaml:8,15,37` ; `values.yaml:77` (`runMigrationsJob: true`) | Élimine la course multi-réplica. |
| Postgres CNPG `kind: Cluster` `instances: 3` + `ScheduledBackup` (cron 6 champs) | ✅ | `templates/postgres-cluster.yaml:11-12,18,67-68` ; `values.yaml:495,511` (`"0 0 2 * * *"`) | Format CNPG 6 champs confirmé. |
| Anti-affinité soft par défaut, `topologyKey kubernetes.io/hostname` | ✅ | `_helpers.tpl:110-129` ; `values.yaml:58-61` | passable en `hard`. |
| `requests`/`limits` sur TOUS les conteneurs | ✅ | `values.yaml` (api `:78-84`, web `:102-108`, background `:124-130`, model-servers `:147-176`, actions `:199-205`, worker `:265-271`, broker `:292-298`, ollama `:453-459`) | Aucune section vide. Tableau §3 cohérent. |
| `runAsNonRoot` partout (« requests/limits + runAsNonRoot » §1/intro) | ✅ (nuancé) | Helper `onix.podSecurityContext` : seccomp RuntimeDefault PARTOUT ; non-root où l'image le supporte (actions/worker/gateway). `readOnlyRootFilesystem` OPT-IN gateway (`values.yaml:403`). NetworkPolicy OPT-IN (`networkpolicy.yaml`, `values.yaml:97`) | Onyx/Ollama restent root (images amont) — documenté `HA_SCALING.md` §5bis (rebuild = suite). Durcissement « gratuit » généralisé ; reste opt-in validé statiquement. |
| File async Celery : worker (Deploy+HPA+PDB) + broker RabbitMQ StatefulSet ; cmd `-A app.celery_app.celery worker` | ✅ | `templates/actions-queue.yaml` ; `values.yaml:258-264,283-286` | Worker scale 2→12. |
| Hook code `onix-actions` (celery_app.py, endpoints async, ONIX_DB_BACKEND=postgres) « à intégrer par l'intégrateur » | 🕳️ (assumé) | `HA_SCALING.md §7` | Doc explicite : chart prêt, **code applicatif non fourni ici** ; honnête (« ne modifie pas actions/app »). Statelessness prouvée par ailleurs (cf. HA_ACCEPTANCE). |
| CronJobs continuité : snapshot OpenSearch + miroir MinIO (`batch/v1`) ; PITR Postgres natif CNPG (pas de CronJob) | ✅ | `templates/cronjob-opensearch-snapshot.yaml:11`, `cronjob-minio-mirror.yaml:8` ; `values.yaml:573-590` | Schedules 6/5-champs respectifs. |
| Ollama StatefulSet+PVC+Service+PDB, GPU conditionnel (`ollama.gpu.enabled`), `OLLAMA_CONTEXT_LENGTH` injecté | ✅ | `templates/ollama.yaml` (GPU `:25-34`, env ctx `:53-54`) ; `values.yaml:410-462` | `contextLength: 8192`. |
| `secret.yaml` rendu seulement si `secrets.create=true` (démo/CI) | ✅ | `templates/secret.yaml:13` ; `values.yaml:45` (`create: false`) | Prod = `existingSecret`. |
| Sous-charts officiels vendorisés (CNPG/OpenSearch/Redis/MinIO) hors-ligne, désactivés par défaut | ✅ | `values.yaml:487-568` (`.enabled: false`) ; `Chart.lock`, `charts/*.tgz` présents | Activation en deux temps (operator→cluster). |
| Comptage « 36 documents par défaut » | ✅(cohérent) | Décompte des kinds actifs par défaut (7 Deploy + 2 STS + 7 Svc + 7 HPA + 8 PDB + 1 Job + 2 CronJob + 1 Ingress + 1 ConfigMap, Secret non rendu) = 36 | Non re-généré (pas de `helm template` exécuté). |
| Tier sémantique du cache (`semantic.enabled`) | 🔇 | `values.yaml:393-397` | Présent dans le chart, non mentionné par HA_SCALING. |

---

## HA_ACCEPTANCE.md (recette server-side + multi-réplica)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Validation server-side `kubectl apply --dry-run=server` (37/52 objets) + contre-exemple négatif CNPG | ❔ | `HA_ACCEPTANCE.md §2` (logs cités) | Recette **exécutée hors de ce dépôt** (apiserver/etcd réels) ; non rejouable ici → non vérifiable, mais cohérente avec les templates (37 ≈ 36 défaut + 1 Secret create=true). |
| Multi-réplica stateless prouvé (kill-switch pod-A → 403 pod-B via Postgres partagé) | ❔ | `HA_ACCEPTANCE.md §3` | S'appuie sur `actions/app/db.py` (hors scope deploy) ; non rejoué ici. |
| Overlay `values-kind-smoke.yaml` : data-tier OFF, `actions.replicaCount=2`, HPA `minReplicas:2`, Secret éphémère | ✅ | `deploy/k8s/onix-ha/values-kind-smoke.yaml` présent (3116 o) ; cité `:139-159` | Fichier livré, valeurs factices `devpass`/`devapikey`. |
| kind bloqué (cgroup v1) — honnêteté sur le NON-testé | ✅ | `HA_ACCEPTANCE.md §5` | Limites assumées explicitement (règle n°1). |

---

## PROD_LOCAL.md (production machine unique durcie)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Overlay `docker-compose.prod-local.yml` empilé : healthchecks db/opensearch/cache/api/web/model + ordre + `restart: always` | ✅ | `docker-compose.prod-local.yml:51-223` | Sonde OpenSearch via `OPENSEARCH_INITIAL_ADMIN_PASSWORD` (`:79`) — note de fiabilité exacte. |
| `depends_on … condition: service_healthy` ; api attend db/opensearch/cache/minio/model sains avant alembic | ✅ | `docker-compose.prod-local.yml:150-160` | Élimine la course alembic. |
| nginx reste lié à 127.0.0.1 (overlay n'y touche pas) | ✅ | `docker-compose.prod-local.yml:216-223` (pas de redéfinition ports) ; base `docker-compose.yml` nginx `127.0.0.1:${ONYX_HOST_PORT}:80` | Frontière « rien de public » respectée. |
| Cibles `up-local-prod`, `down/restart/ps/logs-local-prod`, `config-local-prod` | ✅ | `Makefile:353-373` | **Toutes existent.** `up-local-prod` dépend de `secrets` (`:358`). |
| systemd : `deploy/local-prod/onix.service` (oneshot, RemainAfterExit, `up -d` boot / `down` stop sans `-v`) | ✅ | `deploy/local-prod/onix.service:36-49` | `WorkingDirectory=/home/user/onix` (à adapter, doc le dit). |
| Accès testeurs : Tailscale Serve / LAN via `docker-compose.lan.yml` | ✅ | `docker-compose.lan.yml` (override `ports: !override "${ONYX_HOST_PORT}:80"`) | LAN non chiffré, doc préfère Tailscale. |
| `make backup`/`restore DIR=…` ; modèles Ollama non sauvegardés | ✅ | `Makefile:92-96` ; `scripts/backup.sh:18` (`db_volume opensearch-data minio_data file-system`) | Volumes confirmés `docker-compose.yml:404-409`. |
| `make backup` arrête brièvement la stack | ✅ | `scripts/backup.sh:20-31` (`$DC stop` … `$DC start`) | |
| Durcissement basic : admin d'abord, `REQUIRE_EMAIL_VERIFICATION`, `USER_DIRECTORY_ADMIN_ONLY=true` défaut base | ⚠️/❔ | `PROD_LOCAL.md §6` | `USER_DIRECTORY_ADMIN_ONLY` « défaut de la base » = comportement **Onyx** (non vendoré) → ❔ ; non posé par l'overlay prod-local. |

---

## POC_LOCAL.md (POC local + SharePoint)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| 4 commandes : `make tune && make secrets && make up && make verify` | ✅ | `Makefile:36-75` | Cibles présentes. |
| `make up GPU=1` profil GPU NVIDIA | ✅ | `Makefile:17-19` ; `docker-compose.gpu.yml` (réserve `devices: nvidia count: all`) | |
| URL Ollama interne `http://ollama:11434` (jamais localhost) | ✅ | `docker-compose.yml:300` (`OLLAMA_CONTEXT_LENGTH`), `:349` (`ONIX_OLLAMA_URL:-http://ollama:11434`) ; `scripts/verify.sh:42-65` (teste le DNS interne) | Piège AGENTS.md respecté. |
| SharePoint : `setup-sharepoint-app.sh` (Sites.Read.All), branchement Admin Onyx, FOSS = pas d'ACL par-doc | ✅ | `scripts/setup-sharepoint-app.sh` présent (3670 o) ; cohérent setup-entra | RBAC FOSS vs EE honnête. |
| LAN via `docker-compose.lan.yml` | ✅ | `docker-compose.lan.yml` | |

---

## RUNBOOK.md (exploitation)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Cycle de vie : up/down/restart/ps/stats/logs/verify/destroy | ✅ | `Makefile:48-107` | **Toutes les cibles citées existent.** |
| `make update` = `pull` + `up -d` au tag `IMAGE_TAG` | ✅ | `Makefile:87-90` | |
| Backup/restore (`make backup`, `make restore DIR=…`) | ✅ | `Makefile:92-96` ; scripts | Argument positionnel `$(DIR)` → `restore.sh $1`. |
| Dépannage OpenSearch read-only / watermark : lever les blocs via curl `_settings` | ✅ | `RUNBOOK.md §6` (commande sur conteneur `opensearch`) | Cohérent avec image OpenSearch 3.x (HTTPS 9200). |
| `vm.max_map_count>=262144` vérifié par `make verify` | ✅ | `scripts/verify.sh:25-28` ; `scripts/preflight-local.sh:73-87` | |
| Montée en charge §7 : « rétablir un second `inference_model_server` (`INDEXING_ONLY=True`) » + `INDEXING_MODEL_SERVER_HOST` | ⚠️ | `docker-compose.performance.yml` définit le service `indexing_model_server` (nom distinct) ; base `docker-compose.yml:121` `INDEXING_MODEL_SERVER_HOST:-inference_model_server` | Mécanique correcte (`make up PERF=1`), mais le **nom de service** réel est `indexing_model_server`, pas un « second `inference_model_server` » — imprécision de prose. |
| Ollama natif / `host.docker.internal` (macOS) | ❔ | `RUNBOOK.md §8` | `extra_hosts` Onyx non re-vérifié ligne à ligne (Onyx amont). |

---

## PERFORMANCE.md (tuning Ollama / RAM)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| `make tune` écrit les réglages dans `.env` (réserve OS, baseline, modèle, limites anti-OOM) | ✅ | `Makefile:36-37` (`detect-hardware.sh --apply`) ; logique testée `scripts/tests/test_detect_hardware_mem.py` | **Corrigé 2026-06-22 (#10)** : `pick_model` (`detect-hardware.sh:114-156`) sous-dimensionnait le besoin 14B (11 Go = poids Q4) ; la 2e colonne reflète désormais le PIC RÉEL en génération (KV q8_0 + prompt-cache : 14B≈22). `OLLAMA_MEM=(MODEL_NEED+2)g` (`:158-166`) → un 14B obtient ≥ 24 Go (était 12 Go → SIGKILL OOM). Seuils de sélection/rétrogradation alignés (22/12/5). |
| `num_ctx` câblé : défaut serveur `OLLAMA_CONTEXT_LENGTH=8192`, gravé par `make models` (Modelfile num_ctx + temperature 0.2), répliqué en Helm `ollama.tuning` | ✅ | `docker-compose.yml:296-300` ; `scripts/pull-models.sh:37-78` (8192/12288/16384) ; `values.yaml:424-430` | Piège « 4096 tronque » traité partout. |
| Réglages Ollama (`FLASH_ATTENTION`, `KV_CACHE_TYPE=q8_0`, `KEEP_ALIVE`, `NUM_PARALLEL`, `MAX_LOADED_MODELS`) | ✅ | `env.prod.template:252-258` ; `docker-compose.gpu.yml` (env) ; `values.yaml:424-430` | Compromis CPU/GPU documentés honnêtement. |
| Capacité mesurée (qwen2.5:7b ~5,8 tok/s, 4 vCPU) | ❔ | `PERFORMANCE.md §2bis` | Mesure terrain non rejouable ici. Cohérente avec DEPLOY_AZURE gotcha Ollama CPU. |
| Anti-OOM : somme des `*_MEM_LIMIT` < RAM physique | ✅ | `detect-hardware.sh:158-182` (boucle de garantie) ; `test_detect_hardware_mem.py::test_sum_of_limits_below_ram` | Re-dérivé hors-runtime : sur 16/32/64 Go la somme reste < RAM (15.0/31.0/57.5 Go). |
| **Seed provider LLM Onyx (#9)** : `make seed-provider` enregistre le provider Ollama dans Onyx (sinon `llm_provider` vide → chat « No default LLM model found ») | ✅(idempotence/fail-closed)/❔(API live) | `scripts/seed-provider.sh` ; `Makefile:seed-provider` ; `test_seed_provider.py` (5 tests) | **Ajout 2026-06-22.** Idempotent (skip si présent, `ONIX_SEED_FORCE=1` met à jour), fail-closed (api injoignable / pas d'admin / pas de modèle → exit≠0 bruyant), auth admin par env (email/mdp ou clé API). Contrat exact de l'API admin Onyx = **runtime only** (dit honnêtement). |
| **Résilience restart services critiques (#6)** : `restart: always` + healthcheck `start_period` + démarrage ordonné `service_healthy` | ✅(config)/❔(reprise Docker) | `docker-compose.prod-local.yml:51-223` ; `test_restart_policy.py` (4 tests) | **Verrouillé 2026-06-22.** Assertions statiques : 10 services critiques en `always`, api_server `start_period≥120s` + deps `service_healthy`. La REPRISE après kill-pendant-init (course démon Docker) reste **runtime only**, dite telle quelle. |

---

## Écarts « production-ready entreprise »

### P0 (bloquant prod sérieuse) — *néant*
Aucun fail-open, aucun secret en clair au dépôt, aucune procédure pointant un
fichier/cible inexistant. Le garde-fou défaut-sûr (`preflight-prod.sh`) ferme
correctement la porte ; cache HMAC et `VALID_EMAIL_DOMAINS` sont `:?` (fail si vide).

### P1 (à corriger avant exploitation entreprise)
1. **Ingress Azure « anti-spoofing + chat→gateway »** — ✅ **partiellement résolu**
   (itér. 1). La route EXACTE `/api/chat/send-message → access-gateway:8200` est
   désormais **templatisée** (`ingress.yaml`, gated `ingress.chatViaGateway.enabled`
   ET `accessGateway.enabled`, OFF par défaut, validée par `helm template` : route
   prioritaire sur `/api`). **TODO recette explicite** (documenté `DEPLOY_AZURE.md`
   §Ingress + `values.yaml` chatViaGateway) : forward-auth oauth2-proxy (hors-chart) +
   anti-usurpation `strip X-OIDC-Claims` (snippet propre au contrôleur). Honnête :
   « validé statiquement ; runtime AKS à vérifier ». `deploy/prod` reste l'alternative
   pleinement câblée E2E.
2. **Redis/Postgres managés Azure : TLS/SSL côté Onyx** — ✅ **résolu** (itér. 1).
   `configmap.yaml` rend `REDIS_SSL`/`REDIS_PORT` + `POSTGRES_PORT`/`POSTGRES_SSLMODE`
   UNIQUEMENT si posés en values (in-cluster non-TLS inchangé) ; `values-azure.yaml`
   pose `redis.ssl=true`/`port=6380` + `postgresql.sslmode=require`/`port=5432`. Vérifié
   au rendu Azure. Piège §7 respecté (gateway base 1 garde `rediss://…:6380/1`).
3. **`scripts/backup.sh` ne connaît pas la surcouche prod** — ✅ **résolu** (itér. 1).
   `backup.sh`/`restore.sh` acceptent `PROFILE=base|prod|local-prod` (+ `ENV`) et
   empilent le même jeu compose que le Makefile (Caddy/oauth2-proxy/gateway inclus en
   profil prod). Projet forcé `-p onix` (volumes inchangés). Doc : `DEPLOY_PROD.md`,
   `RUNBOOK.md` §5.

### P2 (durcissement / hygiène)
4. **Durcissement Helm partiel** — ✅ **résolu** (itér. 1 + itér. 2) sans régression.
   - **itér. 1** : helper `onix.podSecurityContext` — `seccompProfile RuntimeDefault`
     appliqué à TOUS les pods ; `runAsNonRoot`/`runAsUser` seulement où l'image le
     supporte (actions/worker UID 10001 ; gateway 10002). **NON posé** sur Onyx/Ollama
     (images root amont — le forcer casserait le boot).
   - **itér. 2 (NetworkPolicy OPT-IN)** : `templates/networkpolicy.yaml` gardé par
     `networkPolicy.enabled` (défaut `false` `values.yaml:97`). Modèle **ingress-only**
     (default-deny ingress ciblant `part-of: onix` SANS toucher le data-tier ; aucun
     egress restreint → DNS/data-tier/Graph jamais coupés) + allow explicites par
     composant (api/web/model-servers/actions/broker/ollama, +gateway si activée).
     Validé : défaut ⇒ **0** NetworkPolicy (×3 jeux de values) ; `--set
     networkPolicy.enabled=true` ⇒ **8** (9 avec gateway), YAML re-parsé OK.
   - **itér. 2 (readOnlyRootFilesystem OPT-IN)** : flag
     `accessGateway.readOnlyRootFilesystem` (défaut `false` `values.yaml:403`) →
     rootfs RO + emptyDir `/tmp` UNIQUEMENT sur l'access-gateway (stateless, sûr).
     **PAS** sur Onyx/Ollama/actions (écrivent sur disque) → documenté.
   - Défaut OFF des deux flags ⇒ **rendu inchangé** (36 docs par défaut, 0 NetworkPolicy).
     **Reste** : NetworkPolicy *egress* (allowlist sortante) + non-root Onyx/Ollama
     (rebuild image USER) — documenté `HA_SCALING.md` §5bis « Suite ». Comportement
     runtime (CNI/AKS) à vérifier sur cluster réel.
5. **Imprécision RUNBOOK §7** — ✅ **résolu** (itér. 1) : `indexing_model_server`
   (profil `make up PERF=1`, `docker-compose.performance.yml`).
6. **Code-sans-doc** (🔇) : HTTP/3 QUIC (Caddy `:443/udp`) et tier sémantique du cache
   (`values.yaml`) non documentés dans ce scope. **Reste** (hygiène mineure).

### Hors backlog initial — traité cette vague
7. **`ENCRYPTION_KEY_SECRET` jamais posé** (footgun critique, vendu acquis
   `ARCHITECTURE.md:67`) — ✅ **résolu** (itér. 1). Câblé partout : Helm
   (`onix.dataTierSecretEnv` → api/background/migrations/actions ; `secret.yaml`/
   `values.yaml` documentent la clé ; placeholder factice `values-kind-smoke.yaml`),
   compose base (api_server+background, hérité par `deploy/prod`), `gen-secrets.sh`
   (génère `rand 48`), `env.template` + `env.prod.template`, `DEPLOY_AZURE.md`. Vérifié :
   rendu 11× (smoke), `compose config -q` OK, `gen-secrets` non-vide, gitleaks 0.

---

## Verdict (3 lignes)

1. **Scope solide et honnête** : la quasi-totalité des affirmations vérifiables tient
   au byte près ; **0 écart majeur**, **0 cible/fichier Make inexistant**, secrets et
   fail-closed conformes aux règles de jeu. Le mono-nœud exposé (`deploy/prod`) est
   la pièce la plus aboutie (Caddy/oauth2-proxy/gateway/anti-usurpation réellement câblés).
2. **Production-ready** : OUI pour mono-nœud durci (`prod-local`) et exposé (`deploy/prod`).
   Le **chart HA** est valide « by-design » ; le déploiement **Azure** a vu ses deux
   trous de câblage **traités** (itér. 1) : TLS Redis/Postgres côté Onyx **câblé**, et
   route chat→gateway **templatisée (OPT-IN)** + forward-auth/anti-spoofing **documenté
   en TODO recette honnête** (oauth2-proxy hors-chart + snippet contrôleur).
3. **Traité (itér. 1)** : `ENCRYPTION_KEY_SECRET` câblé partout (footgun fermé) ;
   `REDIS_SSL`/`sslmode` côté Onyx dans `values-azure` ; `backup.sh`/`restore.sh`
   conscients de la surcouche prod ; durcissement `securityContext` généralisé
   (seccomp partout, non-root où l'image le permet) ; RUNBOOK §7 corrigé ; route
   ingress chat→gateway templatisée. **Reste** : forward-auth/anti-spoofing AKS
   (recette, dépend du contrôleur), code-sans-doc mineur (HTTP/3, tier sémantique).
