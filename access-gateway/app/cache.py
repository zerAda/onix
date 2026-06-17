"""cache — couche de cache applicative RBAC-safe au-dessus d'Onyx/Ollama.

POURQUOI :
  Ollama dispose déjà d'un **KV-cache token-level** *interne* au modèle (réutilise
  des activations entre tokens d'une même séquence). Ce module est la couche
  **supérieure, déterministe, applicative** : on cache la **RÉPONSE COMPLÈTE
  POST-FILTRÉE** (JSON Onyx, après le post-filtre garde-fous) pour qu'une question
  identique posée dans le même périmètre RBAC ne fasse plus aucun aller-retour
  vers Onyx ni vers le LLM. Effet attendu : baisse drastique du coût en tokens et
  de la latence P95 sur le « tail » des questions répétées (FAQ implicites,
  reformulations identiques, requêtes scriptées d'outillage).

GARANTIES RBAC :
  La clé de cache inclut **le périmètre Document Set autorisé** trié (et un sel
  HMAC serveur) : un utilisateur dont le périmètre diffère, même d'un seul set,
  produit une clé DIFFÉRENTE. Conséquence : User A NE PEUT PAS recevoir la
  réponse cachée d'User B s'ils n'ont pas exactement le même périmètre. La preuve
  est dans ``make_cache_key`` (test : ``test_cache.py::test_key_isolation_*``).

CONTRATS :
  * `Cache.lookup` / `Cache.store` sont **exception-safe** : un défaut Redis
    (timeout, connexion refusée, OOM, parse error) est logué une fois et
    transformé en *miss* — JAMAIS en 5xx pour l'utilisateur. C'est un cache, pas
    une source d'autorité.
  * Le secret HMAC (``GATEWAY_CACHE_HMAC_SECRET``) est **REQUIS** quand le cache
    est activé : ``build_cache`` lève RuntimeError à l'init. Il n'est jamais
    autogénéré (un cache HMAC avec sel éphémère perdrait l'isolation entre
    redémarrages) et JAMAIS journalisé (cf. ``__repr__``).

EXEMPLE D'INTÉGRATION (à coller dans ``main.py``, 6 lignes — l'orchestrateur s'en
charge ; ce module n'importe PAS FastAPI pour rester pur) :

    # Avant l'appel httpx :
    _cache_key = make_cache_key(settings=settings, principal=principal.user_id,
                                normalized_question=normalize_question(payload.get("message","")),
                                authorized_doc_sets=authorized)
    _bypass = should_bypass(payload=payload, headers=request.headers)
    if cache and not _bypass:
        hit = cache.lookup(_cache_key)
        if hit is not None:
            return JSONResponse(content=hit, status_code=200)
    # ... appel Onyx + post_filter ...
    if cache and not _bypass and 200 <= resp.status_code < 300:
        cache.store(_cache_key, body, ttl=settings.cache_ttl_seconds)

LIMITES HONNÊTES :
  * `InMemoryBackend` n'est PAS partagé entre workers/réplicas. En HA, brancher
    Redis (``GATEWAY_CACHE_REDIS_URL``) — sans quoi le hit-rate s'effondre dès
    qu'on monte les workers.
  * Pas (encore) de tier sémantique : on cache la question NORMALISÉE exacte.
    L'étiquette ``tier`` est déjà câblée côté métrique pour préparer un futur
    cache approché (embedding + seuil de similarité).
  * `estimate_tokens` est une APPROXIMATION (chars/4) : utile pour piloter les
    économies — pas pour la facturation comptable.
  * On NE cache PAS les flux streaming (``stream=True``) : la sémantique
    streaming/SSE est incompatible avec un body JSON intégral mis en cache.

100 % local/souverain : aucune télémétrie sortante. Backend stdlib + redis-py.
"""
from __future__ import annotations

import abc
import collections
import hashlib
import hmac
import json
import logging
import threading
import time as _time
from typing import Any, Callable, Dict, Mapping, Optional

_logger = logging.getLogger("onix.gateway")


# ─────────────────────────────────────────────────────────────────────────────
# Backends : abstraction + 2 implémentations (in-memory et Redis).
# ─────────────────────────────────────────────────────────────────────────────
class CacheBackend(abc.ABC):
    """Interface bytes-in / bytes-out (le wrapper `Cache` gère le JSON)."""

    @abc.abstractmethod
    def get(self, key: str) -> Optional[bytes]:
        """Renvoie la valeur (bytes) ou ``None`` si absent/expiré/erreur."""

    @abc.abstractmethod
    def set(self, key: str, value: bytes, ttl: int) -> None:
        """Pose `value` sous `key` avec un TTL (secondes). ``ttl<=0`` = pas
        d'expiration (déconseillé pour un cache de réponses RAG)."""

    @abc.abstractmethod
    def close(self) -> None:
        """Libère les ressources (connexions, fichiers, …). Idempotent."""


class InMemoryBackend(CacheBackend):
    """LRU borné, thread-safe. Sert :
       * de fallback quand aucune URL Redis n'est configurée (mono-worker / dev) ;
       * de backend déterministe pour les tests (pas de réseau).

    Concurrence : un seul ``threading.Lock`` protège la LRU. Suffisant pour un
    workload de gateway (lecture/écriture brèves, dominées par le réseau amont).
    Pour une charge très soutenue, brancher Redis.
    """

    def __init__(self, *, max_entries: int = 512, time_func: Callable[[], float] = _time.time) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries doit être > 0")
        self._max = max_entries
        self._now = time_func
        # OrderedDict pour une LRU O(1) (move_to_end + popitem(last=False)).
        self._store: "collections.OrderedDict[str, tuple[bytes, float]]" = collections.OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[bytes]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at and self._now() >= expires_at:
                # Expiré : on évacue pour ne pas retenir la mémoire.
                self._store.pop(key, None)
                return None
            # Lecture = "récemment utilisé" → on remonte en tête.
            self._store.move_to_end(key)
            return value

    def set(self, key: str, value: bytes, ttl: int) -> None:
        expires_at = (self._now() + ttl) if ttl and ttl > 0 else 0.0
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (value, expires_at)
            # Éviction LRU : on sort le plus ancien tant qu'on dépasse la borne.
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    def close(self) -> None:  # pragma: no cover — rien à libérer.
        with self._lock:
            self._store.clear()

    # Pour les tests : taille courante (NE PAS utiliser pour la logique métier).
    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


class RedisBackend(CacheBackend):
    """Wrapper minimal autour de ``redis.Redis``.

    Politique de dégradation : toute exception côté Redis (réseau, parse,
    auth…) est attrapée, logguée UNE FOIS par opération, et convertie en
    *miss* / *no-op*. Le cache n'est jamais une source d'autorité ; sa panne ne
    doit JAMAIS dégrader la sécurité ni faire échouer une requête utilisateur.

    On NE veut PAS rendre la lib `redis` obligatoire (les tests doivent rester
    offline). L'import est donc paresseux et l'absence du module est traitée
    comme une erreur de configuration au moment du `build_cache` (jamais ici).
    """

    def __init__(self, url: str, *, socket_timeout: float = 0.5, on_error: Optional[Callable[[str, Exception], None]] = None) -> None:
        try:
            import redis as _redis  # type: ignore
        except Exception as exc:  # pragma: no cover — vérifié à l'init
            raise RuntimeError(
                "Le module `redis` n'est pas installé ; impossible de construire RedisBackend."
            ) from exc
        # `decode_responses=False` : on stocke des bytes (JSON encodé UTF-8) pour
        # éviter toute conversion implicite côté driver (idempotence stricte).
        self._client = _redis.Redis.from_url(
            url, socket_timeout=socket_timeout, socket_connect_timeout=socket_timeout,
            decode_responses=False, retry_on_timeout=False,
        )
        # Callback de notification (utilisée par `Cache` pour incrémenter les
        # métriques d'erreur). Optionnel pour ne pas coupler les briques.
        self._on_error = on_error
        # Évite de spammer les logs (un avertissement par opération mortelle).
        self._warned: Dict[str, bool] = {"get": False, "set": False, "ping": False}

    def _notify(self, op: str, exc: Exception) -> None:
        if not self._warned.get(op):
            _logger.warning("cache Redis %s en erreur : %s (suite ignorée)", op, type(exc).__name__)
            self._warned[op] = True
        if self._on_error is not None:
            try:
                self._on_error(op, exc)
            except Exception:  # pragma: no cover — l'observabilité ne casse rien
                pass

    def get(self, key: str) -> Optional[bytes]:
        try:
            value = self._client.get(key)
        except Exception as exc:
            self._notify("get", exc)
            return None
        if value is None:
            return None
        if isinstance(value, str):  # decode_responses=True chez un client custom
            return value.encode("utf-8")
        return value

    def set(self, key: str, value: bytes, ttl: int) -> None:
        try:
            if ttl and ttl > 0:
                self._client.set(key, value, ex=ttl)
            else:
                self._client.set(key, value)
        except Exception as exc:
            self._notify("set", exc)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # pragma: no cover — clôture best-effort
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation et composition de clé.
# ─────────────────────────────────────────────────────────────────────────────
def normalize_question(text: str) -> str:
    """Normalisation déterministe : lowercase + collapse des espaces.

    On RESTE conservateur : pas de dé-accentuation, pas de stemming. Le but ici
    est de capturer les répétitions « exactes au formattage près » (espaces
    multiples, retour chariot, casse) — pas la similarité sémantique (futur tier
    « semantic »). Garder la normalisation simple = garder la promesse RBAC :
    une variation de casse n'introduit pas d'ambiguïté de périmètre.
    """
    if not text:
        return ""
    return " ".join(text.lower().split())


def _canonical_extras(extras: Optional[Mapping[str, Any]]) -> bytes:
    """Sérialise un dict d'extras en JSON canonique (clés triées, séparateurs
    compacts, ensure_ascii=True). Garantit que deux dicts logiquement égaux
    produisent les MÊMES octets — donc la même clé."""
    if not extras:
        return b"{}"
    try:
        return json.dumps(
            extras, sort_keys=True, ensure_ascii=True, separators=(",", ":")
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        # Un dict non-sérialisable est un bug d'appelant : on échoue tôt et fort.
        raise ValueError(f"extras non-JSON-sérialisable : {exc}") from exc


# Version du schéma de clé. À incrémenter à chaque changement de COMPOSITION
# (jamais à chaque évolution de l'algo HMAC : on rotate le secret pour cela).
# Indispensable pour invalider proprement un cache existant lors d'un upgrade.
KEY_SCHEMA_VERSION = b"v1"


def make_cache_key(
    *,
    settings: Any,
    principal: Optional[str],
    normalized_question: str,
    authorized_doc_sets: list[str],
    extras: Optional[Mapping[str, Any]] = None,
) -> str:
    """HMAC-SHA256 hex sur un blob CANONIQUE.

    Composition (séparateur ``\\0`` qui ne peut pas apparaître dans les champs) :

        v1 \\0 sorted(authorized_doc_sets) joined by ',' \\0 locale \\0 normalized_question \\0 canonical_extras_json

    Pourquoi le principal n'apparaît PAS dans la clé :
      Le périmètre RBAC EFFECTIF est `authorized_doc_sets` (sorted). Deux
      utilisateurs au MÊME périmètre verront — par construction — la même
      réponse Onyx (le filtre amont est calculé à partir de ce périmètre).
      Inclure l'identité dans la clé empêcherait toute mutualisation entre
      utilisateurs partageant les mêmes accès — sans gain de sécurité. Le
      `principal` n'est utilisé qu'en logs/audit (déjà pseudonymisé).

    Garanties prouvées par les tests :
      - Idempotence : deux appels avec entrées strictement égales → clé identique.
      - Isolation RBAC : si `authorized_doc_sets` diffère (même question, même
        principal), la clé DIFFÈRE → un utilisateur ne peut pas servir le cache
        d'un autre périmètre.
      - Sensibilité à la locale et aux extras (utile pour modèles/personas).
    """
    if not getattr(settings, "cache_hmac_secret", "") or not isinstance(settings.cache_hmac_secret, str):
        # Filet de sécurité : si on arrive ici sans secret, c'est un bug d'init
        # (build_cache aurait dû lever). On refuse explicitement.
        raise RuntimeError(
            "GATEWAY_CACHE_HMAC_SECRET manquant : impossible de calculer une clé HMAC."
        )

    locale = (getattr(settings, "cache_locale", "fr") or "fr").lower().encode("ascii", errors="replace")
    # Tri stable + dédoublonnage : la clé NE doit PAS dépendre de l'ordre
    # d'évaluation des groupes par `mapping.authorized_document_sets`.
    sets_sorted = sorted({s for s in authorized_doc_sets if s})
    sets_blob = ",".join(sets_sorted).encode("utf-8")
    question_blob = (normalized_question or "").encode("utf-8")
    extras_blob = _canonical_extras(extras)

    blob = b"\0".join([KEY_SCHEMA_VERSION, sets_blob, locale, question_blob, extras_blob])
    secret = settings.cache_hmac_secret.encode("utf-8")
    return hmac.new(secret, blob, hashlib.sha256).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Politique de contournement (bypass).
# ─────────────────────────────────────────────────────────────────────────────
def should_bypass(
    *,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    is_admin: bool = False,
) -> Optional[str]:
    """Renvoie une RAISON de bypass (str) ou ``None`` si la requête est cachable.

    Les raisons retournées correspondent EXACTEMENT aux valeurs du label
    `reason` de la métrique ``onix_gateway_cache_bypassed_total`` :

      * ``no_store``              : header ``Cache-Control: no-store`` présent.
      * ``write_intent``          : la question porte une intention d'écriture
                                    (réutilise `guardrail.is_write_request`).
      * ``streaming``             : ``payload.stream is True`` — incompatible
                                    avec un body JSON intégral en cache.
      * ``explicit_admin_bypass`` : header ``X-Onix-Cache: bypass`` posé par un
                                    appelant admin (debug/diagnostic). Refusé
                                    pour les non-admins (header ignoré).

    L'ordre est délibéré : la directive cliente explicite (``Cache-Control``)
    gagne sur tout le reste — c'est le contrat HTTP standard.
    """
    # 1) Directive HTTP standard.
    cache_control = (headers.get("cache-control") or headers.get("Cache-Control") or "").lower()
    if "no-store" in cache_control:
        return "no_store"

    # 2) Stream demandé explicitement : on ne tente pas de capturer un SSE.
    if isinstance(payload, Mapping) and payload.get("stream") is True:
        return "streaming"

    # 3) Bypass admin explicite (debug). Refusé pour les non-admins.
    if is_admin:
        x_cache = (headers.get("x-onix-cache") or headers.get("X-Onix-Cache") or "").lower()
        if x_cache == "bypass":
            return "explicit_admin_bypass"

    # 4) Intention d'écriture côté requête : on refuse de cacher (et de servir).
    #    Import paresseux pour éviter une dépendance circulaire si guardrail
    #    est restructuré.
    try:
        from .guardrail import is_write_request  # type: ignore
    except Exception:  # pragma: no cover — fallback inerte
        is_write_request = lambda _q: False  # noqa: E731
    message = payload.get("message", "") if isinstance(payload, Mapping) else ""
    if isinstance(message, str) and is_write_request(message):
        return "write_intent"

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Estimation de tokens (heuristique transparente).
# ─────────────────────────────────────────────────────────────────────────────
def estimate_tokens(body: Mapping[str, Any]) -> int:
    """Estime ~tokens du corps de la réponse pour piloter le compteur
    « tokens économisés ».

    APPROXIMATION ASSUMÉE : ``chars / 4`` sur le texte d'assistant extrait.
    Cette heuristique est cohérente avec les ratios moyens FR/EN pour les
    tokenizers byte-pair modernes (~3–5 caractères/token). Elle SUFFIT pour
    suivre une tendance (« on a évité X k tokens cette heure ») mais n'est PAS
    une mesure comptable (pour facturation, intégrer le tokenizer réel d'Ollama).
    """
    try:
        from .onyx_proxy import extract_answer  # type: ignore
        answer, _ = extract_answer(body)
    except Exception:  # pragma: no cover — fallback inerte
        answer = ""
    if not answer:
        # Fallback : longueur du JSON sérialisé (ordre de grandeur).
        try:
            answer = json.dumps(body, ensure_ascii=False)
        except Exception:
            return 0
    return max(0, len(answer) // 4)


# ─────────────────────────────────────────────────────────────────────────────
# Façade : `Cache`.
# ─────────────────────────────────────────────────────────────────────────────
class Cache:
    """Façade au-dessus d'un `CacheBackend`.

    Responsabilités :
      * encodage/décodage JSON (utf-8) en bytes ;
      * gestion exception-safe (un défaut backend → miss / no-op) ;
      * point d'attache pour la télémétrie (callbacks fournis par `metrics`).

    Le secret HMAC et la sélection du backend sont laissés à ``build_cache``.
    """

    def __init__(
        self,
        backend: CacheBackend,
        *,
        on_hit: Optional[Callable[[str], None]] = None,
        on_miss: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str, Exception], None]] = None,
    ) -> None:
        self._backend = backend
        self._on_hit = on_hit
        self._on_miss = on_miss
        self._on_error = on_error

    def lookup(self, key: str, *, tier: str = "exact") -> Optional[dict]:
        """Renvoie le body JSON (dict) ou ``None`` si miss.

        Exception-safe : tout défaut (réseau, parse, encodage) est logué + notifié
        + traduit en miss. Conséquence opérationnelle : le cache peut tomber sans
        que la passerelle perde la main."""
        try:
            raw = self._backend.get(key)
        except Exception as exc:
            self._notify_error("get", exc)
            self._notify_miss()
            return None
        if raw is None:
            self._notify_miss()
            return None
        try:
            value = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            # Entrée corrompue : on ne sait pas la valider, on rate le hit.
            self._notify_error("get", exc)
            self._notify_miss()
            return None
        self._notify_hit(tier)
        return value if isinstance(value, dict) else None

    def store(self, key: str, body: Mapping[str, Any], ttl: Optional[int] = None) -> None:
        """Persiste `body` (dict JSON-sérialisable) dans le backend.

        Exception-safe : un échec d'encodage ou de set est logué + ignoré (no-op).
        `ttl` None → pas d'expiration explicite (le backend décide ; en pratique
        on passe toujours ``settings.cache_ttl_seconds``)."""
        try:
            raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        except Exception as exc:
            self._notify_error("set", exc)
            return
        try:
            self._backend.set(key, raw, ttl if ttl is not None else 0)
        except Exception as exc:
            self._notify_error("set", exc)

    def close(self) -> None:
        try:
            self._backend.close()
        except Exception:  # pragma: no cover — clôture best-effort
            pass

    # ── Notifications internes (centralisées pour faciliter les tests). ─────
    def _notify_hit(self, tier: str) -> None:
        if self._on_hit is not None:
            try:
                self._on_hit(tier)
            except Exception:  # pragma: no cover
                pass

    def _notify_miss(self) -> None:
        if self._on_miss is not None:
            try:
                self._on_miss()
            except Exception:  # pragma: no cover
                pass

    def _notify_error(self, op: str, exc: Exception) -> None:
        if self._on_error is not None:
            try:
                self._on_error(op, exc)
            except Exception:  # pragma: no cover
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Fabrique : choisit le backend selon `Settings`.
# ─────────────────────────────────────────────────────────────────────────────
def build_cache(settings: Any) -> Optional[Cache]:
    """Construit l'objet `Cache` selon les réglages.

    * `cache_enabled=False` → renvoie ``None`` (le caller doit traiter ce cas).
    * `cache_enabled=True` + `cache_hmac_secret` vide → **fail-loud** :
      RuntimeError. On REFUSE de démarrer un cache HMAC sans secret stable.
    * `cache_redis_url` non vide → `RedisBackend` (avec timeouts courts).
    * Sinon → `InMemoryBackend` (LRU bornée).

    Câble également les callbacks de métriques (exception-safe ; un défaut
    Prometheus ne casse pas le cache et inversement)."""
    if not getattr(settings, "cache_enabled", False):
        return None
    secret = (getattr(settings, "cache_hmac_secret", "") or "").strip()
    if not secret:
        raise RuntimeError(
            "GATEWAY_CACHE_HMAC_SECRET est requis lorsque GATEWAY_CACHE_ENABLED=true. "
            "Définissez un secret stable (>= 32 octets aléatoires) via votre coffre."
        )

    # Sélection du backend (Redis prioritaire si configuré).
    url = (getattr(settings, "cache_redis_url", "") or "").strip()
    backend: CacheBackend
    if url:
        from . import metrics as _m  # import paresseux : évite l'import au boot si métriques off
        backend = RedisBackend(
            url,
            socket_timeout=0.5,
            on_error=lambda op, _exc, _mm=_m: _mm.inc_cache_error(op),
        )
    else:
        backend = InMemoryBackend(max_entries=int(getattr(settings, "cache_max_entries", 512)))

    # Branchement métrique (toujours via les helpers exception-safe de `metrics`).
    from . import metrics as _m  # idem
    return Cache(
        backend,
        on_hit=lambda tier: _m.inc_cache_hit(tier),
        on_miss=lambda: _m.inc_cache_miss(),
        on_error=lambda op, _exc: _m.inc_cache_error(op),
    )
