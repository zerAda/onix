<!-- Pour clore le scope, écrire `RALPH_DONE` comme TOUTE PREMIÈRE ligne. -->
# État Ralph — access-gateway

## Backlog (source : docs/audit-reality/access-gateway.md)
| ID | Prio | Écart | Axe | Statut |
|---|---|---|---|---|
| G1 | P1 | `explicit_admin_bypass` inerte (`main.py:405` sans `is_admin`) — honnêteté | A1 | ✅ doc (itér. 1) |
| G2 | P1 | Contradiction fail-loud (CACHE.md §4) vs fail-safe silencieux (`main.py:164-168`) | A1/A3 | ✅ doc (itér. 1) |
| G3 | P1 | `_READ_ROLES` partiel (`graph_acl.py:70`) → faux refus possible | A1/A5 | ✅ doc (itér. 1) |
| G4 | P2 | Compteur « 52 tests » → 267 réels (DECISION_RBAC.md §6) | A1 | ✅ (itér. 1) |
| G5 | P2 | « Streaming SSE » trompeur → NDJSON réel | A1 | ✅ (itér. 1) |

## Journal
| Itér. | Date | Item | Correctif | Gates | SHA |
|---|---|---|---|---|---|
| 1 | 2026-06-18 | G5 | STREAMING.md titre « SSE »→« NDJSON » + note transport (preuve `main.py:391`) ; CACHE.md L92/L303 « SSE »→NDJSON | 267 passed | _voir commits_ |
| 1 | 2026-06-18 | G1 | CACHE.md §3 note « État de câblage réel » + §5/§13.6 : `explicit_admin_bypass` inerte (non câblé, `main.py:405` sans `is_admin`) ; **code inchangé** | 267 passed | _voir commits_ |
| 1 | 2026-06-18 | G2 | CACHE.md §4 note « Comportement réel au démarrage » + §7 : dégradation gracieuse (cache OFF + log CRITICAL), pas d'arrêt ; **code inchangé** | 267 passed | _voir commits_ |
| 1 | 2026-06-18 | G3 | RBAC.md §4.3 bis : `_READ_ROLES` liste blanche finie → faux refus possible (custom/localisé), fail-closed, extension par tenant ; **code inchangé** | 267 passed | _voir commits_ |
| 1 | 2026-06-18 | G4 | DECISION_RBAC.md §6/§8 : « 52 tests »→**267** (comptage `pytest --collect-only` vérifié) | 267 passed | _voir commits_ |
| 2 | 2026-06-22 | M7 | **Anti-spoof X-OIDC-Claims** (vuln : usurpation + bypass RBAC total via claims forgés en accès direct). `identity._require_proxy_proof` exige `X-OIDC-Proxy-Secret` == `GATEWAY_PROXY_SHARED_SECRET` (`hmac.compare_digest`) AVANT tout claim — fail-closed (secret absent → refus, sauf override DEV `GATEWAY_ALLOW_UNAUTHENTICATED_HEADER`). Câblé sur les 4 call-sites `main.py` (header `X-OIDC-Proxy-Secret`). Proxy : `nginx.prod.conf` injecte le secret (envsubst, `NGINX_ENVSUBST_FILTER`) + strip `X-OIDC-*`, `Caddyfile` strip `-X-OIDC-Proxy-Secret` au bord. Env : `env.prod.template` + `docker-compose.prod.yml` + `gen-secrets.sh`. TDD : tests échouent AVANT (signature/garde absentes), passent APRÈS. | identity+failclosed **18 passed** ; suite gateway **333 passed** ; bandit **0** | _voir commits_ |

## Notes itération 1
- **Aucun changement de comportement de code** ce tour-ci (réconciliation doc-truth pure, règle n°1).
- `docs/OBSERVABILITY.md` **non touché** (autre scope) — la référence ligne 202 de STREAMING.md vers OBSERVABILITY.md est laissée intacte.
- Reste P2 ouvert (hors périmètre de ce tour) : CODE-DOC-02 (compteur `cache_bypassed{streaming}` mort), CODE-DOC-03/04/05 (endpoints `/v1/feedback`, `/v1/authorized-document-sets`, nuance 503 `/metrics`) — touchent OBSERVABILITY.md / autres docs.
- Écart **majeur** restant (❌, 1) : affirmation de sécurité tier sémantique — non traité ce tour (nécessiterait analyse code/tests dédiée).

## Questions bloquantes
- (aucune)

## Critères de sortie A1–A7
- [ ] A1 (P1/P2 doc-truth réconciliés cette itér. ; reste ❌ tier sémantique + 🔇 endpoints) - [x] A2 (267 passed) - [ ] A3 - [ ] A4 - [ ] A5 - [ ] A6 - [ ] A7(n/a)
