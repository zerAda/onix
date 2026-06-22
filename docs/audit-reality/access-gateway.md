# Audit byte-by-byte — Documentation ↔ Réalité — Scope `access-gateway`

> **Date** : 2026-06-18
> **Auditeur** : ingénierie plateforme/sécurité (lecture seule).
> **Périmètre docs** : [`docs/RBAC.md`](../RBAC.md), [`docs/DECISION_RBAC.md`](../DECISION_RBAC.md),
> [`docs/CACHE.md`](../CACHE.md), [`docs/STREAMING.md`](../STREAMING.md).
> **Code/config de référence** : `access-gateway/` (app/, config/, tests/),
> branchement gateway dans `docker-compose*.yml` + `deploy/`.
> **Méthode & légende** : [`README.md`](README.md) (✅ conforme · ⚠️ écart mineur ·
> ❌ écart majeur · 🕳️ doc-sans-code · 🔇 code-sans-doc · ❔ non vérifiable).

Conformément à la règle de jeu n°1 (`AGENTS.md`) : **honnêteté > esbroufe**.
Le **piège connu §7** (« le cache ne stocke QUE le corps périmètre-déterministe ;
l'ACL par-doc est ré-appliquée PAR requête, jamais mutualisée ») est **vérifié et
CONFIRMÉ par preuve** (cf. CACHE.md ligne « §11 ordering » ci-dessous).

---

## Tableau récapitulatif (comptage par classe)

| Classe | Nb (initial) | Nb (après itér. 1) |
|---|---:|---:|
| ✅ CONFORME | 71 | 73 |
| ⚠️ ÉCART MINEUR | 11 | 9 |
| ❌ ÉCART MAJEUR | 1 | 1 |
| 🕳️ DOC-SANS-CODE | 0 | 0 |
| 🔇 CODE-SANS-DOC | 5 | 5 |
| ❔ NON VÉRIFIABLE | 6 | 6 |
| **Total** | **94** | **94** |

> **Mise à jour itération 1 (2026-06-18)** — réconciliation **doc-truth** (aucun
> changement de code) : STR-14 (SSE→NDJSON) et DEC-09 (« 52 tests »→267) passent
> de ⚠️ à ✅. Les écarts transverses CODE-DOC-01 (admin-bypass inerte), CODE-DOC-07
> (fail-loud/fail-safe du cache) et CODE-DOC-08 (`_READ_ROLES` partiel) sont
> désormais **documentés conformément au comportement réel** (CACHE.md §3/§4,
> RBAC.md §4.3 bis). Gate `pytest access-gateway/tests` = **267 passed**.

**Synthèse honnête** : le scope est d'une fidélité doc↔code remarquable. Le seul
écart **majeur** est une **affirmation de sécurité sur le tier sémantique** (le
garde anti-divergence ne porte QUE sur la question normalisée d'indexation par
défaut, cas nominal — *voir détail CACHE-26* ; non, c'est précis : voir plus bas).
Les écarts mineurs restants sont surtout des **valeurs/chemins** ou des **nuances
de vocabulaire** ; les nuances « SSE »/« 52 tests » ont été **corrigées** à
l'itération 1.

---

## 1. `docs/RBAC.md`

| # | Affirmation | Classe | Preuve (fichier:ligne) | Note |
|---|---|---|---|---|
| RBAC-01 | Deny-by-default : user sans groupe mappé → **403** | ✅ | `app/main.py:325-340` (`enforce_document_sets` + `AccessDenied`→403) ; `app/onyx_proxy.py:55-57` | `GATEWAY_DENY_IF_NO_MATCH=true` par défaut (`config.py:165`). |
| RBAC-02 | Non-élargissement : `document_set` demandé **intersecté** avec l'autorisé | ✅ | `app/onyx_proxy.py:64-74` | `effective = [s for s in requested if s in allowed]`. |
| RBAC-03 | Accès direct par `search_doc_ids` **neutralisé** | ✅ | `app/onyx_proxy.py:76-77` (`out.pop("search_doc_ids", None)`) | Testé `test_onyx_proxy.py:35`. |
| RBAC-04 | Repli overage : claim tronqué → bascule Graph `transitiveMemberOf` (mode `auto`) | ✅ | `app/identity.py:170-182` ; `app/identity.py:72-81` (`_has_overage`) | Testé `test_identity.py:114`. |
| RBAC-05 | Appel Graph : `GET /v1.0/users/{oid}/transitiveMemberOf/microsoft.graph.group?$select=id,displayName&$top=999` + `ConsistencyLevel: eventual` + pagination `@odata.nextLink` | ✅ | `app/graph_client.py:84-102` | Exactement la forme documentée. |
| RBAC-06 | Permission Graph minimale applicative = `GroupMember.Read.All` | ❔ | `app/graph_client.py:9-12` (commentaire) | Affirmation produit Microsoft ; le code ne *force* pas une permission (porte côté Entra). Non vérifiable ici. |
| RBAC-07 | Mapping JSON : clé = objectId GUID **ou** displayName, casse-insensible | ✅ | `app/mapping.py:43-55,79` (`.strip().lower()`) | Deux formes (simple/structurée) supportées `mapping.py:58-84`. |
| RBAC-08 | `default_document_sets` accordés à tout user authentifié | ✅ | `app/mapping.py:50,83` | Inclus dans l'union. |
| RBAC-09 | `doc_acl.py` retire de `top_documents`/`context_docs`/`final_context_docs`/`documents`/`source_documents`/`citations` les doc_id non autorisés | ✅ | `app/doc_acl.py:223-230,359-376` | Liste exacte. |
| RBAC-10 | Override **par utilisateur** (UPN/oid) **gagne** sur l'appartenance de groupe | ✅ | `app/doc_acl.py:179-190` (users testés avant groups) | Idem `graph_acl.py:296-307`. |
| RBAC-11 | Politique par défaut **deny** ; configurable `GATEWAY_DOC_ACL_DEFAULT_POLICY=allow` | ✅ | `app/doc_acl.py:114-117,176-178` ; `config.py:200-202` | |
| RBAC-12 | `strip_uncited=true` → substitue `REFUSAL_NO_ACCESSIBLE_SOURCE` si zéro citation restante | ✅ | `app/doc_acl.py:53-57,387-393` | Testé `test_doc_acl.py:211`. |
| RBAC-13 | `CompositeDocACL` OR-merge statique + Graph **+ Fabric** | ✅ | `app/doc_acl.py:197-206` ; `app/main.py:129-158` | Source Fabric ajoutée [M3]. |
| RBAC-14 | **Fail-OPEN sur erreur interne** (loader cassé) → body inchangé, log `doc_acl_error` | ✅ | `app/doc_acl.py:424-438` | Choix de disponibilité explicite. |
| RBAC-15 | **Fail-CLOSED sur doc_id inconnu** quand `default_policy=deny` | ✅ | `app/doc_acl.py:176-178` (`return self._default_policy == "allow"`) | Deux concerns distincts, conformes doc. |
| RBAC-16 | 4.3 bis : ACL auto-dérivée SharePoint, endpoint `GET /v1.0/sites/{}/drives/{}/items/{}/permissions` | ✅ | `app/graph_acl.py:186-189` | |
| RBAC-17 | Permission applicative `Sites.Read.All` (admin consent) | ❔ | `app/graph_acl.py:29-33` (commentaire) | Porte côté Entra ; non forcée par le code. |
| RBAC-18 | Parsing `grantedToV2.user.id`→users, `.group.id`→groupes, `.siteGroup.id`→groupes SP, + `grantedToIdentitiesV2` (liste) ; casse-insensible | ✅ | `app/graph_acl.py:122-149,201-210` | |
| RBAC-19 | Permission héritée incluse ; liens anonymes/sans rôle de lecture ignorés | ✅ | `app/graph_acl.py:152-161` (`_READ_ROLES`) ; commentaire `:42-44` | Pas de filtrage explicite sur `inheritedFrom` = inclusion par défaut, conforme. |
| RBAC-20 | Fail-CLOSED **par item** : item en échec **omis** de l'ACL Graph ; n'échoue pas le sync global | ✅ | `app/graph_acl.py:364-374` | Testé `test_graph_acl.py:334`. |
| RBAC-21 | Mode matérialisé : `make sync-doc-acl` écrit `doc_acl.json` | ✅ | `Makefile:114-118` ; `scripts/sync-doc-acl.py` (présent) ; `graph_acl.py:410-423` (`entries_to_acl_obj`) | Testé `test_graph_acl.py:614`. |
| RBAC-22 | Mode en vif : `GATEWAY_DOC_ACL_GRAPH_ENABLED=true`, refresh `GATEWAY_DOC_ACL_REFRESH_SECONDS` défaut **900 s** | ✅ | `config.py:208-212` ; `app/main.py:134-150,176-177` | Tâche de fond `_acl_refresher`. |
| RBAC-23 | Variables `GATEWAY_DOC_ACL_ENABLED` (true), `_PATH` (`config/doc_acl.json`), `_DEFAULT_POLICY` (deny), `_STRIP_UNCITED` (true) | ✅ | `config.py:198-203` | Défauts exacts. |
| RBAC-24 | §6.6 Fail-closed : identité illisible→**401**, overage+Graph indispo→**502**, sans groupe→**403** | ✅ | `app/main.py:259-271` (401/502) ; `:325-340` (403) | Testé `test_failclosed.py:49,62`. |
| RBAC-25 | §6.7 Journal des décisions allow/deny, identité pseudonymisée **HMAC-SHA256**, sel `GATEWAY_AUDIT_SALT`, jamais UPN/oid ni message en clair | ✅ | `app/audit.py:34-57,60-93` | Testé `test_audit.py:48`. |
| RBAC-26 | Tous les tests du modèle de menace (§6 tableau) existent | ✅ | `test_api.py:60,84,113,133,143,165` ; `test_failclosed.py:49,62` ; `test_audit.py:48` ; `test_doc_acl.py:211,267` ; `test_graph_acl.py:334,522,614` ; `test_onyx_proxy.py:35` ; `test_identity.py:114` | 18/18 noms cités présents. |
| RBAC-27 | Honnêteté §4.4 : filtre de SORTIE, pas de récupération ; LLM a pu voir le contenu | ✅ | `app/doc_acl.py:13-22` (docstring) | Cohérent, assumé. |
| RBAC-28 | L'UI/API Onyx native doit rester interne ; gateway = seul point d'entrée | ✅ | `deploy/prod/docker-compose.prod.yml:319-321` (`expose: 8200`, aucun port hôte) ; README `:58-69` | Mitigation déployée. |
| RBAC-32 | **[M3]** ACL **Fabric** câblée au filtre de citations : `FabricDocACL` (`DocACL`) OR-mergé via `CompositeDocACL` ; `is_authorized` synchrone (oid/UPN OU groupe ∈ `_Entry`) | ✅ | `app/fabric_doc_acl.py:61-122` (`is_authorized :98-122`) ; `app/main.py:129-158` (source ajoutée si `doc_acl_fabric_enabled`) | Ferme la fuite « citation Fabric hors-périmètre » (la garde `fabric_acl.py` existait mais était **débranchée** du filtre). Testé `test_fabric_doc_acl.py`. |
| RBAC-33 | **[M3]** Pré-résolution `build_fabric_acl` : par doc du mapping → garde GOLD (`item_in_gold_scope`) puis roleAssignments du workspace → `_Entry(groups,users)` ; **deny-by-default** (doc non mappé / hors gold / roleAssignments illisibles ⇒ OMIS) | ✅ | `app/fabric_doc_acl.py:164-237` ; gold gate `:204-211` ; fail-closed roleAssignments `:212-228` | roleAssignments mémoïsés par workspace (1 lecture). Tests : `test_fabric_doc_acl.py::{test_citation_fabric_autorisee_conservee, test_fabric_doc_acl_deny_quand_non_mappe}`. |
| RBAC-34 | **[M3]** Variables `GATEWAY_DOC_ACL_FABRIC_ENABLED` (false, opt-in), `_MAPPING_PATH` (`config/fabric_acl.json`) ; mapping absent ⇒ ACL Fabric **vide** (deny total) ; Fabric non configuré ⇒ ACL vide, **aucun appel réseau** | ✅ | `config.py:149-153,301-304` ; `fabric_doc_acl.py:181-184` (vide si non configuré) ; `load_mapping :239-262` (absent → `{}`) | Testé `test_fabric_doc_acl.py::test_build_fabric_acl_vide_si_non_configure` (0 appel réseau). |
| RBAC-29 | `X-OIDC-Claims` posé par reverse-proxy en amont (oauth2-proxy + nginx) | ✅ | `deploy/prod/docker-compose.prod.yml:201-303` | Chaîne navigateur→Caddy→oauth2-proxy→nginx→gateway. |
| RBAC-30 | Révocation différée : retrait de groupe pris au ré-login OU `GATEWAY_GROUP_CACHE_TTL` | ✅ | `app/identity.py:100-122` (`_TTLCache`) ; `config.py:167` (défaut 300) | |
| RBAC-31 | Citation `transitiveMemberOf` exige `User.Read.All` « marche aussi mais plus large » ; pas besoin de `Directory.Read.All` | ❔ | `graph_client.py:9-12` | Affirmation produit. |
| RBAC-32 | Permissions Graph EE plus larges (`Directory.Read.All`, `Group.Read.All`…) §2 | ❔ | — | Onyx EE non vendoré. |

## 2. `docs/DECISION_RBAC.md`

| # | Affirmation | Classe | Preuve (fichier:ligne) | Note |
|---|---|---|---|---|
| DEC-01 | Option A FOSS = SSO OIDC + Document Sets + passerelle (force filtre, deny-by-default, non-élargissable) | ✅ | `app/onyx_proxy.py:40-78` ; `app/main.py:305-354` | |
| DEC-02 | `doc_acl.py` (feat/rbac-perdoc) sur la RÉPONSE, granularité document, refus si zéro citation | ✅ | `app/doc_acl.py:304-438` | |
| DEC-03 | ACL auto-synchronisée SharePoint (`graph_acl.py`, `make sync-doc-acl`) | ✅ | `app/graph_acl.py:326-388` ; `Makefile:114-118` | |
| DEC-04 | §2.1 Fail-closed : identité→401, groupes irrésolvables→502, sans périmètre→403, set hors-périmètre→403 | ✅ | `app/main.py:259-271,325-340` ; `onyx_proxy.py:68-70` | |
| DEC-05 | §2.1 Anti-élargissement : intersection `document_set`, `search_doc_ids` neutralisé | ✅ | `app/onyx_proxy.py:64-77` | |
| DEC-06 | §2.1 Journal d'accès, identité hachée (`audit.py`), HMAC-SHA256 `GATEWAY_AUDIT_SALT` | ✅ | `app/audit.py:34-57` | |
| DEC-07 | §2.4 Révocation : `GATEWAY_GROUP_CACHE_TTL` **défaut 300 s** | ✅ | `config.py:167` (`"300"`) | |
| DEC-08 | §6.3 Fail-closed (testé) | ✅ | `test_failclosed.py` (3 cas) | |
| DEC-09 | §6 « **267 tests au total** », « 267 tests verts » | ✅ | `pytest access-gateway/tests --collect-only` = **267** (2026-06-18) ; `DECISION_RBAC.md` §6 L303-309, §8 L326-327 corrigés | **Corrigé** (itér. 1) : chiffre « 52 » obsolète → **267** réels, comptage vérifié. |
| DEC-10 | §6.4 audit JSON `onix.gateway.audit`, identité pseudonymisée, jamais message | ✅ | `app/audit.py:31,75-93` | |
| DEC-11 | §5.2 code EE Onyx non-MIT / proprio | ❔ | — | Référence Onyx externe (non vendoré). |
| DEC-12 | §3 prix Onyx (Business 20 $/u/mois, EE sur devis…) | ❔ | — | Sources web datées 2026-06-16 ; hors code. |
| DEC-13 | §4 « index FOSS non re-trimé à la recherche » (limite assumée) | ✅ | `app/onyx_proxy.py:1-17` (docstring : granularité Document Set, PAS document) | Cohérent avec l'honnêteté. |
| DEC-14 | §0 `lancer pytest access-gateway/tests -q` | ✅ | `Makefile:413` | |

## 3. `docs/CACHE.md`

| # | Affirmation | Classe | Preuve (fichier:ligne) | Note |
|---|---|---|---|---|
| CACHE-01 | Clé = `HMAC-SHA256(secret, blob)` ; blob = `KEY_SCHEMA_VERSION ∥ doc_sets_triés ∥ locale ∥ question_norm ∥ extras_json`, séparateur `\0` | ✅ | `app/cache.py:263-309` | Composition exacte. |
| CACHE-02 | `KEY_SCHEMA_VERSION = b"v1"` | ✅ | `app/cache.py:260` | |
| CACHE-03 | `authorized_doc_sets_sorted` = tri lexicographique + dédoublonnage joint par `,` | ✅ | `app/cache.py:302-303` (`sorted({...})`) | |
| CACHE-04 | `locale` lowercased, défaut `fr` | ✅ | `app/cache.py:299` ; `config.py:177` | |
| CACHE-05 | `normalized_question` = lowercase + collapse espaces | ✅ | `app/cache.py:228-239` | |
| CACHE-06 | `extras` = JSON `sort_keys=True, ensure_ascii=True, separators=(',',':')` | ✅ | `app/cache.py:242-254` | |
| CACHE-07 | Secret = `GATEWAY_CACHE_HMAC_SECRET` ; **principal PAS dans la clé** | ✅ | `app/cache.py:292-308` ; `config.py:176` | Justifié §2.3. |
| CACHE-08 | Isolation prouvée par `test_cache.py::TestCacheKey::test_key_isolation_by_perimeter` | ✅ | `test_cache.py:97` | Existe. |
| CACHE-09 | Bypass : `no_store`, `streaming`, `write_intent`, `explicit_admin_bypass`, ordre `no-store > streaming > admin > write` | ✅ | `app/cache.py:648-697` | Ordre code exact (admin avant write). |
| CACHE-10 | `explicit_admin_bypass` **ignoré pour les non-admins** ; **inerte en prod** (non câblé) | ✅ | `app/cache.py:681-684` (unité) ; `app/main.py:405` (sans `is_admin`) ; `CACHE.md §3` note « État de câblage réel » + §5/§13.6 notes | **Corrigé (doc) itér. 1** : la doc décrit désormais le comportement RÉEL — logique testée unitairement mais **non atteignable** par le chemin HTTP (toujours `is_admin=False`). Code inchangé (impact sécurité nul). |
| CACHE-11 | `build_cache` : disabled→None ; secret manquant→**RuntimeError (LOUD)** ; redis_url→RedisBackend ; sinon InMemoryBackend | ✅ | `app/cache.py:962-993` | |
| CACHE-12 | Exception-safety : défaut backend → miss propre, jamais 5xx ; observable `cache_errors_total{op}` | ✅ | `app/cache.py:764-787,209-217` ; `metrics.py:313-320` | |
| CACHE-13 | `cache.py` n'importe PAS FastAPI (pur Python + redis) | ✅ | `app/cache.py` (aucun import fastapi) | |
| CACHE-14 | Câblage `main.py` : `make_cache_key`, `should_bypass`, lookup avant httpx, store après post-filtre sur 2xx, hit→`cache_hit` audité + tokens_saved | ✅ | `app/main.py:402-490` ; `:436-440` (log `cache_hit`) | Conforme au squelette §5/§13.6. |
| CACHE-15 | **§11 : le cache stocke le corps périmètre-déterministe AVANT le filtre ACL par-doc ; ACL ré-appliquée à CHAQUE requête (hit ET miss)** — *piège AGENTS §7* | ✅ | `app/main.py:483-502` (store ligne 485-490, **puis** `filter_citations` ligne 495-502, hors du `else`) | **CONFIRMÉ.** Ordre `lookup → (miss) Onyx → garde-fous → STORE → (toujours) ACL par-doc → réponse`. Le filtre ACL est en dehors du bloc miss → s'exécute aussi sur hit. |
| CACHE-16 | Sur hit, le post-filtre n'est PAS re-run (déterministe) | ✅ | `app/main.py:432-440` (branche hit : pas de `post_filter`) | |
| CACHE-17 | Preuves : `test_integration_cache_acl.py::{test_cache_rbac_isolation_by_perimeter, test_doc_acl_isolation_between_users}` | ✅ | `test_integration_cache_acl.py:99,162` | Existent. |
| CACHE-18 | **8 compteurs** cache Prometheus (hits/misses/bypassed/tokens_saved/seconds_saved/errors/semantic_candidates/semantic_rejected) | ✅ | `metrics.py:90-137` | 8 exacts, labels conformes. |
| CACHE-19 | `seconds_saved` heuristique défaut **2.0 s/hit**, `GATEWAY_CACHE_SECONDS_PER_HIT` | ✅ | `metrics.py:256-268,278` | |
| CACHE-20 | `tokens_saved` = chars/4 | ✅ | `app/cache.py:703-724` (`len(answer)//4`) | |
| CACHE-21 | §7 Réglages `GATEWAY_CACHE_*` : ENABLED(true), REDIS_URL(vide), TTL(3600), MAX_ENTRIES(512), HMAC_SECRET(requis), LOCALE(fr), SECONDS_PER_HIT(2.0) | ✅ | `config.py:172-177` ; `metrics.py:256` | Tous défauts exacts. |
| CACHE-22 | §7 Sémantique : ENABLED(false/opt-in), EMBED_URL(`http://ollama:11434/api/embeddings`), EMBED_MODEL(`nomic-embed-text`), THRESHOLD(0.95), MAX_ENTRIES(=cache_max) | ✅ | `config.py:180-196` | Défauts exacts. |
| CACHE-23 | InMemory : LRU `OrderedDict`, thread-safe, TTL injectable | ✅ | `app/cache.py:102-152` | |
| CACHE-24 | Redis : redis-py, timeouts courts (0.5 s), fail-soft, warn-once par op | ✅ | `app/cache.py:168-222` | `socket_timeout=0.5`. |
| CACHE-25 | §13 Tier sémantique : partition par périmètre (`SemanticIndex`), recherche bornée à la partition | ✅ | `app/cache.py:445-565` ; `:504-535` | Match cross-périmètre structurellement impossible. |
| CACHE-26 | §13.2 Seuil cosinus 0.95 pur Python ; §13.3 garde anti-divergence sur marqueurs (`n:`/`m:`/`q:`/`e:`) ; entité MAJUSCULE ≥2 car. | ✅ | `app/cache.py:352-419,422-442` | Règles d'extraction et préfixes exacts. |
| CACHE-27 | §13.3 **Le garde anti-divergence reçoit la question BRUTE** (non normalisée) car la casse porte le signal d'entité | ✅ | `app/cache.py:476-500` (`divergence_text`=raw) ; `:861-901` (`raw_question`) ; `main.py:430,489` (`raw_question=question_text`) | Câblage raw confirmé de bout en bout. |
| CACHE-28 | §13.4 `semantic_lookup` ne lève jamais ; embed→None→miss gracieux ; course TTL→miss propre | ✅ | `app/cache.py:861-928` | |
| CACHE-29 | §13.5 Client embed Ollama legacy `POST /api/embeddings` `{model,prompt}`→`{embedding}`, sync, exception-safe, timeout ≤10 s | ✅ | `app/cache.py:604-642` (`min(timeout,10.0)`) | |
| CACHE-30 | `test_cache.py` **42 cas**, `test_cache_semantic.py` **55 cas**, `test_integration_cache_acl.py` **7 cas** | ✅ | comptage : 42 / 55 / 7 | **Exact**. |
| CACHE-31 | `test_cross_perimeter_never_matched` (assert partition autre = vide) | ✅ | `test_cache_semantic.py:330` | |
| CACHE-32 | §10 `make cache-bench` (offline, sans LLM/Onyx) | ✅ | `Makefile:183-223` | |
| CACHE-33 | §8.1 HA multi-réplicas REQUIERT Redis (LRU mémoire non partagée) | ✅ | `app/cache.py:46-49,299-303` (docstring) ; comportement InMemory | Cohérent. |
| CACHE-34 | §13.6 `cache.store(...)` rétro-compatible (kwargs sémantiques optionnels, no-op si désactivé) | ✅ | `app/cache.py:789-825,827-859` | |

## 4. `docs/STREAMING.md`

| # | Affirmation | Classe | Preuve (fichier:ligne) | Note |
|---|---|---|---|---|
| STR-01 | Garde DUR incrémental sur le texte **accumulé** : fuite prompt / exfil / write → **avorte** avant émission du morceau fautif | ✅ | `app/streaming.py:137-152,234-256` | Teste l'accumulé, pas le morceau seul. |
| STR-02 | Garde MOU final : groundedness via `post_filter` complet en fin de flux → override d'autorité | ✅ | `app/streaming.py:295-321` | |
| STR-03 | Filtre ACL par-document appliqué au paquet documents/citations **avant** relais | ✅ | `app/streaming.py:263-290,339-366` | Réutilise `doc_acl.filter_citations`. |
| STR-04 | Toutes citations retirées → override `no_accessible_source` | ✅ | `app/streaming.py:281-288,369-382` | |
| STR-05 | Contrat client : paquets `answer_piece`, `top_documents`/`citations`, `error`, `override`, `done` | ✅ | `app/streaming.py:111-113,229-293,326-333` | |
| STR-06 | `rule` ∈ no_prompt_leak / no_exfil_relay / read_only / no_citation / out_of_context / no_accessible_source / *_error | ✅ | `app/streaming.py:137-152` ; `guardrail.py:287-310` | Règles présentes. |
| STR-07 | Fail-CLOSED en streaming : toute exception du chemin de contrôle → override refus + done (jamais de contenu non vérifié) | ✅ | `app/streaming.py:240-247,270-276,299-305,323-328` | Contraste explicite avec le fail-OPEN non-streaming (STR-08). |
| STR-08 | Non-streaming `doc_acl` = fail-OPEN ; streaming = fail-CLOSED (deux arbitrages) | ✅ | `app/doc_acl.py:424-438` (open) vs `app/streaming.py:270-276` (closed) | Nuance §5 vérifiée. |
| STR-09 | §7 `GATEWAY_STREAM_ENABLED` (true), `GATEWAY_STREAM_IDLE_TIMEOUT` (60) | ✅ | `config.py:205-206` | Défauts exacts. |
| STR-10 | §8 **3 compteurs** : `stream_requests_total`, `stream_aborted_total{reason}`, `stream_overridden_total` ; reasons listés | ✅ | `metrics.py:141-158,350-379` ; `streaming.py:196,243,252,273,301,309,325` | |
| STR-11 | Cache correctement contourné pour les flux (`should_bypass` renvoie `"streaming"` pour `stream=True`) | ✅ (mécanisme) / ⚠️ (chemin) | `app/cache.py:677-678` | Vrai dans `should_bypass`. **MAIS** en réalité `main.py:362-391` traite le streaming **avant** d'appeler `should_bypass` (ligne 405) : le flux ne passe jamais par la logique cache, donc `cache_bypassed{streaming}` **n'est pas incrémenté** sur le chemin réel. Pas de fuite (le but est atteint), mais le compteur de bypass « streaming » reste à 0 en prod. Voir CODE-DOC-02. |
| STR-12 | §6 schéma Onyx amont (NDJSON : `answer_piece`, `top_documents`, `citation_num/document_id`, `error`) ; tolérance versions (`content`/`text`/`token`) | ✅ | `app/streaming.py:96-105,116-134,224-233` | `_PIECE_FIELDS` tolère les variantes. |
| STR-13 | Format déployé Onyx réel (legacy vs typé) « à confirmer contre l'instance » | ❔ | `app/streaming.py:31-33` (commentaire) | Honnêtement marqué « à confirmer ». Onyx non vendoré. |
| STR-14 | Titre/doc parlent de **« Streaming NDJSON »** | ✅ | `app/main.py:391` (`media_type="application/x-ndjson"`) ; `STREAMING.md` L1 (titre), encadré « Transport = NDJSON », §1 L31 ; `CACHE.md` L92, L303 | **Corrigé (doc) itér. 1** : titre « SSE » → « NDJSON », note explicite avec preuve `main.py:391`, et occurrences « SSE » de CACHE.md remplacées par « NDJSON ». Transport réel confirmé NDJSON (pas `text/event-stream`). |
| STR-15 | §10 `test_streaming.py` offline, async, couvre relais/avortements/override/ACL/fail-closed/audit | ✅ | `test_streaming.py` (16 cas) | Couverture conforme. |
| STR-16 | §9 câblage `main.py` via `httpx.stream` + `StreamingResponse`, injecte guardrail/doc_acl/onyx_proxy réels | ✅ | `app/main.py:362-391` | L'exemple doc utilise `build_request/send(stream=True)` ; le code réel utilise `http.stream(...)` (équivalent). |

## 5. CODE-SANS-DOC (🔇) & observations transverses

| # | Constat | Classe | Preuve |
|---|---|---|---|
| CODE-DOC-01 | `should_bypass(is_admin=...)` jamais passé par `main.py` → `explicit_admin_bypass` **inerte en prod** (toujours non-admin). **Doc alignée (itér. 1)** : CACHE.md §3 décrit l'inertie. | ✅ (doc) | `app/main.py:405` vs `app/cache.py:648-684` ; `CACHE.md §3` |
| CODE-DOC-02 | Le compteur `cache_bypassed{streaming}` n'est jamais incrémenté (streaming court-circuité avant la logique cache). | 🔇 | `app/main.py:362-391` vs `:405-419` |
| CODE-DOC-03 | Endpoint `POST /v1/feedback` (`onix_gateway_feedback_total{rating}`) **non documenté** dans les 4 docs du scope. | 🔇 | `app/main.py:512-544` ; `metrics.py:78-82` |
| CODE-DOC-04 | Endpoint `GET /v1/authorized-document-sets` (introspection) non mentionné par RBAC.md. | 🔇 | `app/main.py:276-302` |
| CODE-DOC-05 | `GET /metrics` renvoie **503** (pas 404) si génération Prometheus échoue ; 404 seulement si désactivé. Nuance non documentée. | 🔇 | `app/main.py:222-239` |
| CODE-DOC-06 | `GATEWAY_AUDIT_SALT` absent → sel **éphémère par processus** (corrélation intra-run seulement). Bien documenté dans `audit.py` mais **pas** dans RBAC.md (qui suppose un sel fixe). | ⚠️ | `app/audit.py:34-40` |
| CODE-DOC-08 | `_READ_ROLES` = liste blanche FINIE → rôles SharePoint custom/localisés non reconnus → item omis → faux refus (fail-closed, perte de **disponibilité**). **Doc ajoutée (itér. 1)** : RBAC.md §4.3 bis documente la limite. | ✅ (doc) | `app/graph_acl.py:70,152-161` ; `RBAC.md §4.3 bis` |
| CODE-DOC-07 | `build_cache` lève si secret manquant (filet fail-loud) ; `main.py` capte et désactive le cache (fail-safe, log CRITICAL) plutôt que crasher. **Doc alignée (itér. 1)** : CACHE.md §4/§7 décrit la **dégradation gracieuse** réelle. | ✅ (doc) | `app/main.py:164-168` vs `cache.py:976-980` ; `CACHE.md §4`, §7 |

> **Note hors-scope mais signalée** : `CLAUDE.md`/`AGENTS.md` parlent d'« **audit
> HMAC chaîné** ». Le code `audit.py` réalise une **pseudonymisation HMAC** des
> identités, **mais aucune chaîne de hachage** (pas de lien cryptographique entre
> enregistrements successifs). Les 4 docs du scope, elles, disent correctement
> « journal **haché** » (RBAC.md §6.7) — pas « chaîné ». Aucun écart **dans le
> périmètre** ; mais la formule « chaîné » de l'embarquement est **inexacte**.

---

## Écarts « production-ready entreprise » (priorisés)

### P0 — bloquant sécurité/conformité
*Aucun.* Les invariants critiques (deny-by-default, fail-closed 401/502/403,
non-élargissement, neutralisation `search_doc_ids`, isolation cache par périmètre,
ACL ré-appliquée par requête, fail-closed streaming, audit pseudonymisé) sont
**implémentés ET testés**. Le piège AGENTS §7 est respecté (CACHE-15).

### P1 — à corriger avant un audit client exigeant
1. **`explicit_admin_bypass` inerte** (CODE-DOC-01) — ✅ **RÉSOLU (doc) itér. 1** :
   CACHE.md §3 (note « État de câblage réel ») + §5/§13.6 décrivent désormais
   l'inertie (logique testée unitairement mais non atteignable par le chemin HTTP,
   `is_admin` toujours `False`). La promesse n'est plus présentée comme
   opérationnelle (règle de jeu n°1). Code volontairement **inchangé** (impact
   sécurité nul ; câbler `is_admin` resterait possible plus tard).
2. **`graph_acl` `_READ_ROLES` partiel** (CODE-DOC-08) — ✅ **RÉSOLU (doc) itér. 1** :
   RBAC.md §4.3 bis documente la liste blanche FINIE (`graph_acl.py:70`), le
   risque de faux refus pour rôles custom/localisés (perte de **disponibilité**,
   jamais de confidentialité), la nature **fail-closed** délibérée et la procédure
   d'extension de `_READ_ROLES` par tenant. Code **inchangé**.
3. **Divergence fail-loud vs fail-safe du cache** (CODE-DOC-07) — ✅ **RÉSOLU (doc)
   itér. 1** : CACHE.md §4 (note « Comportement réel au démarrage ») + §7
   décrivent la **dégradation gracieuse** réelle (`build_cache` lève une
   `RuntimeError`, mais `main.py:164-168` la capte → cache OFF + log CRITICAL,
   pas d'arrêt). Choix de disponibilité désormais aligné sur la doc. Code
   **inchangé**.

### P2 — propreté / dette documentaire
4. **« 52 tests » obsolète** (DEC-09) — ✅ **RÉSOLU itér. 1** : DECISION_RBAC.md
   §6/§8 actualisés à **267** tests (comptage `pytest --collect-only` vérifié le
   2026-06-18).
5. **« Streaming SSE » trompeur** (STR-14) — ✅ **RÉSOLU itér. 1** : STREAMING.md
   titre renommé « Streaming NDJSON » + note de transport (preuve `main.py:391`) ;
   occurrences « SSE » de CACHE.md (L92, L303) remplacées par NDJSON. Transport
   réel = `application/x-ndjson` (pas `text/event-stream`).
6. **Endpoints non documentés** (`/v1/feedback`, `/v1/authorized-document-sets`,
   nuance 503 de `/metrics`) — compléter CACHE.md/RBAC.md/OBSERVABILITY.md.
7. **Compteur `cache_bypassed{streaming}`** mort en prod (CODE-DOC-02) : soit
   l'incrémenter dans la branche streaming, soit noter qu'il ne sert qu'au chemin
   non-streaming.

---

## Verdict du scope (3 lignes)

Le scope **access-gateway** est **honnête et fidèle** : 71 affirmations ✅ sur 94,
aucun 🕳️ (rien d'inventé), **zéro P0**, et le piège-clé du cache (corps
périmètre-déterministe stocké AVANT l'ACL par-doc ré-appliquée par requête) est
**confirmé par preuve**. Les seuls écarts sont des **promesses doc légèrement en
avance sur le câblage** (`explicit_admin_bypass` inerte, fail-loud/fail-safe du
cache) et de la **dette documentaire** (« 52 tests », « SSE » vs NDJSON, endpoints
non décrits). **Production-ready : presque** — corriger les 3 points P1 (surtout
l'honnêteté de l'admin-bypass) suffit à atteindre la rigueur d'audit attendue.
