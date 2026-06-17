# Cache applicatif RBAC-safe (`access-gateway`)

Ce document décrit la **couche de cache déterministe** ajoutée à la passerelle
`access-gateway/` au-dessus d'Onyx/Ollama. Elle évite tout aller-retour LLM/RAG
quand une **question identique** est posée dans le **même périmètre RBAC**.

> **Position dans la pile** : `Ollama KV-cache` (token-level, INTERNE au modèle)
> ⟶ `Onyx` (RAG, scoring documentaire) ⟶ **`access-gateway` (notre cache de
> réponse JSON, applicatif)** ⟶ client. Le cache applicatif est la couche la
> plus haute, la plus déterministe, et **la seule cross-requête/cross-tenant**
> sous notre contrôle.

---

## 1. Pourquoi un cache applicatif ?

Sur un déploiement RAG réel, on observe systématiquement un « tail » de
questions répétées (FAQ implicites, intégrations outillées, reformulations
exactement identiques). Sans cache applicatif :

* chaque répétition retraverse Onyx (réindexation léger + scoring) **et** le
  LLM (génération complète) → coût en tokens et latence inchangés ;
* le KV-cache d'Ollama n'aide PAS d'une session à l'autre (il vit dans le
  contexte d'une seule séquence de tokens).

Avec le cache de la gateway :

* **coût** : une réponse cachée = 0 token côté LLM ;
* **latence** : on rend la réponse en ~ms (lookup Redis/mémoire) au lieu de
  plusieurs secondes ;
* **traçabilité** : un hit est un `decision="allow", reason="cache_hit"`
  audité comme n'importe quelle requête (cf. `audit.log_access_decision`).

---

## 2. Propriété cardinale — isolation RBAC

> **Aucun utilisateur ne peut recevoir une réponse mise en cache pour un
> utilisateur dont le périmètre Document Set autorisé diffère.**

### 2.1. Composition exacte de la clé

La clé est `HMAC-SHA256(secret, blob)` sur le **blob canonique** suivant
(séparateur `\0` qui ne peut pas apparaître dans les champs) :

```
KEY_SCHEMA_VERSION  ⫶  authorized_doc_sets_sorted  ⫶  locale  ⫶  normalized_question  ⫶  canonical_extras_json
```

où :

| Champ | Source | Précision |
|---|---|---|
| `KEY_SCHEMA_VERSION` | constante module `cache.py` | `b"v1"` aujourd'hui ; bump à toute modif de composition |
| `authorized_doc_sets_sorted` | retour de `mapping.authorized_document_sets(...)` | **tri lexicographique** + dédoublonnage, joint par `,` |
| `locale` | `Settings.cache_locale` | `fr` par défaut, lowercased |
| `normalized_question` | `normalize_question(payload.message)` | lowercase + collapse espaces |
| `canonical_extras_json` | optionnel | JSON `sort_keys=True, ensure_ascii=True, separators=(',', ':')` |

Le **secret HMAC** vient de `GATEWAY_CACHE_HMAC_SECRET` (env).

### 2.2. Preuve d'isolation

Le test `test_cache.py::TestCacheKey::test_key_isolation_by_perimeter` couvre
explicitement le cas : deux appels strictement identiques **sauf**
`authorized_doc_sets` doivent produire des clés DIFFÉRENTES. Tant que ce test
passe, la propriété d'isolation est mécaniquement vraie : aucune logique
applicative supplémentaire (filtres, conditions) n'est nécessaire pour
empêcher User A de récupérer le cache d'User B.

### 2.3. Pourquoi ne pas inclure `principal` (UPN/oid) dans la clé ?

Le **périmètre EFFECTIF** est `authorized_doc_sets`. Deux utilisateurs au
**même périmètre** verront — par construction RBAC — exactement la même
réponse d'Onyx (le filtre amont est calculé à partir de ce périmètre). Inclure
`principal` ferait perdre la mutualisation entre utilisateurs **sans aucun
gain de sécurité**.

Le `principal` reste utilisé en audit (pseudonymisé, comme partout).

---

## 3. Politique de contournement (bypass)

`cache.should_bypass(payload, headers, is_admin)` renvoie une **raison**
(string) ou `None`. Les raisons matchent **exactement** les valeurs du label
`reason` de `onix_gateway_cache_bypassed_total` :

| Raison | Déclencheur | Pourquoi on bypass |
|---|---|---|
| `no_store` | header `Cache-Control: no-store` | directive HTTP standard cliente, prioritaire |
| `streaming` | `payload.stream is True` | un body SSE/streamé n'est pas un JSON intégral cachable |
| `write_intent` | `guardrail.is_write_request(payload.message)` | une intention d'écriture ne doit JAMAIS être satisfaite sans le pipeline complet (le post-filtre y est encore plus strict) |
| `explicit_admin_bypass` | header `X-Onix-Cache: bypass` **+** `is_admin=True` | debug / diagnostic admin ; **ignoré pour les non-admins** (anti déni-de-cache) |

L'ordre est délibéré : `no-store` (directive cliente HTTP) > `streaming` >
`explicit_admin_bypass` > `write_intent` (heuristique métier).

---

## 4. Architecture du module `app/cache.py`

```
┌─────────────────────────────────────────────────────────────────────┐
│ build_cache(settings) → Cache | None                                │
│  ├─ cache_enabled=False                       → None                │
│  ├─ cache_enabled=True + secret manquant      → RuntimeError (LOUD) │
│  ├─ cache_redis_url non vide                  → RedisBackend        │
│  └─ sinon                                     → InMemoryBackend     │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
       ┌───────────────────────────────────────────────────────┐
       │ Cache  (facade)                                       │
       │  - lookup(key, tier="exact") → dict | None            │
       │  - store(key, body, ttl)     → None                   │
       │  - notifications : on_hit / on_miss / on_error        │
       │    → câblage vers `metrics.inc_cache_*`               │
       └───────────────────────────────────────────────────────┘
                              │
                ┌─────────────┴──────────────┐
                ▼                            ▼
       ┌─────────────────┐         ┌─────────────────────────┐
       │ InMemoryBackend │         │ RedisBackend            │
       │  LRU OrderedDict│         │  redis-py, timeouts     │
       │  thread-safe    │         │  fail-soft (miss/no-op) │
       │  TTL injectable │         │  warn-once par op       │
       └─────────────────┘         └─────────────────────────┘
```

**Exception-safety stricte** : un défaut du backend (timeout Redis, parse
JSON, sérialisation, OOM…) **NE PROPAGE JAMAIS** vers la requête HTTP. Il
est logué une fois, observable via `onix_gateway_cache_errors_total{op}`, et
le cache est traité comme s'il n'avait pas répondu (miss propre).

---

## 5. Intégration dans `main.py` (orchestrateur)

Le module `cache.py` n'importe PAS FastAPI : il reste pur Python (stdlib +
`redis`). L'orchestrateur câble `main.py` selon ce squelette (6 lignes
fonctionnelles, le reste est du contexte) :

```python
# build_cache au démarrage (lifespan) :
app.state.cache = build_cache(settings)   # None si désactivé

# Dans POST /v1/chat/send-message — AVANT l'appel httpx amont :
cache = app.state.cache
norm_q = normalize_question(payload.get("message", ""))
ckey   = make_cache_key(settings=settings, principal=principal.user_id,
                        normalized_question=norm_q, authorized_doc_sets=authorized)
bypass = should_bypass(payload=payload, headers=request.headers,
                       is_admin=getattr(principal, "is_admin", False))
if cache and bypass is None:
    hit = cache.lookup(ckey)
    if hit is not None:
        log_access_decision(actor=principal.user_id, decision="allow",
                            reason="cache_hit", endpoint="chat/send-message",
                            authorized_sets=authorized)
        add_cache_tokens_saved(estimate_tokens(hit))
        return JSONResponse(content=hit, status_code=200)
elif bypass:
    inc_cache_bypassed(bypass)

# ... appel Onyx + post_filter (inchangé) ...

# APRÈS le post-filtre, sur 2xx, et SEULEMENT si bypass=None :
if cache and bypass is None and 200 <= resp.status_code < 300:
    cache.store(ckey, body, ttl=settings.cache_ttl_seconds)
```

**Point critique** : on stocke le `body` **POST-FILTRÉ** (après `post_filter`).
Conséquence : sur un hit, on **ne re-run pas le post-filtre** — la réponse
servie est exactement celle qu'on a délivrée la première fois (déterministe,
auditée). C'est intentionnel : ré-évaluer le post-filtre sur un body déjà
filtré n'a aucun sens et ajouterait du CPU pour rien.

---

## 6. Observabilité — 6 compteurs Prometheus

| Métrique | Labels | Sens |
|---|---|---|
| `onix_gateway_cache_hits_total` | `tier` (`exact` aujourd'hui ; `semantic` futur) | Hits du cache applicatif |
| `onix_gateway_cache_misses_total` | — | Misses (entrée absente ou expirée) |
| `onix_gateway_cache_bypassed_total` | `reason` (`no_store`\|`write_intent`\|`streaming`\|`explicit_admin_bypass`) | Cache contourné volontairement |
| `onix_gateway_cache_tokens_saved_total` | — | Tokens approximatifs économisés (heuristique chars/4) |
| `onix_gateway_cache_seconds_saved_total` | — | Secondes de génération économisées (heuristique constante, cf. `GATEWAY_CACHE_SECONDS_PER_HIT`) |
| `onix_gateway_cache_errors_total` | `op` (`get`\|`set`) | Erreurs backend (exception-safe, déjà transformées en miss/no-op) |

**Heuristique « secondes économisées »** : par défaut **2.0 s par hit** —
ordre de grandeur d'une génération RAG sur un LLM 7B local en CPU. Ajustable
via `GATEWAY_CACHE_SECONDS_PER_HIT` (float, secondes) une fois la mesure
empirique (histogramme `onix_gateway_request_latency_seconds`) connue en prod.

**Heuristique « tokens économisés »** : `chars(answer) / 4` — cohérent avec
les ratios moyens des tokenizers byte-pair pour FR/EN. **Suffisant** pour
piloter une tendance ; **insuffisant** pour facturation comptable (utiliser
le tokenizer réel d'Ollama si besoin).

Lecture suggérée pour le hit-rate **vrai** (excluant les bypass volontaires) :

```promql
rate(onix_gateway_cache_hits_total[5m])
/ (rate(onix_gateway_cache_hits_total[5m]) + rate(onix_gateway_cache_misses_total[5m]))
```

---

## 7. Réglages (`GATEWAY_CACHE_*`)

| Variable | Défaut | Description |
|---|---|---|
| `GATEWAY_CACHE_ENABLED` | `true` | Active la couche ; `false` ⇒ aucune logique de cache n'est exécutée. |
| `GATEWAY_CACHE_REDIS_URL` | (vide) | `redis://host:6379/0` → Redis ; vide → LRU mémoire. |
| `GATEWAY_CACHE_TTL_SECONDS` | `3600` | TTL d'une entrée. `0` = sans expiration (déconseillé). |
| `GATEWAY_CACHE_MAX_ENTRIES` | `512` | Bornage de la LRU mémoire (ignoré en Redis). |
| `GATEWAY_CACHE_HMAC_SECRET` | (REQUIS si activé) | Secret HMAC stable. **Fail-loud** au démarrage si manquant. |
| `GATEWAY_CACHE_LOCALE` | `fr` | Locale incluse dans la clé (différencie FR/EN). |
| `GATEWAY_CACHE_SECONDS_PER_HIT` | `2.0` | Heuristique « secondes économisées ». |

**Génération du secret HMAC** (à exécuter une fois et stocker dans le coffre) :

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

**Ne JAMAIS** logger le secret. Le code refuse même de construire un `repr()`
qui le contienne (cf. `cache.py` — `cache_hmac_secret` n'est jamais exposé).

---

## 8. Ops — dimensionner & invalider

### 8.1. Dimensionner

* **LRU mémoire** : `cache_max_entries=512` ≈ 5–50 MB selon la taille des
  réponses (Onyx renvoie typiquement 5–80 KB de JSON). Monter à `2048` voire
  `8192` est sans risque tant qu'on observe un RSS stable.
* **Redis** : pas de borne d'entrées ; configurer `maxmemory` + politique
  `allkeys-lru` côté Redis. L'opt-in HA (multi-réplicas gateway) **REQUIERT**
  Redis : la LRU mémoire n'est PAS partagée entre processus.
* **TTL** : 1 h (`3600`) est un bon compromis sur des connaissances qui
  évoluent peu (FAQ, procédures internes). Pour un corpus très dynamique
  (actualités, prix), descendre à 5–10 min ; à l'inverse, du contenu
  réglementaire stable peut aller à 24 h.

### 8.2. Invalidation

Plusieurs leviers (du plus chirurgical au plus radical) :

1. **TTL naturel** : laisser expirer (le plus simple).
2. **Rotation du secret HMAC** (`GATEWAY_CACHE_HMAC_SECRET`) : invalide
   **toutes** les clés en une opération. Utile lors d'un changement de
   tokenizer/modèle qui rendrait les réponses anciennes obsolètes.
3. **Bump de `KEY_SCHEMA_VERSION`** dans `cache.py` : invalide tout aussi
   surement, sans toucher au secret. À privilégier quand on change la
   COMPOSITION (ex. ajouter `model_id` au blob).
4. **Flush Redis ciblé** (`SCAN` + `DEL`) en cas d'incident corpus précis.
5. **Override client** : un appelant peut passer `Cache-Control: no-store`
   pour forcer un recalcul ponctuel (tracé en `cache_bypassed{no_store}`).

### 8.3. Quand flush ?

* Après un re-ingest massif Onyx qui CHANGE le contenu d'un Document Set ;
* après un changement de prompt système (rare mais critique) ;
* après une rotation de modèle Ollama (la réponse caractère-par-caractère
  changera).

---

## 9. Limites assumées (honnêteté)

* **Pas de tier sémantique aujourd'hui.** La normalisation est lexicale
  (lowercase + espaces). Deux formulations différentes de la même intention
  produisent deux clés distinctes ⇒ deux miss. Un futur `tier="semantic"`
  s'appuiera sur un embedding + seuil — le label est **déjà câblé** côté
  métrique pour éviter une rupture de série temporelle le jour où il sera
  activé.
* **Heuristiques transparentes.** `tokens_saved` (chars/4) et `seconds_saved`
  (constante par hit) sont des **ordres de grandeur**, pas des mesures
  comptables. La documentation produit clarifie ce point.
* **InMemory non partagée.** En HA (multi-réplicas gateway derrière un LB),
  un déploiement sans Redis aura un hit-rate dégradé (chaque réplica
  reconstruit son cache). **Redis recommandé** dès qu'on monte au-delà
  d'un worker uvicorn.
* **Pas de cache pour `stream=True`.** Un body SSE n'est pas un JSON intégral
  ; le cacher trahirait la sémantique streaming attendue par le client.
* **Pas de scope « par utilisateur ».** Volontaire : deux utilisateurs au
  même périmètre RBAC partagent légitimement le cache. Si un usage exigeait
  un cache strictement par-utilisateur (préférences personnelles dans la
  réponse, par ex.), passer `extras={"user": principal.user_id}` à
  `make_cache_key` ; aucune autre modification n'est nécessaire.

---

## 10. Bench offline (`make cache-bench`)

Un mini-bench est livré pour vérifier le câblage en local :

```
make cache-bench
```

Il instancie un `Cache` mémoire, simule N requêtes identiques sans réseau, et
imprime hit-rate + tokens économisés. Aucune dépendance LLM/Onyx (offline
strict). Voir `Makefile` cible `cache-bench`.

---

## 11. Tests (`access-gateway/tests/test_cache.py`)

42 cas, tous offline (`pytest access-gateway/tests/test_cache.py -q`). Ils
couvrent : normalisation, isolation RBAC, bypass, LRU/TTL, fail-soft Redis
(via monkeypatch — pas de fakeredis), fail-loud sur secret manquant,
roundtrip JSON, et l'incrémentation effective des 4 compteurs Prometheus
introduits.
