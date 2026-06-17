# access-gateway — proxy RBAC identity-aware (cloisonnement FOSS par groupe/Document Set)

Composant **nouveau** et **autonome** d'onix. Il s'intercale **devant** la
recherche Onyx pour réintroduire, en **édition gratuite (FOSS)**, un cloisonnement
**par utilisateur** que l'index partagé d'Onyx FOSS n'offre pas nativement (le
trimming par document est réservé à l'Enterprise Edition).

> Lire d'abord la stratégie et ses limites : [`../docs/RBAC.md`](../docs/RBAC.md).

## Ce qu'il fait
1. Récupère l'**identité + les groupes Entra** de l'appelant :
   - depuis le **claim OIDC `groups`** (header `X-OIDC-Claims` injecté par le
     reverse-proxy/IdP, claims **déjà vérifiés**), OU
   - depuis **Microsoft Graph** `transitiveMemberOf` (app-only) si le claim est
     absent ou tronqué (**overage**).
2. Traduit groupe → **Document Set(s) Onyx** autorisés (mapping JSON,
   **deny-by-default**).
3. **Force** `retrieval_options.filters.document_set` au périmètre autorisé et
   **relaie** la requête à Onyx (`/chat/send-message`). Un client ne peut pas
   élargir son périmètre.

**Granularité : groupe / Document Set — pas par document** (≠ OBO d'AC360 / EE).

## Endpoints
| Méthode | Chemin | Rôle |
|---|---|---|
| GET | `/health` | Sonde (pas d'auth). |
| GET | `/v1/authorized-document-sets` | Introspection : groupes + Document Sets autorisés de l'appelant. |
| POST | `/v1/chat/send-message` | Proxy filtré vers Onyx (Document Set forcé). |

## Configuration (variables d'env)
Copier [`.env.template`](.env.template) → `.env` (gitignoré, `chmod 600`). Points
clés : `GATEWAY_GROUP_SOURCE` (`claims` | `graph` | `auto` — **`auto` recommandé**),
`GATEWAY_ONYX_BASE_URL`, `GATEWAY_MAPPING_PATH`, et les `GATEWAY_GRAPH_*` si Graph
est utilisé. Permission Graph **minimale : `GroupMember.Read.All`**.

Mapping : voir [`config/group_map.example.json`](config/group_map.example.json).

## Lancer
```bash
# Local (dev)
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
GATEWAY_MAPPING_PATH=config/group_map.example.json \
  uvicorn app.main:app --port 8200

# Conteneur (prod) : image non-root, healthcheck interne, port 8200
docker build -t onix-access-gateway .
docker run --rm -p 127.0.0.1:8200:8200 \
  --env-file .env \
  -v "$PWD/config/group_map.example.json:/config/group_map.json:ro" \
  onix-access-gateway
```
> En production, déployer sur le réseau `onix-net` **sans port hôte**, derrière le
> reverse-proxy TLS qui assure le SSO OIDC et injecte `X-OIDC-Claims`. **L'UI/API
> Onyx native doit rester interne** (sinon le filtre est contournable).

## Durcissement & déploiement (la passerelle = SEUL point d'entrée)

La sécurité du cloisonnement FOSS **repose entièrement** sur le fait que la
passerelle est **interposée** : si un utilisateur peut joindre l'UI/API Onyx
native, le filtre Document Set est **contournable**. Règles de déploiement
(détaillées dans [`../docs/DECISION_RBAC.md`](../docs/DECISION_RBAC.md) §6) :

1. **N'exposer QUE la passerelle.** Onyx (`web_server`/`api_server`) reste sur le
   réseau interne `onix-net` **sans port hôte** ; aucune route publique ne pointe
   vers lui. Le reverse-proxy TLS public route uniquement vers `access-gateway`.
2. **SSO en amont obligatoire.** Le reverse-proxy/IdP authentifie (OIDC) et injecte
   `X-OIDC-Claims` (claims **déjà vérifiés**). Ne jamais exposer la passerelle sans
   cette couche : elle **fait confiance** à cet en-tête.
3. **Fail-closed.** Identité illisible → **401**. Groupes non résolvables (overage
   + repli Graph indisponible/en erreur) → **502**, jamais un passage « ouvert ».
   Aucun groupe mappé → **403** (`GATEWAY_DENY_IF_NO_MATCH=true`, défaut).
4. **Journal des décisions d'accès (haché).** Chaque allow/deny est journalisé
   (`onix.gateway.audit`, JSON) avec une **identité pseudonymisée** (HMAC-SHA256,
   sel `GATEWAY_AUDIT_SALT`) — **jamais** l'UPN/oid en clair, **jamais** le message.
   Voir [`app/audit.py`](app/audit.py).

## Tests
```bash
pip install -r requirements-dev.txt
pytest tests -q        # 52 tests ; Graph et Onyx amont moqués, aucun réseau réel
```
Couvre notamment (durcissement) : utilisateur **sans groupe** → deny ; **multi-
groupes** → union des Document Sets autorisés **uniquement** ; **fail-closed** si
les groupes sont irrésolvables ; **non-fuite** d'identité/contenu dans l'audit.

## Architecture du code
| Module | Rôle |
|---|---|
| `app/config.py` | Réglages 12-factor (aucun secret en dur). |
| `app/identity.py` | Résolution identité + groupes (claims / graph / auto, overage, cache TTL). |
| `app/graph_client.py` | Client Graph `transitiveMemberOf` (token client-credentials, pagination). |
| `app/mapping.py` | Mapping groupe → Document Set (deny-by-default). |
| `app/onyx_proxy.py` | Forçage du filtre Document Set + anti-élargissement. |
| `app/audit.py` | Journal des décisions d'accès, identité **hachée** (HMAC). |
| `app/main.py` | App FastAPI (endpoints, relais, fail-closed, audit). |

## Limites assumées
- **Pas de trimming par document** (granularité Document Set). Droits hétérogènes
  intra-périmètre ou propagation automatique des ACL SharePoint ⇒ **Enterprise
  Edition** (permission sync). Détails : [`../docs/RBAC.md`](../docs/RBAC.md) §5.
- La passerelle **fait confiance** aux claims vérifiés en amont : ne pas l'exposer
  sans la couche SSO devant elle.
