# Sécurité — onix — Stack IA souveraine (Onyx + Ollama)

Baseline de sécurité et choix de durcissement. Objectif : un déploiement local
**défendable face à un audit exigeant**, sans réserve laissée ouverte.

## 1. Principes

| Principe | Mise en œuvre |
|---|---|
| Moindre exposition | Un seul port publié, lié à `127.0.0.1`. Tout le reste est interne. |
| Moindre privilège | Auth par comptes locaux ; énumération des comptes réservée aux admins. |
| Pas de secret en clair | Secrets générés localement, `.env` gitignoré + `chmod 600`. |
| Pas de fuite de données | LLM local (Ollama), `DISABLE_TELEMETRY=true`, aucun appel cloud. |
| Surface minimale | Services à risque retirés par défaut (voir §4). |
| Reproductibilité | Images **épinglées** (pas de `latest`), fichier compose unique auditable. |

## 2. Exposition réseau

- **nginx** publie `127.0.0.1:${ONYX_HOST_PORT}:80` → injoignable depuis le LAN/Internet.
- **Ollama** : **aucun** `ports:` → accessible uniquement via le DNS interne
  `ollama:11434`. Le LLM n'est donc jamais exposé hors de la stack.
- Postgres, OpenSearch, MinIO, Redis : **aucun port hôte**. Communication sur le
  réseau Docker privé `onix-net` exclusivement. Redis est de plus
  protégé par mot de passe (`--requirepass`, défense en profondeur).

> Vérifier : `docker compose ps` ne doit montrer un mapping de port que pour `nginx`,
> et préfixé par `127.0.0.1`.

## 3. Authentification

- `AUTH_TYPE=basic` : comptes email/mot de passe. **Le premier compte créé est admin.**
  - ⚠ **Prise de contrôle d'instance vierge** : avec `REQUIRE_EMAIL_VERIFICATION=false`
    (défaut local, pas de SMTP), tant qu'aucun compte n'existe, **le premier
    visiteur qui s'inscrit devient administrateur**. Mesure : **créer le compte
    admin IMMÉDIATEMENT après `make up`** (cf. README / RUNBOOK §2), avant que
    quiconque d'autre n'accède à l'URL. En localhost strict, la fenêtre de risque
    est limitée à la machine ; **dès une exposition réseau, voir §6** (vérif.
    d'email + domaines autorisés obligatoires).
- `USER_DIRECTORY_ADMIN_ONLY=true` : un non-admin ne peut pas énumérer les comptes.
- `SESSION_EXPIRE_TIME_SECONDS=86400` : ré-authentification quotidienne.
- En accès strictement localhost, la vérification d'email est désactivée (pas de
  SMTP) ; la surface est limitée à la machine. Pour l'activer, voir §6.

## 4. Réduction de la surface d'attaque

Retirés du compose par rapport à la distribution Onyx standard :

| Service | Raison |
|---|---|
| `code-interpreter` | Montait `/var/run/docker.sock` → équivaut à un accès **root sur l'hôte**. Inacceptable par défaut. |
| `certbot` | Inutile en localhost (pas de TLS public). Réintroduit pour un déploiement exposé (§6). |
| `mcp_server` | Surface d'API supplémentaire non requise. |
| 2ᵉ `model_server` (indexing) | Mutualisé avec l'inference server (RAM) ; réactivable pour la montée en charge. |

## 5. Secrets

Générés par `scripts/gen-secrets.sh` (idempotent) :
`SECRET`, `USER_AUTH_SECRET`, `POSTGRES_PASSWORD`, `DB_READONLY_PASSWORD`,
`OPENSEARCH_ADMIN_PASSWORD` (complexité garantie), `MINIO_ROOT_USER/PASSWORD`,
`S3_AWS_ACCESS_KEY_ID/SECRET`, **`REDIS_PASSWORD`** (Redis lancé avec
`--requirepass` ; honoré par Onyx via `REDIS_PASSWORD`).

- `.env` est **gitignoré** (`.gitignore` racine) et passé en `chmod 600`.
- **Rotation** : modifier la valeur dans `.env` puis `make up` (certains secrets,
  ex. `OPENSEARCH_ADMIN_PASSWORD`, impliquent une réinitialisation du volume —
  voir RUNBOOK). Conservez les secrets dans un coffre (Key Vault, gestionnaire).
- Le scan `gitleaks` du repo (pre-commit) protège contre un commit accidentel.

## 6. Durcissement pour un déploiement EXPOSÉ (au-delà du localhost)

Si vous devez ouvrir l'accès (serveur partagé) :

1. **TLS obligatoire** — placez un reverse proxy TLS devant nginx (Caddy/Traefik/
   nginx+Let's Encrypt) ou réintroduisez `certbot`. Ne publiez jamais en clair.
2. **Bind** — remplacez `127.0.0.1:` par l'IP voulue, derrière un pare-feu.
3. **SSO** — passez `AUTH_TYPE=oidc` et reliez votre **Entra ID** (SSO d'entreprise) :
   `OPENID_CONFIG_URL`, `OIDC_PKCE_ENABLED=true`, et restreignez avec
   `VALID_EMAIL_DOMAINS=votre-domaine.com`.
4. **Vérification d'email** (`basic`) — `REQUIRE_EMAIL_VERIFICATION=true` + `SMTP_*`,
   et `VALID_EMAIL_DOMAINS=…` pour fermer la porte à la prise de contrôle (§3).
5. **Redis** — déjà protégé par `--requirepass ${REDIS_PASSWORD}` (généré par
   `gen-secrets.sh`) ; vérifiez simplement que `REDIS_PASSWORD` est bien défini.
6. **Mises à jour** — suivez le changelog Onyx et relevez `IMAGE_TAG` régulièrement.

## 7. Checklist d'acceptation

- [ ] `make verify` : 0 échec.
- [ ] `docker compose ps` : seul `nginx` publie un port, en `127.0.0.1`.
- [ ] `.env` présent, `chmod 600`, secrets non vides (dont `REDIS_PASSWORD`), **non** versionné.
- [ ] Aucun mot de passe par défaut (`password`, `minioadmin`, `StrongPassword123!`).
- [ ] `DISABLE_TELEMETRY=true`.
- [ ] **Compte admin créé immédiatement après `make up`** (1er compte = admin), mot de passe fort.
- [ ] Sauvegarde testée (`make backup` puis `make restore`).
- [ ] (Si exposé) TLS + SSO + pare-feu en place (§6).
