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

## 6. Observabilité — 8 compteurs Prometheus

| Métrique | Labels | Sens |
|---|---|---|
| `onix_gateway_cache_hits_total` | `tier` (`exact` \| `semantic`) | Hits du cache applicatif, par tier |
| `onix_gateway_cache_misses_total` | — | Misses (entrée absente ou expirée) |
| `onix_gateway_cache_bypassed_total` | `reason` (`no_store`\|`write_intent`\|`streaming`\|`explicit_admin_bypass`) | Cache contourné volontairement |
| `onix_gateway_cache_tokens_saved_total` | — | Tokens approximatifs économisés (heuristique chars/4) |
| `onix_gateway_cache_seconds_saved_total` | — | Secondes de génération économisées (heuristique constante, cf. `GATEWAY_CACHE_SECONDS_PER_HIT`) |
| `onix_gateway_cache_errors_total` | `op` (`get`\|`set`) | Erreurs backend (exception-safe, déjà transformées en miss/no-op) |
| `onix_gateway_cache_semantic_candidates_total` | — | Voisins sémantiques au-dessus du seuil cosinus (presque-hits, **avant** le garde divergence) — cf. §13 |
| `onix_gateway_cache_semantic_rejected_divergence_total` | — | Candidats REJETÉS par le garde anti-divergence factuelle (nombres/dates/entités) — **mesure de sûreté** |

> **Lecture sûreté du tier sémantique** : un ratio `rejected_divergence / candidates`
> élevé signifie que le garde travaille (beaucoup de voisins similaires mais
> factuellement différents écartés). Un ratio `hits{tier="semantic"} / candidates`
> proche de 1 avec peu de rejets suggère un corpus peu factuel (ou un seuil trop
> bas). Surveillez `rejected_divergence` : c'est la preuve VIVANTE que le cache
> ne sert pas la réponse de la question A pour une question B.

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
| `GATEWAY_SEMANTIC_CACHE_ENABLED` | `false` | **OPT-IN.** Active le tier sémantique (embedding + seuil). Désactivé par défaut : risque de précision sur du factuel (cf. §13). |
| `GATEWAY_SEMANTIC_EMBED_URL` | `http://ollama:11434/api/embeddings` | Endpoint Ollama d'embeddings (legacy singulier `{model,prompt}`→`{embedding}`). |
| `GATEWAY_SEMANTIC_EMBED_MODEL` | `nomic-embed-text` | Modèle d'embeddings (déjà pull). Doit être déterministe. |
| `GATEWAY_SEMANTIC_THRESHOLD` | `0.95` | Seuil COSINUS minimal d'un hit. Élevé : faux positif >> miss en coût métier. |
| `GATEWAY_SEMANTIC_MAX_ENTRIES` | (= `GATEWAY_CACHE_MAX_ENTRIES`) | Bornage LRU de l'index sémantique **par périmètre**. |

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

* **Tier sémantique : opt-in, à manier avec discernement.** Le tier exact
  reste la base (lexical, déterministe, sans réseau). Le tier sémantique
  (`tier="semantic"`) est **disponible mais désactivé par défaut**
  (`GATEWAY_SEMANTIC_CACHE_ENABLED=false`). Il capture les reformulations mais
  introduit un **risque de précision intrinsèque** sur un corpus factuel,
  mitigé par deux garde-fous structurels — **voir la §13 dédiée** (architecture,
  règles EXACTES du garde anti-divergence, et limites honnêtes du garde).
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

## 11. Interaction avec le filtre ACL par-document (RBAC fin)

**Point de correction critique** (câblé dans `main.py`) : le cache ne stocke QUE
le corps **périmètre-déterministe** — la réponse d'Onyx après le post-filtre
garde-fous, **AVANT** le filtre ACL par-document (`doc_acl.py`). Le filtre ACL est
**par utilisateur** (groupes/UPN) et donc **ré-appliqué à CHAQUE requête**, hit ou
miss.

Conséquence : deux utilisateurs au **même périmètre Document Set** mutualisent le
coût LLM (même clé), mais chacun se voit retirer **individuellement** les citations
qu'il n'a pas le droit de voir — **jamais** une citation cachée pour l'un n'est
servie à l'autre. Mettre en cache le corps *après* le filtre ACL serait un bug
d'isolation : c'est explicitement évité. Preuves :
`tests/test_integration_cache_acl.py::{test_cache_rbac_isolation_by_perimeter,test_doc_acl_isolation_between_users}`.

Ordre du chemin réponse :
`lookup → (miss) Onyx → garde-fous → STORE(cache) → (toujours) filtre ACL par-doc → réponse`.

## 12. Tests

- `access-gateway/tests/test_cache.py` — **42 cas** offline : normalisation,
  isolation RBAC de la clé, bypass, LRU/TTL, fail-soft Redis (monkeypatch),
  fail-loud sur secret manquant, roundtrip JSON, compteurs Prometheus.
- `access-gateway/tests/test_integration_cache_acl.py` — **7 cas** prouvant le
  **câblage E2E** dans `main.py` (hit évite l'amont, isolation par périmètre,
  mutualisation même périmètre, bypass write/no-store, filtre ACL par-doc).
- `access-gateway/tests/test_cache_semantic.py` — **55 cas** offline (embed
  TOUJOURS mocké) pour le tier sémantique : extraction des marqueurs factuels,
  garde anti-divergence, cosinus pur-Python, partition par périmètre,
  **cross-périmètre jamais matché**, near-dup → hit, sous-seuil → miss,
  divergence 2024/2025 et ALPHA/BETA → rejet, échec embed → miss gracieux,
  client Ollama exception-safe (httpx mocké), compteurs.

---

## 13. Tier SÉMANTIQUE (embedding + seuil) — opt-in, safety-first

> **Mandat de conception** : un cache sémantique sur un corpus **factuel** est
> une **responsabilité** s'il est naïf — servir la réponse de la question A pour
> une question B « proche » est une erreur silencieuse de justesse. Ce tier est
> conçu pour être **génuinement utile ET sûr**, ou désactivé. Il l'est par
> défaut (`GATEWAY_SEMANTIC_CACHE_ENABLED=false`).

### 13.1. Objectif

Le tier exact (§1–§5) ne matche que la question **normalisée à l'identique**.
Une **reformulation** (« combien de jours de congés me reste-t-il ? » vs « quel
est mon solde de congés ? ») produit deux clés ⇒ deux appels LLM. Le tier
sémantique rattrape ces reformulations **dans le même périmètre RBAC**, pour
plus de hits ⇒ moins de coût/latence.

### 13.2. Trois couches de sûreté

1. **Partition PAR PÉRIMÈTRE (RBAC-safe par construction).**
   L'index sémantique (`SemanticIndex`) est un dictionnaire
   `{ périmètre → LRU[ clé_exacte → (embedding de la question normalisée, texte BRUT pour le garde divergence) ] }`. La
   recherche d'un voisin se fait **UNIQUEMENT** dans la partition du périmètre
   de la requête (`_perimeter_partition` = sets autorisés triés+dédoublonnés,
   **la même définition** que la composante `authorized_doc_sets` de la clé
   HMAC exacte). Un match cross-périmètre est **structurellement impossible** :
   il n'y a littéralement aucun voisin à trouver dans une autre partition. Ce
   n'est pas une condition applicative qu'on pourrait oublier — c'est la forme
   même de la donnée. Preuve :
   `test_cache_semantic.py::TestSemanticLookup::test_cross_perimeter_never_matched`
   (qui assert en plus que la partition de l'autre périmètre est **vide**).

2. **Seuil cosinus élevé (0.95 par défaut).**
   On préfère un **miss** (recalcul correct) à un **faux positif** (mauvaise
   réponse servie). En-dessous du seuil → miss net. Cosinus calculé en **pur
   Python** (`_cosine`, aucune dép numpy/torch).

3. **Garde anti-divergence factuelle (LE cœur de la sûreté).**
   Même AU-DESSUS du seuil, on **REFUSE** le match si la requête et le candidat
   divergent sur un fait saillant. C'est ce qui distingue une **reformulation**
   (sûre à matcher) d'une **question factuellement différente** (dangereuse).

### 13.3. Règles EXACTES du garde anti-divergence

> ⚙️ **Entrée du garde = la question BRUTE** (non normalisée). L'embedding
> (similarité cosinus) utilise la forme *normalisée* (lowercase, casse-insensible) ;
> le garde anti-divergence utilise la forme *brute* car **la casse porte le signal
> d'entité** : sans cela, `ALPHA` deviendrait `alpha` et la règle MAJUSCULES ne le
> verrait plus (l'orchestrateur passe donc `raw_question` à `semantic_lookup`/`store`).

`_has_factual_divergence(query, candidate)` renvoie `True` (⇒ **REJET du hit**)
dès que l'**ensemble des marqueurs factuels** des deux textes diffère
(différence symétrique non vide). Les marqueurs extraits par
`_extract_factual_tokens` sont :

| Catégorie | Règle d'extraction | Exemples capturés | Préfixe interne |
|---|---|---|---|
| **Nombre / date / année / version / quantité** | tout token contenant ≥1 chiffre (`\d`), ponctuation de bord retirée, lowercasé | `2024`, `12/03/2025`, `1.5`, `v2`, `3000` | `n:` |
| **Montant / pourcentage** | token portant un symbole `% € $ £` ou une unité (`eur`, `usd`, `gbp`, `k€`, `m€`, `md€`, `pourcent`, `pct`) | `5000€`, `12%`, `3 k€` | `m:` (+ `n:` car chiffré) |
| **Entité saillante (quote)** | segment entre guillemets `" « » “ ” '` (1–64 car.), espaces internes collapsés, lowercasé | `"Acme Corp"`, `« Projet Lune »` | `q:` |
| **Entité saillante (MAJUSCULES)** | token de **≥2 caractères** entièrement en capitales (lettres A–Z + accents FR À-Þ, chiffres autorisés en interne) | `ALPHA`, `BETA`, `SARL`, `EBITDA`, `CDI` | `e:` |

**Conséquences directes (toutes testées) :**

* `« CA 2024 »` vs `« CA 2025 »` → `{n:2024}` ≠ `{n:2025}` → **REJET**.
* `« client ALPHA »` vs `« client BETA »` → `{e:ALPHA}` ≠ `{e:BETA}` → **REJET**.
* `« remise 10% »` vs `« remise 20% »` → **REJET**.
* `« quel est le plafond »` vs `« quel est le plafond de 3000€ »` → un fait
  ajouté d'un côté → **REJET**.
* `« le CA en 2024 »` vs `« quel était le CA 2024 »` → `{n:2024}` = `{n:2024}`,
  reste lexical ignoré → **PAS de divergence** → hit autorisé (si ≥ seuil).
* `« comment poser des congés »` vs `« procédure pour les congés »` → aucun
  marqueur factuel des deux côtés → **PAS de divergence** → hit autorisé.

Le garde est **volontairement sur-inclusif** : en cas de doute, il déclare
« divergent » ⇒ un miss inoffensif (recalcul correct), jamais un faux
« identique » ⇒ réponse erronée. **La précision du cache est sacrifiée au
profit de la justesse.** Une seule initiale isolée (`A`, `I`) n'est PAS traitée
comme une entité (seuil ≥2 caractères) pour éviter un excès de faux divergents.

### 13.4. Chemin de décision (`Cache.semantic_lookup`)

```
semantic_lookup(perimeter, question_normalisée, embed_fn, raw_question):
  1. pas d'index sémantique câblé        → None (no-op)
  2. embed_fn(question) → None/exception  → None (MISS GRACIEUX)
  3. recherche du meilleur voisin DANS LA PARTITION `perimeter` :
       - meilleur cosinus < seuil          → None (miss net)
       - sinon → CANDIDAT (candidates++)   ; pour chaque voisin ≥ seuil,
         trié par similarité décroissante :
           - divergence factuelle ?        → rejected_divergence++ ; voisin suivant
           - sinon                         → on retient sa clé_exacte
  4. lecture du body via la clé_exacte (backend) :
       - absent (course TTL)               → None (miss propre)
       - présent                           → inc_cache_hit("semantic") ; renvoie le body
```

`semantic_lookup` **NE LÈVE JAMAIS** : toute défaillance (embed, recherche,
backend) se traduit en `None` ⇒ l'orchestrateur enchaîne sur l'appel amont.

### 13.5. Client d'embeddings Ollama (`build_embed_fn`)

Schéma **assumé** (confirmé via Context7 `/ollama/ollama`, `docs/api.md`) —
endpoint **legacy singulier** `POST /api/embeddings` :

```
requête  : { "model": "nomic-embed-text", "prompt": "<question normalisée>" }
réponse  : { "embedding": [0.0123, -0.045, ...] }
```

> Note : l'endpoint **moderne** `/api/embed` renvoie `{"embeddings": [[...]]}`
> (batch, pluriel). On cible le legacy singulier, plus simple pour notre usage
> 1-question. Pour basculer sur `/api/embed`, changer l'URL et adapter le
> parsing (`data["embeddings"][0]`) — le reste du code est inchangé.

`build_embed_fn(settings)` renvoie un `embed(text) -> list[float] | None`
**synchrone et exception-safe** : timeout court (≤10 s), tout échec (réseau,
timeout, statut ≠ 2xx, JSON invalide, `httpx` absent, champ `embedding`
manquant) ⇒ `None` ⇒ **aucun hit sémantique**. Aucune dépendance nouvelle
(`httpx` est déjà au projet ; cosinus en pur Python).

### 13.6. Câblage `main.py` (l'orchestrateur câble — ce module ne touche PAS `main.py`)

Le tier sémantique s'insère **après le miss exact, avant l'appel amont**. La
réponse servie reste le **body périmètre-déterministe post-garde-fous** (cf.
§11) : le filtre ACL par-document (`doc_acl.py`) est **toujours** ré-appliqué
par utilisateur en aval, hit sémantique compris — **mêmes garanties que le tier
exact**.

```python
# Au démarrage (lifespan), une fois par process :
app.state.cache    = build_cache(settings)                 # index sémantique inclus si opt-in
app.state.embed_fn = build_embed_fn(settings)              # client Ollama exception-safe

# Dans POST /v1/chat/send-message :
cache  = app.state.cache
norm_q = normalize_question(payload.get("message", ""))
ckey   = make_cache_key(settings=settings, principal=principal.user_id,
                        normalized_question=norm_q, authorized_doc_sets=authorized)
bypass = should_bypass(payload=payload, headers=request.headers,
                       is_admin=getattr(principal, "is_admin", False))

if cache and bypass is None:
    # 1) Tier EXACT d'abord (déterministe, sans réseau).
    hit = cache.lookup(ckey)                                # tier="exact"
    # 2) Tier SÉMANTIQUE en rattrapage (opt-in ; no-op si désactivé).
    if hit is None and settings.semantic_cache_enabled:
        perimeter = "\0".join(sorted({s for s in authorized if s}))   # = _perimeter_partition
        hit = cache.semantic_lookup(perimeter, norm_q, app.state.embed_fn)  # tier="semantic"
    if hit is not None:
        log_access_decision(actor=principal.user_id, decision="allow",
                            reason="cache_hit", endpoint="chat/send-message",
                            authorized_sets=authorized)
        add_cache_tokens_saved(estimate_tokens(hit))
        return JSONResponse(content=hit, status_code=200)   # ACL par-doc appliqué en aval
elif bypass:
    inc_cache_bypassed(bypass)

# ... appel Onyx + post_filter (inchangé) ...

# APRÈS le post-filtre, sur 2xx, et SEULEMENT si bypass=None :
if cache and bypass is None and 200 <= resp.status_code < 300:
    perimeter = "\0".join(sorted({s for s in authorized if s}))
    cache.store(ckey, body, ttl=settings.cache_ttl_seconds,
                # Indexation sémantique best-effort (no-op si tier désactivé) :
                perimeter=perimeter, normalized_question=norm_q,
                embed_fn=app.state.embed_fn)
```

> `cache.store(...)` reste rétro-compatible : les 3 kwargs sémantiques
> (`perimeter`, `normalized_question`, `embed_fn`) sont **optionnels**. Sans
> eux, ou si le tier est désactivé, l'indexation est un no-op silencieux et le
> cache exact fonctionne exactement comme avant.

### 13.7. Limites honnêtes du tier sémantique

* **Le garde n'est pas une compréhension sémantique.** Il compare des
  **marqueurs de surface** (chiffres, entités MAJUSCULES, quotes). Il ne
  détecte PAS une divergence portée par des **mots-outils en minuscules** :
  « congés **payés** » vs « congés **sans solde** », « **avant** 2024 » vs
  « **après** 2024 » (le `2024` est commun ; `avant`/`après` ne sont pas des
  marqueurs factuels). **Mitigation** : le seuil cosinus élevé (0.95) écarte
  déjà la plupart de ces cas (les embeddings divergent), et le tier est
  **opt-in**. Pour un corpus où ces nuances sont critiques, **laisser le tier
  désactivé** et s'appuyer sur le tier exact.
* **Entités en minuscules non capturées.** « client acme » (minuscules) n'est
  PAS vu comme une entité (seules MAJUSCULES/quotes le sont). Encourager les
  libellés d'entités en capitales ou entre guillemets dans le corpus si on veut
  la protection maximale ; sinon le seuil reste le dernier rempart.
* **Faux divergents possibles.** Une reformulation qui change un nombre
  cosmétique (« top 5 » vs « top 10 » sur une question dont la réponse ne
  dépend pas du nombre) sera rejetée ⇒ un miss (recalcul). C'est le compromis
  assumé : **on penche toujours vers le miss sûr.**
* **Coût d'un embedding par requête cachable.** Chaque lookup sémantique ajoute
  un appel `/api/embeddings` (latence réseau locale + inférence). Sur un LLM
  CPU, c'est négligeable devant une génération complète, mais **non nul** : le
  gain net dépend du taux de reformulations réel. Mesurer
  `hits{tier="semantic"}` vs le surcoût avant de généraliser.
* **Index en mémoire, non partagé.** Comme `InMemoryBackend`, l'index
  sémantique n'est pas partagé entre workers/réplicas. En HA, son hit-rate
  dépend de l'affinité de session (pas de partage Redis de l'index dans cette
  version — limite assumée ; le tier exact, lui, peut s'appuyer sur Redis).
* **Modèle d'embedding déterministe requis.** Si le modèle renvoie des
  embeddings non déterministes, deux embeddings de la **même** question peuvent
  passer sous le seuil ⇒ hit-rate dégradé (jamais un faux positif, en
  revanche).

### 13.8. Recommandation de déploiement

Activer le tier sémantique **seulement** après avoir :
1. mesuré un volume réel de reformulations (questions sémantiquement proches
   mais lexicalement distinctes) qui justifie le surcoût d'embedding ;
2. validé sur un échantillon métier que le garde anti-divergence couvre les
   faits saillants de **votre** corpus (les entités y sont-elles en MAJUSCULES /
   entre guillemets ? les nombres y sont-ils discriminants ?) ;
3. mis en place l'alerting sur
   `onix_gateway_cache_semantic_rejected_divergence_total` (sa croissance est
   **saine** : c'est le garde qui protège).

En cas de doute sur un corpus très factuel et sensible : **laisser désactivé**.
Le tier exact, lui, est sûr en toutes circonstances.
