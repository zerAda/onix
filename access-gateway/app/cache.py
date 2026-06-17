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
  * Tier sémantique (embedding + seuil cosinus) DISPONIBLE mais **opt-in**
    (``GATEWAY_SEMANTIC_CACHE_ENABLED=false`` par défaut). Il capture les
    REFORMULATIONS d'une question déjà répondue DANS LE MÊME PÉRIMÈTRE RBAC.
    Deux garde-fous le rendent sûr sur du factuel : (1) l'index est
    **partitionné PAR PÉRIMÈTRE** → un match cross-périmètre est
    structurellement impossible ; (2) un **garde anti-divergence** refuse tout
    candidat qui diffère sur un nombre/date/montant/% ou une entité saillante
    (cf. ``SemanticIndex`` et ``_has_factual_divergence``, docs/CACHE.md §13).
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
import math
import re
import threading
import time as _time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

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
# Tier SÉMANTIQUE — partition par périmètre + garde anti-divergence factuelle.
#
# PHILOSOPHIE DE SÛRETÉ (lire avant de toucher) :
#   Un cache sémantique sur un corpus FACTUEL est une RESPONSABILITÉ s'il est
#   naïf : servir la réponse de la question A pour une question B « proche »
#   est une erreur silencieuse de justesse. On le rend sûr par TROIS couches :
#     1. PARTITION PAR PÉRIMÈTRE : un voisin n'est cherché QUE dans la
#        partition du périmètre RBAC exact de la requête → un match
#        cross-périmètre est STRUCTURELLEMENT impossible (pas une condition
#        applicative qu'on pourrait oublier : il n'y a aucun voisin à trouver
#        ailleurs). Prouvé par test_cache_semantic.
#     2. SEUIL COSINUS ÉLEVÉ (0.95 par défaut) : on préfère un miss (recalcul)
#        à un faux positif. En-dessous → miss net.
#     3. GARDE ANTI-DIVERGENCE : même AU-DESSUS du seuil, on REFUSE le match si
#        la requête et le candidat diffèrent sur un NOMBRE / DATE / ANNÉE /
#        MONTANT / POURCENTAGE, ou sur une ENTITÉ saillante (token MAJUSCULE
#        de ≥2 lettres, ou segment entre guillemets). « CA 2024 » vs
#        « CA 2025 », « client ALPHA » vs « client BETA » NE matchent JAMAIS.
# ─────────────────────────────────────────────────────────────────────────────

# Le séparateur \0 ne peut pas apparaître dans un nom de Document Set : on
# l'utilise pour fabriquer une clé de partition lisible et sans collision à
# partir du périmètre trié+dédoublonné (MÊME définition de périmètre que
# make_cache_key → cohérence stricte entre tier exact et tier sémantique).
def _perimeter_partition(authorized_doc_sets: Sequence[str]) -> str:
    """Clé de partition = périmètre RBAC canonique (sets triés, dédoublonnés).

    C'est la FRONTIÈRE de sûreté du tier sémantique : deux requêtes ne peuvent
    partager une partition que si leur périmètre est *exactement* identique —
    la même invariante que la composante `authorized_doc_sets` de la clé HMAC
    exacte. Un périmètre vide a sa propre partition (cohérent avec la clé
    exacte, qui hash aussi un blob de sets vide)."""
    sets_sorted = sorted({s for s in authorized_doc_sets if s})
    return "\0".join(sets_sorted)


# Nombres/dates/montants/pourcentages : tout token contenant un chiffre. On
# capture aussi les variantes collées à un symbole (%, €, $, k€) et les dates
# (2024, 12/03/2025, 1.5, 1 200,50 → le séparateur d'espace est géré par split).
_NUMERIC_RE = re.compile(r"\d")
# Token « monétaire / pourcentage » : un symbole d'unité saillant.
_MONEY_PERCENT_RE = re.compile(r"[%€$£]|\b(?:eur|usd|gbp|k€|m€|md€|pourcent|pct)\b", re.IGNORECASE)
# Entités saillantes : segments entre guillemets droits/typographiques.
_QUOTED_RE = re.compile(r"[\"«»“”']([^\"«»“”']{1,64})[\"«»“”']")
# Token « entité MAJUSCULE » : ≥2 lettres, intégralement en capitales (gère les
# lettres accentuées FR via le flag UNICODE par défaut de `re` sur les str).
# Ex. ALPHA, BETA, SARL, EBITDA, CDI. On exige ≥2 pour éviter les initiales/A/I.
_UPPER_ENTITY_RE = re.compile(r"\b[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ0-9]{1,}\b")


def _extract_factual_tokens(text: str) -> frozenset[str]:
    """Extrait l'ENSEMBLE des marqueurs factuels saillants d'un texte.

    Sont considérés comme factuels et donc DISCRIMINANTS :
      * tout token contenant un chiffre (année, date, quantité, version, prix) ;
      * tout token portant un symbole monétaire ou de pourcentage ;
      * tout segment entre guillemets (noms propres cités, libellés exacts) ;
      * tout token entièrement EN MAJUSCULES de ≥2 caractères (acronymes, noms
        de clients/entités type ALPHA/BETA).

    Renvoie un `frozenset` normalisé (casse repliée pour les nombres/quotes ;
    les entités MAJUSCULES sont conservées en l'état car la casse EST le signal).

    NOTE : on est volontairement SUR-INCLUSIF (mieux vaut un faux « divergent »
    → un miss inoffensif, qu'un faux « identique » → une mauvaise réponse). La
    précision du cache sémantique est sacrifiée au profit de la JUSTESSE."""
    if not text:
        return frozenset()
    tokens: set[str] = set()

    # 1) Segments entre guillemets (avant le split, pour garder les espaces internes).
    for m in _QUOTED_RE.finditer(text):
        seg = m.group(1).strip().lower()
        if seg:
            tokens.add("q:" + " ".join(seg.split()))

    # 2) Découpage grossier en tokens pour le reste.
    for raw in text.split():
        # Nettoyage léger de la ponctuation de bord (garde % € $ internes).
        stripped = raw.strip(".,;:!?()[]{}…\"'«»“”")
        if not stripped:
            continue
        if _NUMERIC_RE.search(stripped):
            # Tout token numérique est un fait : on le garde tel quel (lowercase).
            tokens.add("n:" + stripped.lower())
        if _MONEY_PERCENT_RE.search(raw):
            tokens.add("m:" + raw.lower())

    # 3) Entités MAJUSCULES (sur le texte brut : la casse est le signal).
    for m in _UPPER_ENTITY_RE.finditer(text):
        tokens.add("e:" + m.group(0))

    return frozenset(tokens)


def _has_factual_divergence(query: str, candidate: str) -> bool:
    """True si `query` et `candidate` diffèrent sur AU MOINS un marqueur factuel.

    C'est le garde de SÛRETÉ : il transforme un voisin sémantique en match
    SEULEMENT si l'ensemble de ses faits saillants est identique. La moindre
    divergence (un nombre, une date, une entité en plus, en moins, ou changée)
    → True → on REFUSE le hit (fall-through vers un miss → recalcul correct).

    Symétrique par construction (différence symétrique d'ensembles non vide)."""
    q = _extract_factual_tokens(query)
    c = _extract_factual_tokens(candidate)
    return q != c


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Similarité cosinus en PUR PYTHON (pas de numpy : contrainte deps).

    Renvoie une valeur dans [-1, 1] ; 0.0 si l'une des normes est nulle ou si
    les longueurs diffèrent (vecteurs incomparables → traités comme non
    similaires, jamais comme une exception)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom <= 0.0:  # pragma: no cover — couvert par na/nb mais garde défensive
        return 0.0
    return dot / denom


class SemanticIndex:
    """Index d'embeddings PARTITIONNÉ PAR PÉRIMÈTRE, borné (LRU par partition).

    Structure : { perimeter_partition: OrderedDict[ exact_key -> (embedding,
    normalized_question) ] }. La recherche d'un voisin se fait UNIQUEMENT dans
    la partition du périmètre fourni — d'où l'impossibilité STRUCTURELLE d'un
    match cross-périmètre (RBAC-safe by construction, pas par condition).

    Chaque partition est bornée par `max_entries` (réutilise la borne LRU du
    cache exact) : on évince la plus ancienne entrée au-delà. Thread-safe via
    un unique `threading.Lock` (workload gateway : opérations brèves).

    On stocke la `normalized_question` à côté de l'embedding pour pouvoir
    appliquer le garde anti-divergence factuelle AU MOMENT de la recherche
    (sans relire le backend de valeurs)."""

    def __init__(self, *, max_entries: int = 512) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries doit être > 0")
        self._max = max_entries
        # perimeter -> OrderedDict[exact_key -> (embedding, normalized_question)]
        self._parts: Dict[str, "collections.OrderedDict[str, Tuple[List[float], str]]"] = {}
        self._lock = threading.Lock()

    def add(
        self,
        *,
        perimeter: str,
        exact_key: str,
        embedding: Sequence[float],
        normalized_question: str,
        divergence_text: Optional[str] = None,
    ) -> None:
        """Indexe (best-effort) l'embedding d'une question répondue.

        Idempotent sur `exact_key` au sein d'une partition (ré-indexer remonte
        l'entrée en tête LRU). Un embedding vide/non numérique est ignoré (on
        n'indexe pas du bruit qui ne matcherait jamais proprement).

        `divergence_text` (optionnel) = texte utilisé par le GARDE anti-divergence
        au moment de la recherche. On y stocke la question BRUTE (non normalisée)
        car la casse porte le signal d'entité (ALPHA vs BETA) : la normalisation
        lowercase tuerait la détection d'entités. Défaut = `normalized_question`
        (rétro-compatible : un appelant qui ne fournit que le normalisé garde
        l'ancien comportement)."""
        vec = _coerce_vector(embedding)
        if not vec:
            return
        with self._lock:
            part = self._parts.get(perimeter)
            if part is None:
                part = collections.OrderedDict()
                self._parts[perimeter] = part
            if exact_key in part:
                part.move_to_end(exact_key)
            part[exact_key] = (vec, divergence_text or normalized_question or "")
            while len(part) > self._max:
                part.popitem(last=False)

    def search(
        self,
        *,
        perimeter: str,
        embedding: Sequence[float],
        threshold: float,
        query_text: str,
        on_candidate: Optional[Callable[[], None]] = None,
        on_rejected_divergence: Optional[Callable[[], None]] = None,
    ) -> Optional[str]:
        """Renvoie l'`exact_key` du meilleur voisin SÛR, ou ``None``.

        Algorithme :
          1. on ne regarde QUE la partition `perimeter` (RBAC-safe) ;
          2. on calcule le cosinus vs chaque entrée, on garde le meilleur ;
          3. si le meilleur < `threshold` → ``None`` (miss net) ;
          4. sinon c'est un CANDIDAT (`on_candidate`) ; on applique le garde
             anti-divergence factuelle : s'il diverge → on l'écarte
             (`on_rejected_divergence`) et on continue avec les voisins
             suivants triés par similarité décroissante ;
          5. on renvoie le 1er candidat au-dessus du seuil ET sans divergence.

        Important : un rejet pour divergence N'EST PAS un fallback vers un
        voisin moins similaire mais divergent lui aussi — on ne renvoie un
        match QUE si (similarité ≥ seuil) ET (aucune divergence factuelle)."""
        query_vec = _coerce_vector(embedding)
        if not query_vec:
            return None
        with self._lock:
            part = self._parts.get(perimeter)
            if not part:
                return None
            # Snapshot (clé, sim, question) trié par similarité décroissante.
            scored: List[Tuple[float, str, str]] = []
            for key, (vec, nq) in part.items():
                scored.append((_cosine(query_vec, vec), key, nq))
        if not scored:
            return None
        scored.sort(key=lambda t: t[0], reverse=True)

        emitted_candidate = False
        for sim, key, cand_q in scored:
            if sim < threshold:
                break  # trié décroissant : plus rien au-dessus du seuil.
            # Au moins un voisin franchit le seuil : c'est un candidat sémantique.
            if not emitted_candidate:
                emitted_candidate = True
                if on_candidate is not None:
                    try:
                        on_candidate()
                    except Exception:  # pragma: no cover — observabilité inerte
                        pass
            # GARDE DE SÛRETÉ : divergence factuelle → on refuse CE voisin.
            if _has_factual_divergence(query_text, cand_q):
                if on_rejected_divergence is not None:
                    try:
                        on_rejected_divergence()
                    except Exception:  # pragma: no cover
                        pass
                continue
            return key  # voisin au-dessus du seuil ET factuel-compatible.
        return None

    # Diagnostic tests uniquement (NE PAS utiliser pour la logique métier).
    def _partition_size(self, perimeter: str) -> int:
        with self._lock:
            part = self._parts.get(perimeter)
            return len(part) if part else 0


def _coerce_vector(embedding: Sequence[float]) -> List[float]:
    """Convertit un embedding en list[float] propre, ou [] si non exploitable.

    Tolère les ints/str numériques (robustesse au JSON) ; toute valeur non
    convertible invalide TOUT le vecteur (on préfère ne pas indexer un vecteur
    partiel qui fausserait le cosinus)."""
    if not embedding or not isinstance(embedding, (list, tuple)):
        return []
    out: List[float] = []
    for v in embedding:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            return []
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Client d'embeddings Ollama (exception-safe) — endpoint legacy /api/embeddings.
#
# Schéma ASSUMÉ (confirmé via Context7 /ollama/ollama, docs/api.md) :
#   requête  : POST {url}  body JSON  { "model": <str>, "prompt": <str> }
#   réponse  : 200         body JSON  { "embedding": [<float>, ...] }
# (L'endpoint moderne /api/embed renvoie {"embeddings": [[...]]} pour un batch ;
#  on cible le legacy singulier, plus simple pour notre usage 1-question.)
#
# CONTRAT DUR : cette fonction NE LÈVE JAMAIS dans le chemin requête. Toute
# erreur (réseau, timeout, statut != 2xx, JSON invalide, modèle absent) →
# renvoie ``None`` → AUCUN hit sémantique (fall-through propre vers un miss).
# ─────────────────────────────────────────────────────────────────────────────
def build_embed_fn(settings: Any) -> Callable[[str], Optional[List[float]]]:
    """Fabrique une fonction ``embed(text) -> list[float] | None`` synchrone et
    exception-safe, branchée sur Ollama via httpx.

    On crée un client httpx par appel (timeout court) : simple, sans état
    partagé, suffisant pour un volume « tail » de requêtes cachables. Pour un
    débit élevé, l'orchestrateur peut injecter sa propre `embed_fn` réutilisant
    un client partagé — `semantic_lookup`/`store` acceptent n'importe quel
    callable respectant la même signature."""
    url = (getattr(settings, "semantic_embed_url", "") or "").strip()
    model = (getattr(settings, "semantic_embed_model", "") or "nomic-embed-text").strip()
    # Timeout court : l'embedding d'une question est rapide ; on ne veut PAS
    # rallonger la latence si Ollama est lent/indisponible (on retombe en miss).
    timeout = float(getattr(settings, "upstream_timeout", 30) or 30)
    timeout = min(timeout, 10.0)

    def _embed(text: str) -> Optional[List[float]]:
        if not text or not url:
            return None
        try:
            import httpx  # import paresseux : déjà une dép du projet.

            resp = httpx.post(
                url,
                json={"model": model, "prompt": text},
                timeout=httpx.Timeout(timeout),
            )
            if resp.status_code < 200 or resp.status_code >= 300:
                _logger.debug("embed: statut amont %s (→ pas de hit sémantique)", resp.status_code)
                return None
            data = resp.json()
        except Exception as exc:
            # Réseau, timeout, JSON invalide, httpx absent… → miss propre.
            _logger.debug("embed: échec (%s) → pas de hit sémantique", type(exc).__name__)
            return None
        vec = _coerce_vector(data.get("embedding") if isinstance(data, Mapping) else None)
        return vec or None

    return _embed


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
        semantic_index: Optional["SemanticIndex"] = None,
        semantic_threshold: float = 0.95,
        on_semantic_candidate: Optional[Callable[[], None]] = None,
        on_semantic_rejected: Optional[Callable[[], None]] = None,
    ) -> None:
        self._backend = backend
        self._on_hit = on_hit
        self._on_miss = on_miss
        self._on_error = on_error
        # Tier sémantique (optionnel) : None ⇒ `semantic_lookup` est un no-op
        # (toujours un miss). Activé par `build_cache` quand opt-in.
        self._semantic = semantic_index
        self._semantic_threshold = semantic_threshold
        self._on_semantic_candidate = on_semantic_candidate
        self._on_semantic_rejected = on_semantic_rejected

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

    def store(
        self,
        key: str,
        body: Mapping[str, Any],
        ttl: Optional[int] = None,
        *,
        perimeter: Optional[str] = None,
        normalized_question: Optional[str] = None,
        embed_fn: Optional[Callable[[str], Optional[Sequence[float]]]] = None,
        raw_question: Optional[str] = None,
    ) -> None:
        """Persiste `body` (dict JSON-sérialisable) dans le backend.

        Exception-safe : un échec d'encodage ou de set est logué + ignoré (no-op).
        `ttl` None → pas d'expiration explicite (le backend décide ; en pratique
        on passe toujours ``settings.cache_ttl_seconds``).

        Indexation sémantique (BEST-EFFORT, opt-in) : si un `semantic_index` est
        câblé ET que `perimeter` + `normalized_question` + `embed_fn` sont
        fournis, on calcule l'embedding de la question et on l'indexe DANS LA
        PARTITION DU PÉRIMÈTRE, associé à `key` (la clé exacte). Tout échec
        d'embedding est avalé (le store de la valeur reste prioritaire et
        réussi) : un défaut d'index sémantique ne casse JAMAIS le cache exact."""
        try:
            raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        except Exception as exc:
            self._notify_error("set", exc)
            return
        try:
            self._backend.set(key, raw, ttl if ttl is not None else 0)
        except Exception as exc:
            self._notify_error("set", exc)
        # Indexation sémantique best-effort (n'altère jamais le store ci-dessus).
        self.index_embedding(
            key, perimeter=perimeter, normalized_question=normalized_question,
            embed_fn=embed_fn, raw_question=raw_question,
        )

    def index_embedding(
        self,
        key: str,
        *,
        perimeter: Optional[str],
        normalized_question: Optional[str],
        embed_fn: Optional[Callable[[str], Optional[Sequence[float]]]],
        raw_question: Optional[str] = None,
    ) -> None:
        """Indexe (best-effort) l'embedding d'une question répondue.

        No-op silencieux si le tier sémantique est désactivé ou si un argument
        requis manque. EXCEPTION-SAFE de bout en bout : aucune erreur (embedding
        ou indexation) ne remonte — un cache sémantique défaillant doit dégrader
        en simple cache exact, jamais faire échouer une requête."""
        if self._semantic is None or not embed_fn or not perimeter or not normalized_question:
            return
        try:
            vec = embed_fn(normalized_question)
        except Exception:  # pragma: no cover — embed_fn est censé être safe
            vec = None
        if not vec:
            return
        try:
            self._semantic.add(
                perimeter=perimeter,
                exact_key=key,
                embedding=vec,
                normalized_question=normalized_question,
                divergence_text=raw_question,
            )
        except Exception:  # pragma: no cover — indexation best-effort
            pass

    def semantic_lookup(
        self,
        perimeter: str,
        question: str,
        embed_fn: Callable[[str], Optional[Sequence[float]]],
        *,
        raw_question: Optional[str] = None,
    ) -> Optional[dict]:
        """Recherche un voisin sémantique SÛR dans la partition `perimeter`.

        Retourne le body JSON (dict) du voisin si — et seulement si :
          * un index sémantique est câblé (sinon ``None``) ;
          * l'embedding de `question` est calculable (sinon miss GRACIEUX) ;
          * un voisin de la MÊME partition a une similarité ≥ seuil ;
          * ce voisin NE diverge PAS factuellement (nombres/dates/entités).

        Sur succès : émet ``inc_cache_hit("semantic")`` (via `on_hit`). Sur tout
        autre cas : ``None`` (l'appelant enchaîne sur l'appel amont). N'émet PAS
        de miss ici : la comptabilité miss/hit du tier exact reste la référence
        du hit-rate ; le tier sémantique est un *rattrapage* au-dessus.

        NE LÈVE JAMAIS : tout échec → ``None``."""
        if self._semantic is None:
            return None
        # `question` est déjà la question NORMALISÉE (cohérence avec le tier
        # exact et avec ce qui a été indexé). On embed dans cette forme.
        try:
            vec = embed_fn(question)
        except Exception:
            # embed_fn est censé être exception-safe ; double filet ici.
            vec = None
        if not vec:
            return None  # embed indisponible → miss gracieux (pas de hit).
        try:
            exact_key = self._semantic.search(
                perimeter=perimeter,
                embedding=vec,
                threshold=self._semantic_threshold,
                query_text=(raw_question or question),
                on_candidate=self._on_semantic_candidate,
                on_rejected_divergence=self._on_semantic_rejected,
            )
        except Exception:  # pragma: no cover — recherche best-effort
            return None
        if exact_key is None:
            return None
        # Voisin SÛR trouvé : on lit le body via le backend (tier="semantic").
        # On lit en direct pour ne PAS ré-incrémenter le compteur de miss du
        # backend si l'entrée a expiré entre-temps (course TTL) — dans ce cas
        # on renvoie proprement None.
        try:
            raw = self._backend.get(exact_key)
        except Exception as exc:
            self._notify_error("get", exc)
            return None
        if raw is None:
            # L'entrée de valeur a expiré mais l'index la pointait encore :
            # miss propre (l'index sera nettoyé naturellement par la LRU).
            return None
        try:
            value = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self._notify_error("get", exc)
            return None
        if not isinstance(value, dict):
            return None
        self._notify_hit("semantic")
        return value

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

    # Tier sémantique (opt-in). Désactivé par défaut : un cache approché sur du
    # factuel est un risque de précision (cf. docs/CACHE.md §13). Activé, il est
    # rendu sûr par la partition-par-périmètre + le garde anti-divergence.
    semantic_index: Optional[SemanticIndex] = None
    semantic_threshold = 0.95
    if getattr(settings, "semantic_cache_enabled", False):
        semantic_threshold = float(getattr(settings, "semantic_threshold", 0.95))
        semantic_index = SemanticIndex(
            max_entries=int(
                getattr(
                    settings,
                    "semantic_max_entries",
                    getattr(settings, "cache_max_entries", 512),
                )
            )
        )

    # Branchement métrique (toujours via les helpers exception-safe de `metrics`).
    from . import metrics as _m  # idem
    return Cache(
        backend,
        on_hit=lambda tier: _m.inc_cache_hit(tier),
        on_miss=lambda: _m.inc_cache_miss(),
        on_error=lambda op, _exc: _m.inc_cache_error(op),
        semantic_index=semantic_index,
        semantic_threshold=semantic_threshold,
        on_semantic_candidate=lambda: _m.inc_cache_semantic_candidate(),
        on_semantic_rejected=lambda: _m.inc_cache_semantic_rejected_divergence(),
    )
