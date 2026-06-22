# Scope `access-gateway` — dossier agent

> **Mission** : passerelle FastAPI placée **devant** Onyx qui apporte, en FOSS, ce
> qu'Onyx réserve à l'EE — le **cloisonnement par utilisateur** (groupe Entra →
> Document Set), l'**ACL par-document** (filtre de sortie), un **cache RBAC-safe**,
> le **streaming** et l'exposition `/metrics`.
> **Sous-agent** : sécurité plateforme (FastAPI/Redis). **État** :
> [`../../ralph/state/access-gateway.md`](../../ralph/state/access-gateway.md).
>
> 👤 **Owner** : Sécurité plateforme (FastAPI/Redis) · 🗓️ **Dernière revue** : 2026-06-22 · 🔁 **Cadence de revue** : 120 j (cf. [registre](scopes.json)).

Routeur : [`README.md`](README.md) · Projet : [`../../AGENTS.md`](../../AGENTS.md).

## 1. Mission & frontière FOSS/EE

| | |
|---|---|
| **Apporte (FOSS)** | cloisonnement groupe→Document Set forcé à la requête ; ACL par-doc OR-mergée (statique + Graph SharePoint) appliquée **en sortie** ; cache déterministe par périmètre ; streaming NDJSON ; `/metrics`. |
| **Reste EE/Onyx** | la *permission-sync* SharePoint native (propagation des ACL à l'index) = **EE + certificat**. En FOSS, Onyx indexe sans ACL → **la passerelle cloisonne** (filtre de sortie, limite assumée : le LLM a pu *voir* le contenu pendant la génération — cf. [`../RBAC.md`](../RBAC.md)). |

## 2. Carte du code — [`../../access-gateway/`](../../access-gateway/)

| Fichier | Rôle |
|---|---|
| [`app/main.py`](../../access-gateway/app/main.py) | **Point d'entrée** FastAPI. Endpoints : `GET /health`, `GET /metrics`, `GET /v1/authorized-document-sets`, `POST /v1/chat/send-message` (force le filtre Document Set + relais Onyx + post-filtre), `POST /v1/feedback`. |
| [`app/config.py`](../../access-gateway/app/config.py) | `Settings` 12-factor (env-only). Tout réglage : Onyx amont, source des groupes, Graph, Fabric/gold, cache, doc_acl, streaming. |
| [`app/identity.py`](../../access-gateway/app/identity.py) | Extraction de l'identité vérifiée depuis `X-OIDC-Claims` (oid/upn/groups), cache TTL. **Anti-spoof (M7)** : `_require_proxy_proof` exige une preuve de transit proxy (`X-OIDC-Proxy-Secret` == `GATEWAY_PROXY_SHARED_SECRET`, comparaison temps constant) AVANT de croire le moindre claim — fail-closed. |
| [`app/mapping.py`](../../access-gateway/app/mapping.py) | Mapping **groupe Entra → Document Set** (fichier JSON, `deny_if_no_match`). |
| [`app/graph_client.py`](../../access-gateway/app/graph_client.py) | Graph app-only : `transitiveMemberOf` (groupes Entra d'un user). |
| [`app/graph_acl.py`](../../access-gateway/app/graph_acl.py) | ACL par-doc **dérivée de SharePoint** (`fetch_item_principals` : users/groups/siteGroups en lecture), `GraphDocACL` (TTL). |
| [`app/doc_acl.py`](../../access-gateway/app/doc_acl.py) | ACL statique (`doc_id → {users,groups}`) + composite (OR-merge avec Graph **et Fabric**). Politique par défaut deny. |
| [`app/fabric_client.py`](../../access-gateway/app/fabric_client.py) | Client **Fabric / OneLake / Power BI** (GET-only, 3 audiences, `is_gold_path` fail-closed, auth `az` injectable). |
| [`app/fabric_acl.py`](../../access-gateway/app/fabric_acl.py) | `can_principal_read` **fail-closed**, gold-only (roleAssignments ∪ principalAccess OneLake). |
| [`app/fabric_doc_acl.py`](../../access-gateway/app/fabric_doc_acl.py) | **[M3]** Adaptateur `FabricDocACL` (`DocACL`) : câble l'ACL Fabric au **filtre de citations**. `build_fabric_acl` pré-résout `{doc_id → _Entry}` (roleAssignments gold-only). OR-mergé via `CompositeDocACL`. Deny-by-default. |
| [`app/cache.py`](../../access-gateway/app/cache.py) | Cache **RBAC-safe** (clé HMAC incluant le périmètre trié) + tier sémantique opt-in + garde anti-divergence. |
| [`app/guardrail.py`](../../access-gateway/app/guardrail.py) | Post-filtre garde-fous sur la réponse de l'assistant. |
| [`app/streaming.py`](../../access-gateway/app/streaming.py) | Relais **NDJSON** (`application/x-ndjson`) token-par-token + garde DUR incrémental + override final. |
| [`app/onyx_proxy.py`](../../access-gateway/app/onyx_proxy.py) | Relais HTTP vers Onyx amont + `enforce_document_sets` (périmètre RBAC). **`force_internal_search` (#12)** : force l'outil `internal_search` d'Onyx (`forced_tool_id`+`allowed_tool_ids`) ⇒ RAG **non-agentique** sourcé même avec un modèle CPU faible (réglage `GATEWAY_FORCE_INTERNAL_SEARCH`, défaut ON ; prouvé live gemma3, cf. RUNTIME-EVIDENCE #12). |
| [`app/metrics.py`](../../access-gateway/app/metrics.py) | Compteurs Prometheus (`/metrics`). |
| [`app/audit.py`](../../access-gateway/app/audit.py) | Journal d'audit structuré des décisions d'accès (acteur haché). |
| [`tests/`](../../access-gateway/tests/) | Suite **offline** (httpx mocké) + harnais e2e LIVE (`tests/e2e/`). |

## 3. Commandes

```bash
make secrets-gateway                 # génère GATEWAY_CACHE_HMAC_SECRET (access-gateway/.env)
pytest access-gateway/tests          # suite offline (aucun réseau réel)
# e2e LIVE (vrai tenant) — cf. docs/E2E_ACCESS_LIVE.md
python access-gateway/tests/e2e/run_access_e2e.py
```

## 4. Tests & preuves

- **Offline** : `pytest access-gateway/tests` — RBAC accordé/refusé, filtre Document
  Set forcé, fail-closed (cache off sans secret, ACL deny par défaut), Fabric gold-only,
  régression GUID OneLake. Aucun appel réseau (httpx `MockTransport`).
- **LIVE** : `tests/e2e/run_access_e2e.py` (SharePoint A1–A3, Fabric B1–B5) — réutilise
  le code déployé ; SKIP propre sans creds (exit 2). Cf. [`../E2E_ACCESS_LIVE.md`](../E2E_ACCESS_LIVE.md).

## 5. Invariants & pièges (ne pas casser)

- **Cache ↔ ACL** : le cache **ne stocke QUE** le corps déterministe par périmètre ;
  l'**ACL par-doc est ré-appliquée PAR requête** (jamais mutualisée). **Ne pas inverser.**
- **Fail-closed** partout : identité absente → 401 ; aucun groupe mappé +
  `deny_if_no_match` → refus ; source ACL indisponible → on n'accorde pas.
- **Preuve de transit proxy (anti-spoof, M7)** : `X-OIDC-Claims` n'est JAMAIS cru
  verbatim. Le proxy de confiance (nginx interne) injecte `X-OIDC-Proxy-Secret` ;
  la passerelle le compare en temps constant à `GATEWAY_PROXY_SHARED_SECRET`. Secret
  configuré + preuve absente/fausse → 401. Secret **non** configuré → refus aussi,
  sauf override DEV `GATEWAY_ALLOW_UNAUTHENTICATED_HEADER=true` (jamais en prod).
  Le proxy doit **stripper** tout `X-OIDC-*` entrant client puis ré-injecter
  (cf. `deploy/prod/Caddyfile` + `nginx.prod.conf`).
- **Fabric = lecture seule, tables GOLD uniquement** (`is_gold_path`). Aucune méthode
  POST/PUT/DELETE ne doit exister dans `fabric_client.py`.
- **siteGroups ≠ groupes Entra** : `graph_acl` capte `siteGroup.id` (entiers SharePoint)
  mais la résolution user renvoie des **GUID Entra** → partager via **groupes Entra**
  (cf. [`../DECISION_RBAC.md`](../DECISION_RBAC.md)).
- **Zéro secret loggé** (jeton/claims jamais journalisés).

> 🔒 **Sécurité (scope)** : applique [`SECURITY.md`](../../SECURITY.md) + le scope gardien
> [`security-governance`](security-governance.md) ; **fail-closed**, zéro secret loggé ;
> gates `make bandit gitleaks pip-audit trivy` **verts** avant commit.

## 6. Observabilité

`GET /metrics` (Prometheus) + journal d'audit structuré (`app/audit.py`). Dashboard
[`../../monitoring/grafana/dashboards/onix-gateway.json`](../../monitoring/grafana/dashboards/onix-gateway.json).
Vue d'ensemble : [`../OBSERVABILITY.md`](../OBSERVABILITY.md).

## 7. Docs de fond

[`../RBAC.md`](../RBAC.md) · [`../DECISION_RBAC.md`](../DECISION_RBAC.md) ·
[`../CACHE.md`](../CACHE.md) · [`../STREAMING.md`](../STREAMING.md) ·
[`../connectors/SHAREPOINT.md`](../connectors/SHAREPOINT.md) ·
[`../connectors/FABRIC.md`](../connectors/FABRIC.md) ·
[`../E2E_ACCESS_LIVE.md`](../E2E_ACCESS_LIVE.md).

## 8. Audit & journal

Écarts doc↔code : [`../audit-reality/access-gateway.md`](../audit-reality/access-gateway.md).
Journal de boucle : [`../../ralph/state/access-gateway.md`](../../ralph/state/access-gateway.md).
Prompt de scope : [`../../ralph/scopes/access-gateway.md`](../../ralph/scopes/access-gateway.md).

## 9. Sous-agent

| | |
|---|---|
| Discipline | Sécurité plateforme (FastAPI/Redis) |
| Skills | `/security-review`, `/code-review`, `/verify`, `/simplify` |
| MCP | `Context7` (fastapi, starlette, redis-py) **avant** de coder une API ; `github` (CI) |
| Cibles de preuve | `pytest access-gateway/tests`, `/metrics`, invariant cache↔ACL |

## 10. Maintenir cette fiche

Touche au code `access-gateway/` ⇒ mets à jour §2 (carte du code), §3/§4 si une
commande/test change, et reporte la preuve `fichier:ligne` dans
[`../audit-reality/access-gateway.md`](../audit-reality/access-gateway.md) + le journal.
Vérifie les liens : `make docs-check`.
