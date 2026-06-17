"""metrics — observabilité Prometheus pour la passerelle RBAC onix.

Expose des compteurs et histogrammes (préfixés ``onix_gateway_``) sur le
chemin réel de la requête : RBAC, garde-fous, citations, latences, erreurs
amont. Toutes les primitives sont **exception-safe** : un défaut Prometheus
ne doit JAMAIS modifier le comportement HTTP de la passerelle.

Configuration :
  GATEWAY_METRICS_ENABLED  (bool, défaut : true) — quand false, aucun
  compteur n'est incrémenté et GET /metrics renvoie 404.

Modèle multi-worker :
  En mode uvicorn multi-worker (``--workers N``), chaque processus dispose de
  son propre registre mémoire. Le préfixe ``prometheus_multiprocess_dir``
  (variable d'env ``PROMETHEUS_MULTIPROC_DIR``) permet à prometheus-client de
  persister les métriques sur disque et d'agréger les valeurs de tous les
  workers à chaque scrape — voir docs/OBSERVABILITY.md §multiprocess. En mode
  single-worker (défaut en dev et conteneur standard), aucune configuration
  supplémentaire n'est nécessaire.
"""
from __future__ import annotations

import logging

_logger = logging.getLogger("onix.gateway")

# ─────────────────────────────────────────────────────────────────────────────
# Définitions des métriques (une seule fois au niveau module).
# ─────────────────────────────────────────────────────────────────────────────
try:
    from prometheus_client import Counter, Histogram

    # Requêtes totales par endpoint et décision (allow / deny).
    REQUESTS_TOTAL = Counter(
        "onix_gateway_requests_total",
        "Nombre total de requêtes traitées par la passerelle",
        ["endpoint", "decision"],
    )

    # Déclenchements du garde-fous : règle et blocage (true / false).
    GUARDRAIL_TOTAL = Counter(
        "onix_gateway_guardrail_total",
        "Passages dans le post-filtre garde-fous (par règle et statut de blocage)",
        ["rule", "blocked"],
    )

    # Réponse sans contexte documentaire reconstruit.
    ANSWER_NO_CONTEXT_TOTAL = Counter(
        "onix_gateway_answer_no_context_total",
        "Réponses Onyx 2xx dont le contexte documentaire reconstruit est vide",
    )

    # Présence de citation dans la réponse FINALE (après post-filtre éventuel).
    ANSWER_WITH_CITATION_TOTAL = Counter(
        "onix_gateway_answer_with_citation_total",
        "Réponses FINALES (post-filtre) comportant au moins une citation de source",
    )
    ANSWER_WITHOUT_CITATION_TOTAL = Counter(
        "onix_gateway_answer_without_citation_total",
        "Réponses FINALES (post-filtre) sans aucune citation de source",
    )

    # Latence bout-en-bout (appel amont + post-filtre) en secondes.
    # Buckets adaptés aux délais d'un LLM local (génération lente possible).
    REQUEST_LATENCY_SECONDS = Histogram(
        "onix_gateway_request_latency_seconds",
        "Latence bout-en-bout de l'appel amont + post-filtre (secondes)",
        buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120),
    )

    # Erreurs de relais vers Onyx (timeout, connexion refusée, etc.) → 502.
    UPSTREAM_ERRORS_TOTAL = Counter(
        "onix_gateway_upstream_errors_total",
        "Erreurs de relais HTTP vers l'amont Onyx (→ 502)",
    )

    # Retours utilisateur (feedback optionnel).
    FEEDBACK_TOTAL = Counter(
        "onix_gateway_feedback_total",
        "Retours utilisateur sur les réponses (up / down)",
        ["rating"],
    )

    # ── Cache applicatif (RBAC-safe, cf. app/cache.py + docs/CACHE.md) ──
    # Le label `tier` est déjà câblé : aujourd'hui seul `exact` est émis
    # (correspondance question normalisée + périmètre identique). Tier
    # `semantic` est un futur cache approché (embedding + seuil de
    # similarité) ; déclarer l'étiquette dès maintenant évite toute
    # rupture de série temporelle quand il sera activé.
    CACHE_HITS_TOTAL = Counter(
        "onix_gateway_cache_hits_total",
        "Hits du cache applicatif de réponses (par tier de correspondance)",
        ["tier"],
    )
    CACHE_MISSES_TOTAL = Counter(
        "onix_gateway_cache_misses_total",
        "Misses du cache applicatif (entrée absente ou expirée)",
    )
    # `reason` ∈ no_store|write_intent|streaming|explicit_admin_bypass — cf.
    # cache.should_bypass. Permet de séparer le cache « éteint volontairement »
    # des miss naturels (utile pour mesurer le hit-rate VRAI).
    CACHE_BYPASSED_TOTAL = Counter(
        "onix_gateway_cache_bypassed_total",
        "Requêtes pour lesquelles le cache a été contourné (par raison)",
        ["reason"],
    )
    CACHE_TOKENS_SAVED_TOTAL = Counter(
        "onix_gateway_cache_tokens_saved_total",
        "Tokens approximatifs économisés par les hits (heuristique chars/4)",
    )
    CACHE_SECONDS_SAVED_TOTAL = Counter(
        "onix_gateway_cache_seconds_saved_total",
        "Secondes de génération économisées par les hits (heuristique constante)",
    )
    # `op` ∈ get|set — distingue les erreurs de lookup et de store côté backend.
    CACHE_ERRORS_TOTAL = Counter(
        "onix_gateway_cache_errors_total",
        "Erreurs du backend de cache (get / set), exception-safe → miss ou no-op",
        ["op"],
    )

    # ── Tier SÉMANTIQUE (embedding + seuil, cf. app/cache.py + docs/CACHE.md §13) ──
    # Candidats sémantiques examinés : on a calculé un embedding de la requête
    # et trouvé au MOINS un voisin (dans le MÊME périmètre) au-dessus du seuil
    # cosinus. Compteur des « presque-hits » avant le garde anti-divergence.
    CACHE_SEMANTIC_CANDIDATES_TOTAL = Counter(
        "onix_gateway_cache_semantic_candidates_total",
        "Candidats sémantiques au-dessus du seuil cosinus (avant garde divergence)",
    )
    # Candidats REJETÉS par le garde factuel : la requête et le candidat
    # divergent sur un NOMBRE/DATE/MONTANT/% ou une ENTITÉ saillante (MAJUSCULES
    # ou guillemets). C'est le rejet qui rend le cache sémantique SÛR sur du
    # factuel (ex. « CA 2024 » ≠ « CA 2025 », « client ALPHA » ≠ « client BETA »).
    CACHE_SEMANTIC_REJECTED_DIVERGENCE_TOTAL = Counter(
        "onix_gateway_cache_semantic_rejected_divergence_total",
        "Candidats sémantiques REJETÉS pour divergence numérique/entité (sécurité factuelle)",
    )

    # ── Streaming SSE (RBAC-safe, cf. app/streaming.py + docs/STREAMING.md) ──
    # Requêtes traitées en mode flux (relais token-par-token devant Onyx).
    STREAM_REQUESTS_TOTAL = Counter(
        "onix_gateway_stream_requests_total",
        "Requêtes traitées en streaming SSE (relais token-par-token)",
    )
    # `reason` = règle/garde ayant AVORTÉ un flux (garde DUR incrémental ou
    # erreur fail-closed) : no_prompt_leak|no_exfil_relay|read_only|
    # guard_error|doc_acl_error|postfilter_error|internal_error.
    STREAM_ABORTED_TOTAL = Counter(
        "onix_gateway_stream_aborted_total",
        "Flux SSE avortés par un garde DUR ou une erreur fail-closed (par raison)",
        ["reason"],
    )
    # Flux où la réponse finale a été REMPLACÉE par un override d'autorité
    # (groundedness molle a posteriori, ou substitution « pas de source accessible »).
    STREAM_OVERRIDDEN_TOTAL = Counter(
        "onix_gateway_stream_overridden_total",
        "Flux SSE dont la réponse finale a été remplacée par un override d'autorité",
    )

    _METRICS_AVAILABLE = True

except Exception as _exc:  # pragma: no cover — jamais déclenché en tests normaux
    _logger.debug("prometheus_client indisponible, métriques désactivées : %s", _exc)
    _METRICS_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# Helpers exception-safe (appelés depuis main.py).
# Chaque helper vérifie la disponibilité ET attrape les exceptions Prometheus
# pour n'JAMAIS propager d'erreur à l'appelant.
# ─────────────────────────────────────────────────────────────────────────────

def inc_requests(endpoint: str, decision: str) -> None:
    """Incrémente `onix_gateway_requests_total{endpoint, decision}`."""
    if not _METRICS_AVAILABLE:
        return
    try:
        REQUESTS_TOTAL.labels(endpoint=endpoint, decision=decision).inc()
    except Exception as exc:
        _logger.debug("metrics inc_requests: %s", exc)


def inc_guardrail(rule: str, blocked: bool) -> None:
    """Incrémente `onix_gateway_guardrail_total{rule, blocked}`."""
    if not _METRICS_AVAILABLE:
        return
    try:
        GUARDRAIL_TOTAL.labels(rule=rule, blocked=str(blocked).lower()).inc()
    except Exception as exc:
        _logger.debug("metrics inc_guardrail: %s", exc)


def inc_answer_no_context() -> None:
    """Incrémente `onix_gateway_answer_no_context_total`."""
    if not _METRICS_AVAILABLE:
        return
    try:
        ANSWER_NO_CONTEXT_TOTAL.inc()
    except Exception as exc:
        _logger.debug("metrics inc_answer_no_context: %s", exc)


def inc_citation(has_citation: bool) -> None:
    """Incrémente l'un des deux compteurs de citation selon la présence."""
    if not _METRICS_AVAILABLE:
        return
    try:
        if has_citation:
            ANSWER_WITH_CITATION_TOTAL.inc()
        else:
            ANSWER_WITHOUT_CITATION_TOTAL.inc()
    except Exception as exc:
        _logger.debug("metrics inc_citation: %s", exc)


def observe_latency(seconds: float) -> None:
    """Enregistre une observation dans `onix_gateway_request_latency_seconds`."""
    if not _METRICS_AVAILABLE:
        return
    try:
        REQUEST_LATENCY_SECONDS.observe(seconds)
    except Exception as exc:
        _logger.debug("metrics observe_latency: %s", exc)


def inc_upstream_error() -> None:
    """Incrémente `onix_gateway_upstream_errors_total`."""
    if not _METRICS_AVAILABLE:
        return
    try:
        UPSTREAM_ERRORS_TOTAL.inc()
    except Exception as exc:
        _logger.debug("metrics inc_upstream_error: %s", exc)


def inc_feedback(rating: str) -> None:
    """Incrémente `onix_gateway_feedback_total{rating}`."""
    if not _METRICS_AVAILABLE:
        return
    try:
        FEEDBACK_TOTAL.labels(rating=rating).inc()
    except Exception as exc:
        _logger.debug("metrics inc_feedback: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers cache (RBAC-safe). Tous exception-safe — un défaut Prometheus ou un
# label inattendu NE DOIT JAMAIS faire échouer une requête utilisateur.
# Heuristique de temps économisé : valeur par défaut 2.0 s par hit (= ordre de
# grandeur d'une génération RAG moyenne sur un LLM 7B local). Configurable via
# l'env GATEWAY_CACHE_SECONDS_PER_HIT pour ajuster sur la mesure réelle
# (cf. docs/CACHE.md §observabilité).
# ─────────────────────────────────────────────────────────────────────────────
import os as _os  # local pour rester contenu au bloc cache.

_DEFAULT_SECONDS_PER_HIT = 2.0


def _seconds_per_hit() -> float:
    raw = _os.environ.get("GATEWAY_CACHE_SECONDS_PER_HIT")
    if not raw:
        return _DEFAULT_SECONDS_PER_HIT
    try:
        v = float(raw)
        # Bornage : valeurs négatives → désactive l'incrément.
        return v if v >= 0 else 0.0
    except (TypeError, ValueError):
        return _DEFAULT_SECONDS_PER_HIT


def inc_cache_hit(tier: str = "exact") -> None:
    """Incrémente `onix_gateway_cache_hits_total{tier}` + le compteur de
    secondes économisées (heuristique constante, cf. ``_seconds_per_hit``)."""
    if not _METRICS_AVAILABLE:
        return
    try:
        CACHE_HITS_TOTAL.labels(tier=tier).inc()
        CACHE_SECONDS_SAVED_TOTAL.inc(_seconds_per_hit())
    except Exception as exc:
        _logger.debug("metrics inc_cache_hit: %s", exc)


def inc_cache_miss() -> None:
    """Incrémente `onix_gateway_cache_misses_total`."""
    if not _METRICS_AVAILABLE:
        return
    try:
        CACHE_MISSES_TOTAL.inc()
    except Exception as exc:
        _logger.debug("metrics inc_cache_miss: %s", exc)


def inc_cache_bypassed(reason: str) -> None:
    """Incrémente `onix_gateway_cache_bypassed_total{reason}`."""
    if not _METRICS_AVAILABLE:
        return
    try:
        CACHE_BYPASSED_TOTAL.labels(reason=reason).inc()
    except Exception as exc:
        _logger.debug("metrics inc_cache_bypassed: %s", exc)


def add_cache_tokens_saved(tokens: int) -> None:
    """Ajoute `tokens` à `onix_gateway_cache_tokens_saved_total`. Tolère 0/négatif."""
    if not _METRICS_AVAILABLE or tokens <= 0:
        return
    try:
        CACHE_TOKENS_SAVED_TOTAL.inc(int(tokens))
    except Exception as exc:
        _logger.debug("metrics add_cache_tokens_saved: %s", exc)


def inc_cache_error(op: str) -> None:
    """Incrémente `onix_gateway_cache_errors_total{op}` (op ∈ get|set)."""
    if not _METRICS_AVAILABLE:
        return
    try:
        CACHE_ERRORS_TOTAL.labels(op=op).inc()
    except Exception as exc:
        _logger.debug("metrics inc_cache_error: %s", exc)


def inc_cache_semantic_candidate() -> None:
    """Incrémente `onix_gateway_cache_semantic_candidates_total` (voisin trouvé
    au-dessus du seuil cosinus, AVANT le garde anti-divergence)."""
    if not _METRICS_AVAILABLE:
        return
    try:
        CACHE_SEMANTIC_CANDIDATES_TOTAL.inc()
    except Exception as exc:
        _logger.debug("metrics inc_cache_semantic_candidate: %s", exc)


def inc_cache_semantic_rejected_divergence() -> None:
    """Incrémente `onix_gateway_cache_semantic_rejected_divergence_total` (un
    candidat pourtant similaire a été REFUSÉ pour divergence factuelle)."""
    if not _METRICS_AVAILABLE:
        return
    try:
        CACHE_SEMANTIC_REJECTED_DIVERGENCE_TOTAL.inc()
    except Exception as exc:
        _logger.debug("metrics inc_cache_semantic_rejected_divergence: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers streaming SSE. Tous exception-safe — un défaut Prometheus NE DOIT
# JAMAIS interrompre un flux en cours côté utilisateur.
# ─────────────────────────────────────────────────────────────────────────────

def inc_stream_requests() -> None:
    """Incrémente `onix_gateway_stream_requests_total`."""
    if not _METRICS_AVAILABLE:
        return
    try:
        STREAM_REQUESTS_TOTAL.inc()
    except Exception as exc:
        _logger.debug("metrics inc_stream_requests: %s", exc)


def inc_stream_aborted(reason: str) -> None:
    """Incrémente `onix_gateway_stream_aborted_total{reason}` (garde DUR /
    erreur fail-closed ayant coupé le flux)."""
    if not _METRICS_AVAILABLE:
        return
    try:
        STREAM_ABORTED_TOTAL.labels(reason=reason).inc()
    except Exception as exc:
        _logger.debug("metrics inc_stream_aborted: %s", exc)


def inc_stream_overridden() -> None:
    """Incrémente `onix_gateway_stream_overridden_total` (réponse finale
    remplacée par un override d'autorité)."""
    if not _METRICS_AVAILABLE:
        return
    try:
        STREAM_OVERRIDDEN_TOTAL.inc()
    except Exception as exc:
        _logger.debug("metrics inc_stream_overridden: %s", exc)
