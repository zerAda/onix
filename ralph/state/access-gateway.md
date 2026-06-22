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
| 2 | 2026-06-22 | M3 | **Fix sécurité (code)** : ACL **Fabric** câblée au filtre de citations. Nouveau `app/fabric_doc_acl.py` (`FabricDocACL` `DocACL` + `build_fabric_acl`, deny-by-default, gold-only) ; câblage `_build_doc_acl` (`main.py:129-158`) ; settings `GATEWAY_DOC_ACL_FABRIC_ENABLED`/`_MAPPING_PATH` (`config.py`). Test TDD `test_fabric_doc_acl.py` (fuite AVANT, absente APRÈS). | 333 passed (`py -m pytest tests -q`) ; bandit 0 | _voir commits_ |
| 3 | 2026-06-22 | #12 | **Stopgap RAG non-agentique (CPU)** : `onyx_proxy.force_internal_search()` injecte `forced_tool_id`+`allowed_tool_ids`=outil `internal_search` sur le payload chat relayé (couvre stream + non-stream via `safe_payload`) ⇒ Onyx exécute la recherche (`tool_choice=REQUIRED`) ⇒ réponse **sourcée+citée** même avec un modèle CPU faible. Réglages `GATEWAY_FORCE_INTERNAL_SEARCH` (défaut ON) + `GATEWAY_FORCE_SEARCH_TOOL_ID` (1). **Prouvé live** gemma3:12b (RUNTIME-EVIDENCE #12). | gateway **343 passed** (+4 `test_onyx_proxy`) ; bandit 0 | _voir commits_ |
| 3 | 2026-06-22 | API-compat | **Defense-in-depth RBAC** : `enforce_document_sets` pose le périmètre Document Set sur `retrieval_options` ET `internal_search_filters` (SendMessageRequest 4.1.x) — additif/strictement resserrant. Réponse au finding « la gateway force via l'ancien schéma » (RUNTIME-EVIDENCE #12 ; honoring 4.1.1 à confirmer live). | gateway **345 passed** (+2) ; bandit 0 | _voir commits_ |
| 4 | 2026-06-22 | #12-unwrap | **Déballage défensif** `unwrap_wrapped_answer` : gemma3 enveloppe parfois sa réponse en `{"result":"..."}` ; on l'extrait sur le chemin réponse (`extract_answer`) AVANT garde-fous/ACL, UNIQUEMENT si objet JSON avec `result` str (sinon inchangé → citations/grounding préservés). | gateway **350 passed** (+5) ; bandit 0 | _voir commits_ |

## Notes itération 2 — M3 (ACL Fabric → citations)
- **Vuln réelle confirmée** : `fabric_acl.py` (`can_principal_read`/`authorized_items`) existait et était testé, MAIS **jamais branché** comme source `DocACL` du filtre. `_build_doc_acl` (`main.py`) ne câblait que `StaticDocACL` + `GraphDocACL`. Un doc Fabric hors-périmètre fuitait en citation.
- **Fix fail-closed** : adaptateur synchrone `FabricDocACL` (même pattern que `GraphDocACL`) ; pré-résolution build-time (gold gate + roleAssignments → `_Entry`). Doc non mappé / hors gold / roleAssignments illisibles ⇒ **exclu** (deny-by-default). Fabric non configuré ⇒ ACL vide, **0 appel réseau**.
- **Opt-in** : `GATEWAY_DOC_ACL_FABRIC_ENABLED=false` par défaut (cohérent avec la source Graph, opt-in). Le filtre de citations (`doc_acl.filter_citations`) est **inchangé** : il consomme l'ACL OR-mergée via `CompositeDocACL`, donc le streaming bénéficie aussi du fix (même `acl`).
- **Limite assumée (honnêteté)** : filtre de SORTIE, pas de récupération — identique à `doc_acl`/`graph_acl` (cf. docs/RBAC.md §4.4). Le zéro-fuite strict à la recherche reste Fabric/Onyx EE.

## Notes itération 1
- **Aucun changement de comportement de code** ce tour-ci (réconciliation doc-truth pure, règle n°1).
- `docs/OBSERVABILITY.md` **non touché** (autre scope) — la référence ligne 202 de STREAMING.md vers OBSERVABILITY.md est laissée intacte.
- Reste P2 ouvert (hors périmètre de ce tour) : CODE-DOC-02 (compteur `cache_bypassed{streaming}` mort), CODE-DOC-03/04/05 (endpoints `/v1/feedback`, `/v1/authorized-document-sets`, nuance 503 `/metrics`) — touchent OBSERVABILITY.md / autres docs.
- Écart **majeur** restant (❌, 1) : affirmation de sécurité tier sémantique — non traité ce tour (nécessiterait analyse code/tests dédiée).

## Questions bloquantes
- (aucune)

## Critères de sortie A1–A7
- [ ] A1 (P1/P2 doc-truth réconciliés cette itér. ; reste ❌ tier sémantique + 🔇 endpoints) - [x] A2 (267 passed) - [ ] A3 - [ ] A4 - [ ] A5 - [ ] A6 - [ ] A7(n/a)
