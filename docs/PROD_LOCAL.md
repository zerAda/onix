# Production sur machine unique — onix (prod-local)

> Runbook de **mise en production RÉELLE sur UNE SEULE machine perso** (64 Go,
> bon CPU), pour **1-2 testeurs**, de façon **FIABLE** : survit aux redémarrages,
> démarrage ordonné, sauvegardes, accès TLS **privé**. **Sans** domaine public ni
> OIDC. C'est le palier entre le **POC local** ([`POC_LOCAL.md`](POC_LOCAL.md)) et
> la **prod d'entreprise exposée** ([`DEPLOY_PROD.md`](DEPLOY_PROD.md)).

Ce runbook **ne duplique pas** le POC ; il ajoute la couche « exploitation
durable ». Pour l'**installation initiale** (matériel, Docker, premier
démarrage) et la **connexion SharePoint** (indispensable), suivez d'abord
[`POC_LOCAL.md`](POC_LOCAL.md) — notamment **§3** pour SharePoint.

## 1. Quel palier choisir ? (POC vs prod-local vs prod-domaine)

| Critère | POC local | **prod-local (ce doc)** | prod-domaine (entreprise) |
|---|---|---|---|
| Fichiers compose | `docker-compose.yml` | base **+** `docker-compose.prod-local.yml` | base **+** `deploy/prod/docker-compose.prod.yml` |
| Auth | basic, admin créé | basic durci (admin + vérif. e-mail) | **OIDC Entra FORCÉ** |
| TLS / accès testeurs | localhost (+ LAN) | **Tailscale Serve** (TLS privé) / LAN | Caddy + Let's Encrypt (domaine **public**) |
| Domaine public requis | non | **non** | **oui** |
| Healthchecks | partiels | **complets** (db, opensearch, cache, api, web, model) | complets + préflight de sûreté |
| Démarrage ordonné | best-effort | **`depends_on … service_healthy`** | idem + garde-fou OIDC/TLS |
| Redémarrage | `unless-stopped` | **`always` + systemd au boot** | `always` |
| RBAC par-document | non | optionnel (passerelle) | **passerelle activée** |
| Cible | démo / essai | **1-2 testeurs, durable, 1 PC** | équipe, exposition Internet |

Frontière nette : prod-local **ne rend rien public**. nginx reste lié à
`127.0.0.1` (hérité de la base — l'overlay n'y touche pas) ; l'accès distant
passe par un **tunnel TLS privé** (Tailscale) ou un **LAN de confiance**.

## 2. Démarrer en prod-local

L'overlay `docker-compose.prod-local.yml` s'**empile** sur la base. On part de
l'installation POC (matériel réglé, secrets générés), puis on bascule.

```bash
cd onix
make tune          # règle .env au matériel (64 Go → modèle 14b) — cf. POC §2
make secrets       # secrets forts dans .env (chmod 600) — OBLIGATOIRE avant up
make up            # 1er démarrage + pré-tirage des modèles (peut être long)
```

Puis **bascule en overlay prod-local** (healthchecks + démarrage ordonné +
`restart: always`). Le plus simple — **cibles Makefile dédiées** :

```bash
make preflight-local   # prérequis : daemon Docker, vm.max_map_count, RAM, disque, ports, secrets
make up-local-prod     # = docker compose -f docker-compose.yml -f docker-compose.prod-local.yml up -d
make verify            # santé de bout en bout (services, câblage Ollama interne, génération)
```

Équivalent explicite (sans Make) :

```bash
docker compose -f docker-compose.yml -f docker-compose.prod-local.yml up -d
```

Validez la composition **sans rien démarrer** :

```bash
docker compose -f docker-compose.yml -f docker-compose.prod-local.yml config -q
```

> **Healthchecks éprouvés (pas devinés).** Le socle de données a été démarré
> RÉELLEMENT et observé `healthy` : Postgres (`pg_isready`), Redis (`redis-cli
> ping`), MinIO (`mc ready`), **OpenSearch** (`curl -ksf -u admin:… https://…:9200/_cluster/health`,
> statut `green`). Les sondes applicatives sont vérifiées contre le code/Dockerfiles
> Onyx v4.1.1 : api_server `GET /health` (8080, route publique ; `curl` présent dans
> l'image backend), model-server `GET /api/health` (9000, `APIRouter(prefix="/api")`,
> python stdlib), web `:3000/` (`wget` busybox de node-alpine ; le serveur force
> `HOSTNAME=0.0.0.0` → la sonde loopback fonctionne).
> Chaîne de démarrage fiable : **socle données sain → api saine → web saine → nginx**.
>
> *Note de fiabilité :* la sonde OpenSearch lit `OPENSEARCH_INITIAL_ADMIN_PASSWORD`
> (la variable réellement présente dans le conteneur) — et non `OPENSEARCH_ADMIN_PASSWORD`,
> qui y est absente et donnerait un mot de passe vide (401 → jamais `healthy`).

Première connexion : ouvrez `http://localhost:3000` (ou l'URL Tailscale, §5) et
**créez le compte admin IMMÉDIATEMENT** (§6). Réglez l'assistant LLM : Provider
**Ollama**, URL `http://ollama:11434`, modèle recommandé par `make tune`.

## 3. Survie au redémarrage (le point clé d'une « vraie » prod)

Deux mécanismes complémentaires, à activer **tous les deux** :

1. **`restart: always`** (posé par l'overlay sur tous les services) : Docker
   relance chaque conteneur après un crash **et** au démarrage du démon.
2. **Démon Docker au boot + unit systemd onix** : garantit que Docker démarre au
   boot et que la composition complète (base + overlay) est (re)lancée.

```bash
sudo systemctl enable docker        # Docker au démarrage de la machine
# Unit fournie : deploy/local-prod/onix.service (adapter WorkingDirectory)
sudo cp deploy/local-prod/onix.service /etc/systemd/system/onix.service
sudo systemctl daemon-reload
sudo systemctl enable --now onix    # active + démarre
systemctl status onix               # « active (exited) » attendu
```

Détails install / désinstall : [`../deploy/local-prod/README.md`](../deploy/local-prod/README.md).
La pré-condition Linux **`vm.max_map_count >= 262144`** (OpenSearch) doit être
**persistée** (`/etc/sysctl.d/99-onyx.conf`) pour survivre au reboot — cf.
[`POC_LOCAL.md`](POC_LOCAL.md) §1 et [`RUNBOOK.md`](RUNBOOK.md) §6.

## 4. Sauvegardes (et test de restauration)

`scripts/backup.sh` arrête brièvement la stack (cohérence), archive `db_volume`,
`opensearch-data`, `minio_data`, `file-system` dans `backups/<horodatage>/`, puis
redémarre. Les modèles Ollama ne sont **pas** sauvegardés (re-tirables via
`make models`).

```bash
make backup        # → backups/AAAAMMJJ-HHMMSS/
```

**Cron quotidien** (exemple, sauvegarde à 02h30 ; adapter le chemin du dépôt) :

```cron
# crontab -e
30 2 * * * cd /home/user/onix && /usr/bin/make backup >> /home/user/onix/backups/cron.log 2>&1
```

> La sauvegarde **arrête** brièvement la stack : planifiez-la hors des heures de
> test. Pensez à **purger** les anciennes archives (espace disque) et à copier
> `backups/` hors machine (clé/NAS) — une sauvegarde sur le même disque ne
> protège pas d'une panne disque.

**Testez la restauration** régulièrement (une sauvegarde non testée n'en est pas
une) — `scripts/restore.sh` ÉCRASE les volumes après confirmation :

```bash
make restore DIR=backups/AAAAMMJJ-HHMMSS    # demande « oui » avant d'écraser
make verify                                  # contrôle la stack après restauration
```

## 5. Accès des testeurs en TLS privé

nginx reste lié à `127.0.0.1` : rien n'est exposé sur Internet. Deux options.

- **Tailscale Serve (RECOMMANDÉ — privé + TLS, souverain, zéro modif)** :
  ```bash
  # installez Tailscale, connectez-vous, puis :
  tailscale serve 3000
  ```
  Les testeurs (ajoutés à votre **tailnet**) ouvrent
  `https://<machine>.<tailnet>.ts.net`. Chiffré de bout en bout, aucune
  ouverture Internet, aucun port hôte public.

- **LAN de confiance** (override existant `docker-compose.lan.yml`) :
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.prod-local.yml \
    -f docker-compose.lan.yml up -d
  ```
  Testeurs : `http://<IP-LAN-de-la-machine>:3000`. **Uniquement** un réseau de
  confiance ; l'empilement conserve les healthchecks + `restart: always` de
  l'overlay prod-local. Trafic LAN **non chiffré** → préférez Tailscale.

## 6. Durcissement de l'authentification (basic, sans OIDC)

prod-local reste en `AUTH_TYPE=basic` (pas de domaine public/OIDC). On durcit
néanmoins l'accès :

1. **Créer le compte admin EN PREMIER, immédiatement** : tant qu'aucun compte
   n'existe, **le premier inscrit prend l'instance (admin)**. Ne pas différer.
2. **`REQUIRE_EMAIL_VERIFICATION=true`** dans `.env` dès que l'accès dépasse
   `localhost` (Tailscale/LAN).
3. **`USER_DIRECTORY_ADMIN_ONLY=true`** (déjà le défaut de la base) : énumération
   des comptes réservée aux admins.
4. **`VALID_EMAIL_DOMAINS`** : restreindre aux domaines e-mail attendus.
5. **Mots de passe forts** pour tous les comptes ; secrets `.env` en `chmod 600`
   (posé par `gen-secrets.sh`) — jamais commités.

Voir [`SECURITY.md`](SECURITY.md) §6 pour le détail de ces variables.

> Étape de durcissement SUIVANTE (OPTIONNELLE, **non imposée** en prod-local) :
> la passerelle onix (RBAC / ACL par-document) cloisonne l'accès aux documents
> par utilisateur/groupe. Elle n'est activée que dans la surcouche prod
> d'entreprise. Pour l'évaluer : [`RBAC.md`](RBAC.md).

## 7. Observabilité

Métriques + dashboards (Prometheus/Grafana) via la stack monitoring dédiée :

```bash
make monitor-up      # démarre l'observabilité (Grafana sur le port configuré)
```

Détails, alertes et tableaux de bord : [`OBSERVABILITY.md`](OBSERVABILITY.md).
Au quotidien : `make ps`, `make stats`, `make logs` (cf. [`RUNBOOK.md`](RUNBOOK.md) §1).

## 8. Préflight & vérification

Avant/après bascule, contrôlez l'environnement :

- **`scripts/preflight-local.sh`** (garde-fou prod-local — pré-requis hôte,
  secrets présents, `vm.max_map_count`, etc.) : à lancer avant `up`.
- **`make verify`** (`scripts/verify.sh`) : santé des services + câblage
  Onyx↔Ollama + génération réelle. Doit finir sur « Stack saine ».

## 9. SharePoint (indispensable)

La connexion SharePoint (app Entra app-only, Microsoft Graph, branchement du
connecteur, indexation) est **détaillée dans [`POC_LOCAL.md`](POC_LOCAL.md) §3**.
Elle est identique en prod-local — **on ne la duplique pas ici**.

## 10. Limites & passage à l'échelle supérieure

- **1 machine = pas de HA** : un arrêt machine = service indisponible ; la
  vitesse dépend du CPU (le **cache** amortit les répétitions) ; les testeurs
  dépendent de votre connexion (Tailscale) ou du LAN.
- Quand la validation est faite, on **déplace sans réécriture** :
  - **Prod d'entreprise exposée** (domaine + TLS + OIDC) : [`DEPLOY_PROD.md`](DEPLOY_PROD.md).
  - **VM Azure** : [`DEPLOY_AZURE.md`](DEPLOY_AZURE.md).
  - **AKS / haute dispo** : [`HA_SCALING.md`](HA_SCALING.md).
  Même image, même connecteur SharePoint, même couche onix — déjà prêts.
