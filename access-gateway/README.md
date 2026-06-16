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

## Tests
```bash
pip install -r requirements-dev.txt
pytest tests -q        # 38 tests ; Graph et Onyx amont moqués, aucun réseau réel
```

## Architecture du code
| Module | Rôle |
|---|---|
| `app/config.py` | Réglages 12-factor (aucun secret en dur). |
| `app/identity.py` | Résolution identité + groupes (claims / graph / auto, overage, cache TTL). |
| `app/graph_client.py` | Client Graph `transitiveMemberOf` (token client-credentials, pagination). |
| `app/mapping.py` | Mapping groupe → Document Set (deny-by-default). |
| `app/onyx_proxy.py` | Forçage du filtre Document Set + anti-élargissement. |
| `app/main.py` | App FastAPI (endpoints, relais). |

## Limites assumées
- **Pas de trimming par document** (granularité Document Set). Droits hétérogènes
  intra-périmètre ou propagation automatique des ACL SharePoint ⇒ **Enterprise
  Edition** (permission sync). Détails : [`../docs/RBAC.md`](../docs/RBAC.md) §5.
- La passerelle **fait confiance** aux claims vérifiés en amont : ne pas l'exposer
  sans la couche SSO devant elle.
