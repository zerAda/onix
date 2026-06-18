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
                ┌─────────────────────────────────────────────┐
                │  caddy  (reverse-proxy TLS)                  │ ← SEUL service exposé (BIND_IP)
                │  HTTPS auto, HSTS, en-têtes                  │
                │  • supprime tout X-OIDC-Claims ENTRANT (anti-usurpation)
                │  • /oauth2/*           → oauth2-proxy        │
                │  • /api/chat/send-message → forward_auth(oauth2-proxy), puis nginx
                │  • reste du site       → nginx               │
                └───────┬──────────────────────────────┬──────┘
                        │ http (onix-net)               │ /oauth2/auth (forward_auth)
                        ▼                                ▼
        ┌─────────────────────────────┐        ┌─────────────────────────┐
        │  nginx  (routage applicatif)│        │  oauth2-proxy (OIDC)    │
        │  • /api/chat/send-message → │        │  vérifie le jeton Entra,│
        │    access-gateway:8200/v1/… │        │  --set-xauthrequest :   │
        │    + pose X-OIDC-Claims      │◀──────│  X-Auth-Request-User=oid │
        │      depuis l'identité       │ copy   │  (recopié par Caddy)    │
        │      VÉRIFIÉE (sinon vide)   │ headers└─────────────────────────┘
        │  • /api/*, / → Onyx (natif) │
        └───────┬──────────────────┬──┘
                │ chat (RBAC)       │ reste
                ▼                   ▼
   ┌────────────────────────┐   api_server · web_server · background
   │  access-gateway:8200    │   · opensearch · postgres · minio
   │  RBAC : Document Set     │   · ollama · actions   (internes, onix-net)
   │  forcé + cache RBAC-safe │        ▲
   │  (Redis base 1) + ACL    │        │  http://api_server:8080/chat/send-message
   │  par-doc + garde-fous    │────────┘   (GATEWAY_ONYX_BASE_URL)
   │  + streaming + /metrics  │
   └────────────────────────┘
```

> **Nouveau (cette surcouche) :** la passerelle RBAC `access-gateway` — jusqu'ici
> orpheline (présente dans `access-gateway/` mais **déployée nulle part**) — est
> désormais **dans le chemin de requête du chat**. Toute requête
> `/api/chat/send-message` la traverse (RBAC appliqué), derrière oauth2-proxy
> (identité vérifiée) et Caddy (TLS + anti-usurpation). Détails : **§5bis**.

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
| `deploy/prod/docker-compose.prod.yml` | Surcouche prod : Caddy TLS, OIDC forcé, nginx interne, garde-fou, image `actions` épinglée, **+ `access-gateway` (RBAC) et `oauth2-proxy` (vérificateur OIDC), internes** (cf. §5bis). |
| `deploy/prod/Caddyfile` | Reverse-proxy TLS (HTTPS auto, HSTS, en-têtes). **Route `/oauth2/*` → oauth2-proxy, protège `/api/chat/send-message` par `forward_auth`, supprime tout `X-OIDC-Claims` entrant (anti-usurpation).** |
| `deploy/prod/nginx.prod.conf` | Variante nginx prod : honore `X-Forwarded-Proto=https` de Caddy. **Route `/api/chat/send-message` → `access-gateway:8200/v1/chat/send-message` en posant `X-OIDC-Claims` depuis l'identité vérifiée ; efface `X-OIDC-Claims` sur les autres routes.** |
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

## 5bis. Passerelle RBAC (`access-gateway`) dans le chemin du chat

> **Pourquoi cette section existe.** Toute la couche de sécurité d'entreprise —
> RBAC par groupe/Document Set, cache RBAC-safe, streaming SSE, filtre ACL
> par-document, `/metrics` — vit dans `access-gateway/` mais n'était **déployée
> nulle part** : un service **orphelin**, hors du chemin de requête. Cette
> surcouche le rend **réel** : chaque requête de chat le traverse, RBAC appliqué.

### a) Topologie du chemin de requête

```
Navigateur
  │ POST https://<ONYX_DOMAIN>/api/chat/send-message   (fetch/XHR, streaming)
  ▼
caddy  ── supprime tout X-OIDC-Claims & X-Auth-Request-* ENTRANT (anti-usurpation)
  │     ── forward_auth → oauth2-proxy:/oauth2/auth
  │           • 2xx (session valide) → copy_headers X-Auth-Request-User (=oid), …
  │           • 401 → remonté tel quel (XHR ; cf. limite « double session » plus bas)
  ▼
nginx  ── location = /api/chat/send-message
  │     ── pose X-OIDC-Claims = {"oid":…,"sub":…,"upn":…}  (UNIQUEMENT depuis
  │        l'identité vérifiée recopiée par Caddy ; vide si absente → 401 passerelle)
  │     ── rewrite → /v1/chat/send-message ; proxy_buffering off (streaming)
  ▼
access-gateway:8200  ── RBAC : force retrieval_options.filters.document_set au
  │                      périmètre autorisé (deny-by-default, non élargissable),
  │                      cache RBAC-safe (Redis base 1), garde-fous, ACL par-doc,
  │                      streaming SSE, /metrics
  ▼
api_server:8080/chat/send-message   (Onyx, interne onix-net)
```

Le **reste** du site (UI Onyx, autres `/api/*`, `/openapi.json`, WebSocket,
uploads) continue d'aller **directement à Onyx** via nginx, avec l'**OIDC natif
d'Onyx** (inchangé). On ne route par la passerelle **que** le chemin chat : c'est
là que se joue le cloisonnement RBAC, et cela évite d'imposer un second flux OIDC
à toute l'UI.

### b) Contrat OIDC → `X-OIDC-Claims` (identité vérifiée)

La passerelle **fait confiance** à l'en-tête `X-OIDC-Claims` (JSON de claims
**déjà vérifiés**). Rien dans la base ne produisait cet en-tête : l'OIDC natif
d'Onyx authentifie l'UI mais n'**expose pas** les claims à un proxy tiers. D'où
l'ajout d'**oauth2-proxy** comme **vérificateur OIDC** :

1. `oauth2-proxy` porte la poignée de main OIDC Entra (app registration **dédiée**,
   Redirect URI `https://<ONYX_DOMAIN>/oauth2/callback`), valide le jeton, tient un
   cookie de session, et — avec `--set-xauthrequest` — **renvoie** l'identité
   vérifiée en en-têtes de **réponse** sur `/oauth2/auth` :
   - `--user-id-claim=oid`  → `X-Auth-Request-User` = **oid** (objectId Entra,
     identité **stable** utilisée aussi par Microsoft Graph) ;
   - `--oidc-email-claim=upn` / `X-Auth-Request-Preferred-Username` = **upn**.
2. **Caddy** (`forward_auth` → `/oauth2/auth`) **recopie** ces en-têtes (`copy_headers`)
   sur la requête transmise à nginx **si** la session est valide.
3. **nginx** assemble alors `X-OIDC-Claims = {"oid":"…","sub":"…","upn":"…"}` à
   partir de ces valeurs **vérifiées** (directive `set`, qui interpole les
   variables — un `map` n'interpole pas dans une chaîne, cf. commentaires du
   fichier) et le transmet à la passerelle.

La passerelle lit `oid`/`upn` (cf. `access-gateway/app/identity.py`) puis, en mode
`GATEWAY_GROUP_SOURCE=graph` (**défaut prod**), résout l'appartenance aux groupes
via **Microsoft Graph** `transitiveMemberOf` (permission **application** de moindre
privilège `GroupMember.Read.All`). Elle traduit groupe → Document Set (mapping
JSON, deny-by-default) et force le périmètre.

### c) Règle anti-usurpation (le point critique)

`X-OIDC-Claims` est **digne de confiance UNIQUEMENT** parce qu'un client ne peut
pas le poser. Garanti à **deux** niveaux (défense en profondeur) :

- **Au bord (Caddy)** : `request_header -X-OIDC-Claims` (et `-X-Auth-Request-*`)
  **supprime** tout en-tête entrant homonyme **avant** tout routage. Un client qui
  envoie `X-OIDC-Claims: {"oid":"admin",…}` se le fait **retirer** immédiatement.
- **Au hop interne (nginx)** : sur **chaque** `location`, `proxy_set_header
  X-OIDC-Claims …` **écrase** la valeur — vide partout **sauf** sur le chemin chat,
  où elle est (re)construite **exclusivement** depuis l'identité vérifiée par
  oauth2-proxy. Aucune valeur cliente ne survit.

**Fail-closed** : si oauth2-proxy n'a pas fourni d'oid (session absente),
`X-OIDC-Claims` est **vide** → la passerelle répond **401** (jamais un passage
« ouvert »). Groupes irrésolvables (overage + Graph en erreur) → **502** ; aucun
Document Set autorisé → **403** (`GATEWAY_DENY_IF_NO_MATCH=true`).

### d) Activer / régler les capacités en prod (variables d'env)

Toutes dans `deploy/prod/.env.prod` (cf. `env.prod.template`, section « PASSERELLE
RBAC »). Valeurs par défaut **sûres** ; voici les leviers :

| Capacité | Variable(s) clés | Notes |
|---|---|---|
| **Cache RBAC-safe** | `GATEWAY_CACHE_ENABLED=true`, `GATEWAY_CACHE_HMAC_SECRET` (**requis**), TTL/locale | Réutilise le **Redis `cache`** existant en **base 1** (Onyx = base 0) : la passerelle compose `redis://:${REDIS_PASSWORD}@cache:6379/1`. Clé HMAC = périmètre Document Set **trié** → **aucune** fuite inter-utilisateur. Sans secret HMAC stable, le cache se **désactive** au démarrage (log CRITICAL). Cf. `docs/CACHE.md`. |
| **ACL par-document** | `GATEWAY_DOC_ACL_ENABLED=true`, fichier monté sur `/config/doc_acl.json` | **ACTIF seulement** si le fichier ACL existe (sinon **INACTIF** + avertissement — un deny-all serait une panne). Générable via `make sync-doc-acl`. Filtre de **sortie** (retire les citations non autorisées). Cf. `docs/RBAC.md`. |
| **ACL SharePoint via Graph** | `GATEWAY_DOC_ACL_GRAPH_ENABLED=true`, mapping `/config/doc_acl_mapping.json` | **Opt-in** : nécessite Graph configuré. OR-merge avec l'ACL statique. Re-sync selon `GATEWAY_DOC_ACL_REFRESH_SECONDS`. |
| **Streaming SSE** | `GATEWAY_STREAM_ENABLED=true`, `GATEWAY_STREAM_IDLE_TIMEOUT` | Relais token-par-token (latence perçue ÷10 sur CPU). nginx **désactive le buffering** sur le chemin chat. Cf. `docs/STREAMING.md`. |
| **Garde-fous** | `GATEWAY_GUARDRAIL_ENABLED=true` | Post-filtre déterministe (couche 3) sur la réponse. Laisser actif. |
| **/metrics** | `GATEWAY_METRICS_ENABLED=true` | Prometheus, réseau interne (pas de port hôte). À scraper depuis `monitoring/`. |
| **Source des groupes** | `GATEWAY_GROUP_SOURCE=graph` + `GATEWAY_GRAPH_*` | `graph` recommandé (cf. §5bis-b). `auto`/`claims` : voir limite ci-dessous. |

**Secrets** : `GATEWAY_CACHE_HMAC_SECRET` et `GATEWAY_AUDIT_SALT` sont générés par
`make secrets-gateway` (écrit dans `access-gateway/.env`) — **recopiez-les** dans
`deploy/prod/.env.prod`, ou injectez-les depuis le **coffre** (Key Vault). Le
secret client Graph va dans `GATEWAY_GRAPH_CLIENT_SECRET`, le secret oauth2-proxy
dans `OAUTH2_PROXY_CLIENT_SECRET`, et `OAUTH2_PROXY_COOKIE_SECRET` se génère via
`openssl rand -base64 32`. *(Note : `scripts/gen-secrets.sh` ne génère
automatiquement les `GATEWAY_*` que pour le fichier de la passerelle, pas pour
`.env.prod` — d'où la recopie/coffre.)*

### e) Mapping & app registration Entra (à préparer)

- **Mapping groupe → Document Set** : montez votre fichier réel sur
  `/config/group_map.json` (le compose monte par défaut l'**exemple** versionné
  `access-gateway/config/group_map.example.json` — **à adapter**, deny-by-default).
- **Entra** : déclarez (au choix) **une app dédiée oauth2-proxy** (Redirect URI
  `…/oauth2/callback`) **et** une app/permission **Graph** `GroupMember.Read.All`
  (consentement admin) pour la résolution des groupes. L'oid émis par oauth2-proxy
  et l'oid interrogé par Graph **coïncident** (même tenant).

### f) Limites HONNÊTES (ce qui exige un tenant Entra réel / a un compromis)

1. **Validable hors ligne (fait ici)** : `compose config -q` (base + prod) ✓,
   `caddy validate` ✓ (*Valid configuration*), parse nginx ✓, topologie
   interne-only (seul Caddy publie 80/443) ✓, règle anti-usurpation **présente**
   dans Caddy **et** nginx ✓.
2. **Exige un tenant Entra + domaine réels (NON validé ici, pas d'IdP en CI)** :
   le **flux OIDC bout-en-bout** d'oauth2-proxy (sign-in, callback, cookie), la
   présence effective de `X-Auth-Request-User=oid` après `/oauth2/auth`, et la
   résolution **Graph** des groupes. À vérifier en `test` (cf. §9) avant prod.
3. **Double session OIDC** (compromis assumé) : l'UI Onyx utilise l'OIDC **natif**
   d'Onyx ; le chemin chat utilise la session **oauth2-proxy**. Au **premier**
   appel chat, oauth2-proxy n'a pas de session → **401** (non redirigé, car XHR).
   L'utilisateur établit la session oauth2-proxy par **une** navigation de premier
   niveau vers `https://<ONYX_DOMAIN>/oauth2/sign_in` (SSO Entra **silencieux** s'il
   est déjà connecté), après quoi les XHR portent le cookie. *Mieux à terme :*
   placer oauth2-proxy **devant tout** le site (session unique) — non fait ici pour
   ne pas casser l'UI Onyx (Onyx FOSS ne délègue pas proprement l'auth à un en-tête
   amont) ; c'est le compromis du périmètre « 5 fichiers, sans toucher au code ».
4. **Groupes en mode `claims`/`auto`** : `oauth2-proxy` émet les groupes en **CSV**
   (`X-Auth-Request-Groups`), or la passerelle attend un **tableau JSON** dans
   `groups`. nginx **pur** (sans `njs`) ne reconstruit pas un tableau JSON fiable
   depuis une chaîne CSV → on **n'injecte pas** `groups` dans `X-OIDC-Claims`. On
   privilégie donc `GATEWAY_GROUP_SOURCE=graph` (robuste, gère l'overage > ~200
   groupes). Pour `auto`/`claims`, il faudrait soit `njs`, soit qu'oauth2-proxy
   émette un en-tête JSON ad hoc (alpha `injectResponseHeaders`) — **hors du
   périmètre** de cette livraison.
5. **`compose config -q` autonome** (`-f deploy/prod/docker-compose.prod.yml` seul)
   **échoue volontairement** : la surcouche **référence** des services de la base
   (`cache`, `api_server`, `nginx`). Valider **empilé** :
   `make config-prod ENV=deploy/prod/.env.prod` (cf. §12).

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
- **En déploiement PROD exposé** (`deploy/prod/`), passez le profil pour que l'arrêt
  bref de cohérence inclue AUSSI Caddy/oauth2-proxy/access-gateway (sinon le bord
  resterait actif pendant la sauvegarde) :
  ```bash
  PROFILE=prod ENV=deploy/prod/.env.prod make backup
  PROFILE=prod ENV=deploy/prod/.env.prod make restore DIR=backups/<horodatage>
  ```
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
      `nginx`, `access-gateway`, `oauth2-proxy` n'exposent **aucun** port public.
- [ ] **Passerelle RBAC** (§5bis) : `access-gateway` et `oauth2-proxy` **healthy** ;
      `OAUTH2_PROXY_*`, `GATEWAY_GRAPH_*`, `GATEWAY_CACHE_HMAC_SECRET`,
      `GATEWAY_AUDIT_SALT` renseignés (coffre) ; mapping `group_map.json` **adapté**
      (pas l'exemple) ; chemin chat **vérifié** end-to-end en `test` (un appel
      `/api/chat/send-message` **sans** session oauth2-proxy → **401** ; **avec** →
      Document Set forcé, cf. logs `onix.gateway.audit`).
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
logique du garde-fou (refus/passe), `gitleaks 0`, gitignore des `.env`, **et — pour
la passerelle RBAC (§5bis) — la présence de la règle anti-usurpation (Caddy + nginx)
et le câblage du chemin chat → `access-gateway:8200/v1/chat/send-message`**.

Exige un **vrai domaine public + DNS + ports 80/443 ouverts + tenant Entra ID** :
- émission/renouvellement **réels** du certificat Let's Encrypt (ACME) ;
- bout-en-bout **OIDC** (redirection vers Entra ID, callback, vérification d'e-mail,
  filtrage `VALID_EMAIL_DOMAINS`) ;
- HSTS observé dans un navigateur, redirection `80→443` en conditions réelles ;
- **passerelle RBAC bout-en-bout** (§5bis-f) : flux OIDC d'**oauth2-proxy**,
  présence de `X-Auth-Request-User=oid` après `/oauth2/auth`, résolution **Graph**
  des groupes, forçage du Document Set sur un vrai `/api/chat/send-message`. **Non
  validable sans IdP** : à exercer en `test` avant la bascule prod.

> Pour la **haute disponibilité, le scale-out et le sans-interruption strict**,
> se référer à **WS4**. La présente surcouche cible un **mono-nœud durci, exposé,
> reproductible**.
