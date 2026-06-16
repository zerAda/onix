# Déploiement de PRODUCTION — onix (Onyx + Ollama)

Runbook d'un déploiement **d'entreprise exposé** : reverse-proxy **TLS** (Caddy,
HTTPS automatique), **SSO Microsoft Entra ID** (OIDC) forcé, **multi-
environnement** (dev / test / prod) et **garde-fou de défaut-sûr** qui refuse de
démarrer une instance exposée sans TLS + OIDC + vérification d'e-mail.

> Cette page complète `docs/SECURITY.md` §6 (durcissement pour exposition) et
> `docs/RUNBOOK.md` (exploitation locale). Pour la **haute disponibilité / le
> sans-interruption multi-nœuds**, voir **WS4** (réplication, bascule, scale-out).

---

## 0. Modèle de déploiement

```
                         Internet / LAN
                              │  443 (TLS), 80 (ACME + redirection)
                              ▼
                ┌─────────────────────────────┐
                │  caddy  (reverse-proxy TLS)  │  ← SEUL service exposé (BIND_IP)
                │  HTTPS auto, HSTS, en-têtes  │
                └──────────────┬──────────────┘
                               │  http  (réseau interne onix-net)
                               ▼
                ┌─────────────────────────────┐
                │  nginx  (routage applicatif) │  ← repassé en INTERNE (127.0.0.1)
                │  /api → api_server, / → web  │
                └──────────────┬──────────────┘
                               ▼
        api_server · web_server · background · opensearch · postgres
        · minio · redis · ollama · actions      (tous internes, onix-net)
```

- **Caddy** termine TLS sur `:443`, obtient/renouvelle les certificats Let's
  Encrypt **automatiquement**, redirige `80 → 443`, pose **HSTS** + en-têtes de
  sécurité, puis proxifie vers le **nginx interne** (routage `/api`, WebSocket,
  upload 5 Go **réutilisé tel quel**, déjà audité).
- En prod, **nginx n'expose plus de port public** (surcouche : `ports: !reset []`,
  `expose: "80"`) : Caddy l'atteint via le réseau Docker `onix-net`.
- Le compose de base n'est **jamais modifié** : la prod est une **surcouche**
  (`deploy/prod/docker-compose.prod.yml`) empilée avec `-f`.

### Fichiers livrés (WS3)

| Fichier | Rôle |
|---|---|
| `deploy/prod/docker-compose.prod.yml` | Surcouche prod : Caddy TLS, OIDC forcé, nginx interne, garde-fou, image `actions` épinglée. |
| `deploy/prod/Caddyfile` | Reverse-proxy TLS (HTTPS auto, HSTS, en-têtes, proxy → `nginx:80`). |
| `deploy/prod/nginx.prod.conf` | Variante nginx prod : honore `X-Forwarded-Proto=https` de Caddy (callbacks OIDC / cookies sécurisés corrects). Routage identique à la base. |
| `deploy/prod/env.prod.template` | Gabarit d'environnement prod/test (à copier en `.env.prod` / `.env.test`). |
| `scripts/preflight-prod.sh` | Garde-fou « défaut-sûr » : refuse une exposition sans TLS+OIDC+e-mail. |
| `Makefile` (bloc `--- WS3 ---`) | Cibles `config-prod`, `up-prod`, `down-prod`, `secrets-prod`, `preflight-prod`, … |

---

## 1. Schéma multi-environnement (dev / test / prod)

**Un fichier d'environnement par environnement** ; la même base de compose sert
partout, seule la surcouche et le `.env` changent.

| Env | Fichier env | Gabarit | Auth | Exposition | Démarrage |
|---|---|---|---|---|---|
| **dev** (local) | `.env` | `env.template` | `basic` | `127.0.0.1` | `make up` |
| **test** (pré-prod) | `deploy/prod/.env.test` | `deploy/prod/env.prod.template` | `oidc` | IP interne / domaine de test | `make up-prod ENV=deploy/prod/.env.test` |
| **prod** | `deploy/prod/.env.prod` | `deploy/prod/env.prod.template` | `oidc` | domaine public | `make up-prod ENV=deploy/prod/.env.prod` |

> **Astuce test sans rate-limit** : en `test`, mettre
> `ACME_CA=https://acme-staging-v02.api.letsencrypt.org/directory` (certificats
> Let's Encrypt **staging**, non fiables côté navigateur mais sans quota), ou
> `tls internal` dans le Caddyfile (CA locale Caddy).

---

## 2. Pré-requis

- Docker + Docker Compose v2 (`docker compose`).
- Un **serveur** (VM/bare-metal) avec ports **80 et 443 ouverts** vers `BIND_IP`.
- Un **domaine public** (`ONYX_DOMAIN`) dont l'enregistrement **DNS A/AAAA**
  pointe vers `BIND_IP`. Indispensable pour le certificat Let's Encrypt (ACME
  HTTP-01/TLS-ALPN-01).
- Un **enregistrement d'application Entra ID** (Azure portal → App registrations).
- Linux : `vm.max_map_count >= 262144` (OpenSearch — cf. `RUNBOOK.md` §6).

---

## 3. Mise en route prod (pas à pas)

```bash
# 1) Préparer l'environnement de production
cp deploy/prod/env.prod.template deploy/prod/.env.prod
#    → éditer deploy/prod/.env.prod : ONYX_DOMAIN, ACME_EMAIL, BIND_IP,
#      OAUTH_CLIENT_ID/SECRET, OPENID_CONFIG_URL, VALID_EMAIL_DOMAINS, ACTIONS_IMAGE…

# 2) Générer / compléter les secrets dans CE fichier (idempotent)
make secrets-prod ENV=deploy/prod/.env.prod
#    (équivaut à : ENV_FILE=deploy/prod/.env.prod ./scripts/gen-secrets.sh)
#    En entreprise : injecter plutôt les secrets depuis un COFFRE (Key Vault). Cf. §8.

# 3) Valider la composition AVANT de démarrer (résout les variables, ne lance rien)
make config-prod ENV=deploy/prod/.env.prod        # → "✓ base + prod valide"

# 4) (option) Vérifier le garde-fou de défaut-sûr en sec
make preflight-prod ENV=deploy/prod/.env.prod

# 5) Démarrer la production
make up-prod ENV=deploy/prod/.env.prod

# 6) Premier accès : https://<ONYX_DOMAIN> → Caddy émet le certificat au vol.
```

Le **premier compte** qui se connecte (via Entra ID) devient **administrateur** :
connectez-vous immédiatement avec le compte admin prévu.

---

## 4. TLS (Caddy — HTTPS automatique)

- **Aucune action manuelle de certificat.** Au premier accès à `https://<ONYX_DOMAIN>`,
  Caddy résout le challenge ACME (HTTP-01 sur `:80`, TLS-ALPN-01 sur `:443`),
  obtient le certificat, l'**agrafe (OCSP)** et le **renouvelle** avant expiration.
- Les certificats/clés sont persistés dans le volume **`caddy_data`** : ne pas le
  supprimer (sinon ré-émission → risque de **rate-limit** Let's Encrypt).
- **HSTS** : `max-age=63072000; includeSubDomains; preload` (2 ans). En-têtes posés
  sur **toutes** les réponses : `X-Content-Type-Options`, `X-Frame-Options:SAMEORIGIN`,
  `Referrer-Policy`, `Permissions-Policy` ; en-têtes `Server`/`X-Powered-By` retirés.
- **Redirection 80 → 443** : native, automatique (confirmée par `caddy validate` :
  *enabling automatic HTTP->HTTPS redirects*).

**Validation syntaxe du Caddyfile** (sans domaine/certs) :
```bash
docker run --rm -e ONYX_DOMAIN=exemple.fr -e ACME_EMAIL=ops@exemple.fr \
  -v "$PWD/deploy/prod/Caddyfile":/etc/caddy/Caddyfile:ro \
  caddy:2.10-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

> **Variante sans domaine public** (lab / réseau fermé) : remplacer la ligne
> `{$ONYX_DOMAIN} {` par `https://{$ONYX_DOMAIN} {` + directive `tls internal`
> (Caddy génère une CA locale). À réserver aux environnements de test.

---

## 5. SSO Microsoft Entra ID (OIDC) — forcé en prod

La surcouche prod **impose** : `AUTH_TYPE=oidc`, `REQUIRE_EMAIL_VERIFICATION=true`,
`VALID_EMAIL_DOMAINS=<vos domaines>`, `WEB_DOMAIN=https://<ONYX_DOMAIN>`,
`OIDC_PKCE_ENABLED=true`.

### a) Enregistrement d'application Entra ID (Azure portal)

1. **App registrations → New registration** (compte mono-tenant recommandé).
2. **Redirect URI** (plateforme *Web*) :
   `https://<ONYX_DOMAIN>/auth/oidc/callback`
   (le chemin `/auth/oidc/callback` est servi par Onyx via nginx → Caddy).
3. Noter l'**Application (client) ID** → `OAUTH_CLIENT_ID`.
4. **Certificates & secrets → New client secret** → `OAUTH_CLIENT_SECRET`.
5. URL de configuration OpenID (v2.0) → `OPENID_CONFIG_URL` :
   `https://login.microsoftonline.com/<TENANT_ID>/v2.0/.well-known/openid-configuration`
   (`<TENANT_ID>` = ID de tenant **GUID** ou domaine).

### b) Variables (dans `deploy/prod/.env.prod`)

```dotenv
AUTH_TYPE=oidc
OAUTH_CLIENT_ID=<client-id>
OAUTH_CLIENT_SECRET=<client-secret>
OPENID_CONFIG_URL=https://login.microsoftonline.com/<TENANT_ID>/v2.0/.well-known/openid-configuration
OIDC_PKCE_ENABLED=true
REQUIRE_EMAIL_VERIFICATION=true
VALID_EMAIL_DOMAINS=exemple.fr            # CSV : ferme la porte hors organisation
WEB_DOMAIN=https://<ONYX_DOMAIN>          # HTTPS obligatoire (cookies + callback)
# OIDC_SCOPE_OVERRIDE=openid,email,profile  # (option) remplace les scopes par défaut
```

> **Important** : `WEB_DOMAIN` doit être en `https://` et identique au domaine
> public, sinon la redirection OIDC et les cookies sécurisés cassent. Le garde-fou
> (§6) le vérifie.

### c) Propagation du contexte TLS (X-Forwarded-Proto)

En prod, nginx est un hop **interne** derrière Caddy : la requête y arrive en
`http`. Pour qu'Onyx construise des **URLs de callback OIDC** et des **cookies
sécurisés** corrects, la surcouche monte `deploy/prod/nginx.prod.conf` (à la place
de `nginx/onyx.conf`), qui **propage** le `X-Forwarded-Proto: https` envoyé par
Caddy (au lieu d'écraser avec `$scheme`). Routage par ailleurs **identique**.

**Validation syntaxe nginx** (registre accessible requis) :
```bash
docker run --rm -v "$PWD/deploy/prod/nginx.prod.conf":/etc/nginx/conf.d/default.conf:ro \
  nginx:1.27-alpine nginx -t
```

---

## 6. Garde-fou de défaut-sûr (refus de démarrer non sécurisé)

Le service **`preflight`** (conteneur busybox éphémère, `scripts/preflight-prod.sh`)
s'exécute **avant** `api_server`, `web_server`, `background` et `caddy`
(`depends_on: condition: service_healthy`). Il applique la règle :

> **Si `BIND_IP` ≠ `127.0.0.1` (exposition réseau), alors TLS + OIDC +
> vérification d'e-mail sont OBLIGATOIRES**, sinon **refus de démarrer**.

Contrôles (en exposition) — tout manquement bloque le démarrage :

| Contrôle | Exigence |
|---|---|
| TLS / domaine | `ONYX_DOMAIN` non vide **et** `WEB_DOMAIN` en `https://` |
| OIDC | `AUTH_TYPE=oidc`, `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OPENID_CONFIG_URL` (forme `https://…/.well-known/openid-configuration`) |
| Vérification e-mail | `REQUIRE_EMAIL_VERIFICATION=true` **et** `VALID_EMAIL_DOMAINS` non vide |

En **localhost strict** (`BIND_IP=127.0.0.1`, cas dev/test sur l'hôte), le garde-fou
laisse passer (surface limitée à la machine). Diagnostic manuel :
`make preflight-prod ENV=…`.

> Conséquence : une coquille (`AUTH_TYPE=basic` laissé, `WEB_DOMAIN` en `http://`,
> domaines e-mail oubliés) sur une instance exposée **n'aboutit pas** à une mise en
> ligne ouverte — la stack refuse de monter. C'est la garantie « pas de réserve
> laissée ouverte ».

---

## 7. Reproductibilité — images épinglées & build CI (coordination WS6)

- **Onyx / Ollama / Postgres / OpenSearch / MinIO / Redis / nginx / Caddy** :
  images **épinglées par tag** (cf. compose base + `IMAGE_TAG`, `caddy:2.10-alpine`).
- **`actions`** (microservice local) : en prod, **pas de build implicite**. La
  surcouche neutralise le `build:` (`build: !reset null`) et impose une image de
  **registre épinglée** via `ACTIONS_IMAGE`, idéalement par **digest** :

  ```dotenv
  ACTIONS_IMAGE=ghcr.io/<org>/onix-actions:1.0.0@sha256:<digest_64_hex>
  ```

### Build/push en CI (à câbler par WS6 — ne pas toucher `.github/` ici)

Le pipeline qui construit et pousse l'image `actions` relève de **WS6**. Recette
recommandée (à intégrer dans un workflow GitHub Actions par WS6) :

```bash
# Construire depuis ./actions (Dockerfile épinglé : python:3.11.11-slim-bookworm)
docker build -t ghcr.io/<org>/onix-actions:1.0.0 ./actions
# Pousser, puis RÉCUPÉRER LE DIGEST pour épinglage fort
docker push ghcr.io/<org>/onix-actions:1.0.0
docker inspect --format '{{index .RepoDigests 0}}' ghcr.io/<org>/onix-actions:1.0.0
#   → ghcr.io/<org>/onix-actions:1.0.0@sha256:…   (à reporter dans ACTIONS_IMAGE)
```

- Versionner `ACTIONS_IMAGE` (tag **immuable** + digest) par release.
- Le déploiement prod fait `docker compose … pull` (cf. `pull_policy: always`) puis
  `up -d` : il **tire l'image figée**, jamais un build local divergent.

---

## 8. Gestion des secrets

- `deploy/prod/.env*` est **gitignoré** (`.gitignore`) ; ne jamais le committer.
  `gen-secrets.sh` applique `chmod 600`.
- **En entreprise** : préférer un **coffre** (Azure Key Vault, HashiCorp Vault,
  secrets CI) et injecter les valeurs au déploiement (variables d'environnement
  ou rendu du `.env.prod` au runtime), plutôt que des secrets en clair sur disque.
- Secrets concernés (mêmes clés que la base) : `SECRET`, `USER_AUTH_SECRET`,
  `POSTGRES_PASSWORD`, `DB_READONLY_PASSWORD`, `OPENSEARCH_ADMIN_PASSWORD`,
  `MINIO_ROOT_USER/PASSWORD`, `S3_AWS_*`, `REDIS_PASSWORD`, `ONIX_ACTIONS_API_KEY`,
  **+ `OAUTH_CLIENT_SECRET`** (Entra ID).
- **Rotation** : mettre à jour la valeur (coffre/`.env.prod`) puis `make up-prod`.
  ⚠ Certains secrets (ex. `OPENSEARCH_ADMIN_PASSWORD`) impliquent une réinit du
  volume — cf. `RUNBOOK.md`.

---

## 9. Promotion dev → test → prod

1. **dev** : développer/valider en local (`make up`, `make verify`) — profil `basic`,
   `127.0.0.1`.
2. **test (pré-prod)** : créer `deploy/prod/.env.test` (OIDC pointant sur une **app
   Entra ID de test** ou un tenant de test ; `ACME_CA` **staging** ou `tls internal`).
   ```bash
   cp deploy/prod/env.prod.template deploy/prod/.env.test   # éditer (domaine test)
   make secrets-prod ENV=deploy/prod/.env.test
   make config-prod  ENV=deploy/prod/.env.test
   make up-prod      ENV=deploy/prod/.env.test
   ```
   Recette : SSO Entra ID OK, citations, génération, `make verify`. **Figer
   `IMAGE_TAG` et `ACTIONS_IMAGE` (digest)** identiques à ce qui ira en prod.
3. **prod** : reporter **les mêmes tags/digests** validés en test dans
   `deploy/prod/.env.prod`, puis `make config-prod` → `make up-prod`. **Aucune image
   non testée ne doit changer entre test et prod** (reproductibilité §7).

> Règle d'or : on **promeut des artefacts identiques** (tags + digests), on ne
> reconstruit pas entre les étages. Seul l'`.env` (domaine, IdP, secrets) diffère.

---

## 10. Sauvegarde / restauration

- **Sauvegarde** : `make backup` (cf. `RUNBOOK.md` §5) archive `db_volume`,
  `opensearch-data`, `minio_data`, `file-system`.
- **Le volume `caddy_data`** (certificats) peut aussi être archivé pour éviter une
  ré-émission après restauration sur une nouvelle machine.
- **Sans interruption (best effort, mono-nœud)** : Postgres et OpenSearch
  supportent un **dump à chaud** (`pg_dump`, snapshot OpenSearch) sans arrêter la
  stack. Exemple Postgres :
  ```bash
  docker compose --env-file deploy/prod/.env.prod \
    -f docker-compose.yml -f deploy/prod/docker-compose.prod.yml \
    exec -T relational_db pg_dump -U postgres -Fc postgres > backup_pg_$(date +%F).dump
  ```
  > `make backup` réalise un arrêt **bref** (cohérence des volumes). Pour une
  > **vraie continuité de service** (sauvegarde et mises à jour **sans coupure**,
  > bascule, réplication), voir **WS4 (haute disponibilité)** : c'est là que se
  > traite le sans-interruption au sens strict (plusieurs réplicas, rolling update).

---

## 11. Rollback

```bash
# A) Rollback applicatif (mauvaise version d'image)
#    1. Restaurer les tags PRÉCÉDENTS dans deploy/prod/.env.prod :
#         IMAGE_TAG=<ancien>           (Onyx)
#         ACTIONS_IMAGE=…@sha256:<ancien-digest>
#    2. Re-tirer + redémarrer (migrations Alembic : voir note ci-dessous)
make up-prod ENV=deploy/prod/.env.prod
make config-prod ENV=deploy/prod/.env.prod   # revalider

# B) Rollback données (migration ratée / corruption)
make restore DIR=backups/<horodatage>        # cf. RUNBOOK §5
```

> ⚠ **Migrations de schéma** : Onyx applique `alembic upgrade head` au démarrage de
> `api_server`. Un rollback de version peut nécessiter une **restauration de la
> base** (B) si la migration n'est pas réversible. **Toujours `make backup` avant
> une montée de version.** Pour un rollback **sans coupure**, voir **WS4**.

---

## 12. Checklist d'acceptation (prod)

- [ ] `make config-prod ENV=deploy/prod/.env.prod` → **✓ base + prod valide**.
- [ ] `caddy validate` sur `deploy/prod/Caddyfile` → **Valid configuration**.
- [ ] `make preflight-prod` : en exposition, **refuse** sans TLS+OIDC+e-mail ;
      **passe** avec la configuration complète.
- [ ] `docker compose … ps` : **seul `caddy` publie 80/443** (sur `BIND_IP`) ;
      `nginx` n'expose **aucun** port public.
- [ ] `https://<ONYX_DOMAIN>` répond en **TLS valide**, `http://` **redirige** vers `https://`.
- [ ] En-tête **HSTS** présent ; connexion **via Entra ID** uniquement.
- [ ] `deploy/prod/.env.prod` : `chmod 600`, **non** versionné, secrets non vides
      (dont `OAUTH_CLIENT_SECRET`).
- [ ] `IMAGE_TAG` + `ACTIONS_IMAGE` **épinglés** (digest) et **identiques** à ceux validés en test.
- [ ] `make backup` testé ; procédure de rollback (§11) connue.
- [ ] **gitleaks 0** sur le dépôt.

---

## 13. Validation finale — ce qui exige un vrai domaine/certs

Validable **hors ligne / sans domaine** (fait par WS3) : syntaxe Caddyfile
(`caddy validate` → *Valid configuration*), composition `config -q` (base + prod),
logique du garde-fou (refus/passe), `gitleaks 0`, gitignore des `.env`.

Exige un **vrai domaine public + DNS + ports 80/443 ouverts + tenant Entra ID** :
- émission/renouvellement **réels** du certificat Let's Encrypt (ACME) ;
- bout-en-bout **OIDC** (redirection vers Entra ID, callback, vérification d'e-mail,
  filtrage `VALID_EMAIL_DOMAINS`) ;
- HSTS observé dans un navigateur, redirection `80→443` en conditions réelles.

> Pour la **haute disponibilité, le scale-out et le sans-interruption strict**,
> se référer à **WS4**. La présente surcouche cible un **mono-nœud durci, exposé,
> reproductible**.
