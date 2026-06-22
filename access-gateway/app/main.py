"""access-gateway — application FastAPI (proxy RBAC identity-aware devant Onyx).

Endpoints :
  GET  /health                      — sonde (pas d'auth).
  GET  /v1/authorized-document-sets — introspection : groupes + Document Sets
                                       autorisés de l'appelant (debug/UX).
  POST /v1/chat/send-message        — proxy vers Onyx, filtre Document Set FORCÉ
                                       au périmètre de l'utilisateur, puis relais.

Sécurité : identité fournie par le SSO OIDC en amont (en-tête X-OIDC-Claims,
claims VÉRIFIÉS par le reverse-proxy/IdP). La passerelle ne fait jamais confiance
à un `document_set` choisi par le client au-delà de son périmètre (deny-by-default).

100 % local/souverain hors l'appel à Microsoft Entra (Graph/OIDC) — strictement
nécessaire pour connaître l'appartenance aux groupes de l'entreprise.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import __version__
from .audit import log_access_decision, log_guardrail_decision
from .config import get_settings
from .guardrail import has_citation, post_filter
from .identity import IdentityError, _TTLCache, resolve_principal
from .graph_client import GraphError
from .mapping import GroupMap, load_group_map
from .metrics import (
    add_cache_tokens_saved,
    inc_answer_no_context,
    inc_cache_bypassed,
    inc_citation,
    inc_feedback,
    inc_guardrail,
    inc_requests,
    inc_upstream_error,
    observe_latency,
)
from .onyx_proxy import (
    AccessDenied,
    apply_filtered_answer,
    enforce_document_sets,
    extract_answer,
    reconstruct_context,
    upstream_headers,
)
from . import audit as _audit
from .cache import (
    _perimeter_partition,
    build_cache,
    build_embed_fn,
    estimate_tokens,
    make_cache_key,
    normalize_question,
    should_bypass,
)
from .doc_acl import CompositeDocACL, StaticDocACL, filter_citations
from .fabric_client import FabricClient
from .fabric_doc_acl import build_fabric_acl
from .fabric_doc_acl import load_mapping as load_fabric_mapping
from .graph_acl import GraphSession, build_graph_acl, load_mapping
from .streaming import proxy_stream

_logger = logging.getLogger("onix.gateway")


def _configure_logging() -> None:
    level = getattr(logging, os.environ.get("GATEWAY_LOG_LEVEL", "INFO").upper(), logging.INFO)
    for name in ("onix.gateway", "onix.gateway.audit"):
        logger = logging.getLogger(name)
        logger.setLevel(level)
        if not logger.handlers and not logging.getLogger().handlers:
            h = logging.StreamHandler()
            h.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
            logger.addHandler(h)
            logger.propagate = False


class _State:
    """État applicatif partagé (mapping + client HTTP + cache groupes)."""

    group_map: GroupMap
    http: httpx.AsyncClient
    cache: _TTLCache


async def _build_doc_acl(http_client: httpx.AsyncClient, settings):
    """Construit le filtre ACL par-document : sources combinées en OR —
    StaticDocACL (doc_acl.json) et/ou GraphDocACL (permissions SharePoint via
    Microsoft Graph, opt-in). Renvoie ``(acl | None, graph_acl | None)``. Tolérant :
    une source en échec est OMISE (jamais de crash de la passerelle pour l'ACL)."""
    if not settings.doc_acl_enabled:
        return None, None
    sources: list = []
    graph_acl = None
    if os.path.exists(settings.doc_acl_path):
        try:
            sources.append(StaticDocACL.from_file(
                settings.doc_acl_path, default_policy=settings.doc_acl_default_policy))
            _logger.info("doc_acl statique chargé (%s).", settings.doc_acl_path)
        except Exception as exc:  # noqa: BLE001
            _logger.error("doc_acl statique illisible (%s) — ignoré.", exc)
    if settings.doc_acl_graph_enabled:
        try:
            graph = GraphSession(client=http_client, settings=settings)
            mapping = load_mapping(settings.doc_acl_mapping_path)
            graph_acl = await build_graph_acl(
                graph, mapping,
                default_policy=settings.doc_acl_default_policy,
                ttl_seconds=settings.doc_acl_refresh_seconds,
            )
            sources.append(graph_acl)
            _logger.info("doc_acl Graph synchronisé (%d docs SharePoint).", len(graph_acl))
        except GraphError as exc:
            _logger.error("doc_acl Graph indisponible (%s) — source statique seule.", exc)
    # ── Source Microsoft FABRIC (M3) : ferme la fuite « citation Fabric hors
    # périmètre » en câblant l'ACL Fabric (gold-only, roleAssignments) au filtre.
    # OR-mergée avec les autres sources. Opt-in ; fail-closed (échec → omise). ──
    if settings.doc_acl_fabric_enabled:
        fabric_client: Optional[FabricClient] = None
        try:
            fabric_client = FabricClient(settings, client=http_client)
            fabric_mapping = load_fabric_mapping(settings.doc_acl_fabric_mapping_path)
            fabric_acl = await build_fabric_acl(
                fabric_client, fabric_mapping,
                default_policy=settings.doc_acl_default_policy,
            )
            sources.append(fabric_acl)
            _logger.info("doc_acl Fabric synchronisé (%d docs Fabric).", len(fabric_acl))
        except Exception as exc:  # noqa: BLE001
            # Une source en échec est OMISE (jamais de crash de la passerelle pour
            # l'ACL) ; deny-by-default au filtre reste la posture sûre.
            _logger.error("doc_acl Fabric indisponible (%s) — source omise.", exc)
        finally:
            # FabricClient possède son client httpx s'il l'a créé ; ici on lui a
            # passé le client partagé (owns_client=False) → aclose est un no-op,
            # on n'attente donc pas le client partagé de la passerelle.
            if fabric_client is not None:
                await fabric_client.aclose()
    if not sources:
        _logger.warning(
            "doc_acl activé mais aucune source exploitable (fichier '%s' / Graph) "
            "→ filtre par-document INACTIF.", settings.doc_acl_path,
        )
        return None, graph_acl
    if len(sources) == 1:
        return sources[0], graph_acl
    return CompositeDocACL(sources), graph_acl


async def _acl_refresher(app: FastAPI, settings) -> None:
    """Tâche de fond : re-synchronise périodiquement l'ACL (statique + Graph) selon
    le TTL. Le swap de ``app.state.doc_acl`` est atomique (les lecteurs voient
    l'ancienne OU la nouvelle ACL, jamais un état partiel). Un refresh raté ne tue
    pas la passerelle (on conserve l'ACL courante)."""
    interval = max(60, int(settings.doc_acl_refresh_seconds))
    while True:
        try:
            await asyncio.sleep(interval)
            combined, gacl = await _build_doc_acl(app.state.http, settings)
            app.state.doc_acl = combined
            app.state.doc_acl_graph = gacl
            _logger.info("doc_acl re-synchronisé (intervalle %ss).", interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            _logger.error("doc_acl refresh échoué (%s) — ACL courante conservée.", exc)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _configure_logging()
    settings = get_settings()
    app.state.group_map = load_group_map(settings.mapping_path)
    app.state.cache = _TTLCache(settings.group_cache_ttl)
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(settings.upstream_timeout))
    # Cache applicatif RBAC-safe (couche au-dessus d'Onyx/Ollama). FAIL-SAFE : une
    # config invalide (secret HMAC manquant) DÉSACTIVE le cache avec un log CRITICAL
    # plutôt que de couper la passerelle (le cache est une optimisation, pas une
    # autorité ; cache OFF = posture sûre — aucun risque de fuite).
    try:
        app.state.response_cache = build_cache(settings)
    except RuntimeError as exc:
        _logger.critical("Cache DÉSACTIVÉ (config invalide) : %s", exc)
        app.state.response_cache = None
    # Fonction d'embedding du tier sémantique (opt-in) : construite une fois si
    # activé, injectée aux lookups/stores. None si désactivé → tier sémantique inerte.
    app.state.embed_fn = build_embed_fn(settings) if settings.semantic_cache_enabled else None
    # Filtre ACL par-document (FOSS) : StaticDocACL ⊕ GraphDocACL (cf. _build_doc_acl).
    app.state.doc_acl, app.state.doc_acl_graph = await _build_doc_acl(app.state.http, settings)
    # Rafraîchi périodique de l'ACL Graph (tâche de fond) si TTL > 0.
    app.state.acl_refresher = None
    if app.state.doc_acl_graph is not None and settings.doc_acl_refresh_seconds > 0:
        app.state.acl_refresher = asyncio.create_task(_acl_refresher(app, settings))
    _logger.info(
        "gateway prête : source=%s, groupes=%d, deny_if_no_match=%s, cache=%s, doc_acl=%s, stream=%s",
        settings.group_source,
        len(app.state.group_map.by_group),
        settings.deny_if_no_match,
        "on" if app.state.response_cache is not None else "off",
        "on" if app.state.doc_acl is not None else "off",
        "on" if settings.stream_enabled else "off",
    )
    try:
        yield
    finally:
        task = getattr(app.state, "acl_refresher", None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        await app.state.http.aclose()
        _cache = getattr(app.state, "response_cache", None)
        if _cache is not None:
            _cache.close()


app = FastAPI(
    title="onix access-gateway",
    version=__version__,
    description="Proxy RBAC identity-aware (cloisonnement par groupe/Document Set) devant Onyx.",
    lifespan=_lifespan,
)


@app.get("/health")
async def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "service": "onix-access-gateway",
        "version": __version__,
        "group_source": settings.group_source,
        "graph_configured": settings.graph_configured,
        "deny_if_no_match": settings.deny_if_no_match,
        "groups_mapped": len(getattr(app.state, "group_map", GroupMap()).by_group),
    }


@app.get("/metrics")
async def metrics() -> Response:
    """Métriques Prometheus — format texte exposition v0.0.4.

    Réseau interne uniquement (pas de port hôte publié). Aucune auth requise
    sur le réseau ``onix-net`` (cf. posture de monitoring/prometheus.yml).
    Renvoie 404 si ``GATEWAY_METRICS_ENABLED=false``.
    """
    settings = get_settings()
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Métriques désactivées.")
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
    except Exception as exc:
        _logger.warning("Erreur génération métriques Prometheus : %s", exc)
        raise HTTPException(status_code=503, detail="Métriques temporairement indisponibles.")


async def _principal_and_sets(
    request: Request,
    x_oidc_claims: Optional[str],
    *,
    endpoint: str,
    proxy_secret_header: Optional[str] = None,
):
    """Facteur commun : résout l'identité, ses groupes, et ses Document Sets.

    **Fail-closed** : toute impossibilité de résoudre l'identité (401) ou les
    groupes (502) est journalisée comme un DENY et propagée. La décision d'accès
    finale (allow/deny selon le périmètre) est journalisée par l'appelant.
    Le secret de preuve proxy (X-OIDC-Proxy-Secret) est propagé à resolve_principal
    pour l'anti-spoof : un X-OIDC-Claims sans preuve de transit proxy est rejeté.
    """
    settings = get_settings()
    try:
        principal = await resolve_principal(
            settings,
            oidc_claims_header=x_oidc_claims,
            proxy_secret_header=proxy_secret_header,
            cache=request.app.state.cache,
            http_client=request.app.state.http,
        )
    except IdentityError as exc:
        # Identité absente/illisible : refus dur, journalisé sans acteur identifiable.
        log_access_decision(
            actor=None, decision="deny", reason="identity_unresolved", endpoint=endpoint
        )
        raise HTTPException(status_code=401, detail=str(exc))
    except GraphError as exc:
        # Dépendance amont (Graph) indisponible alors que requise : on REFUSE
        # (fail-closed) plutôt que de laisser passer sans groupes. 502 car externe.
        log_access_decision(
            actor=None, decision="deny", reason="group_source_unavailable", endpoint=endpoint
        )
        raise HTTPException(status_code=502, detail=str(exc))
    authorized = request.app.state.group_map.authorized_document_sets(principal.group_ids)
    return principal, authorized


@app.get("/v1/authorized-document-sets")
async def authorized_document_sets(
    request: Request,
    x_oidc_claims: Optional[str] = Header(default=None, alias="X-OIDC-Claims"),
    x_oidc_proxy_secret: Optional[str] = Header(default=None, alias="X-OIDC-Proxy-Secret"),
) -> dict[str, Any]:
    """Introspection : qui suis-je, mes groupes, mes Document Sets autorisés."""
    principal, authorized = await _principal_and_sets(
        request, x_oidc_claims, endpoint="authorized-document-sets",
        proxy_secret_header=x_oidc_proxy_secret,
    )
    decision = "allow" if authorized else "deny"
    log_access_decision(
        actor=principal.user_id,
        decision=decision,
        reason="introspection",
        group_source=principal.source,
        group_count=len(principal.group_ids),
        authorized_sets=authorized,
        endpoint="authorized-document-sets",
    )
    inc_requests(endpoint="authorized-document-sets", decision=decision)
    return {
        "user_id": principal.user_id,
        "upn": principal.upn,
        "group_source": principal.source,
        "group_count": len(principal.group_ids),
        "authorized_document_sets": authorized,
    }


@app.post("/v1/chat/send-message")
async def chat_send_message(
    request: Request,
    x_oidc_claims: Optional[str] = Header(default=None, alias="X-OIDC-Claims"),
    x_oidc_proxy_secret: Optional[str] = Header(default=None, alias="X-OIDC-Proxy-Secret"),
) -> JSONResponse:
    """Proxy de recherche : force le filtre Document Set au périmètre autorisé,
    puis relaie la requête à Onyx et renvoie sa réponse."""
    settings = get_settings()
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Corps JSON invalide.")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Le corps doit être un objet JSON.")

    principal, authorized = await _principal_and_sets(
        request, x_oidc_claims, endpoint="chat/send-message",
        proxy_secret_header=x_oidc_proxy_secret,
    )

    try:
        safe_payload = enforce_document_sets(
            payload, authorized, deny_if_empty=settings.deny_if_no_match
        )
    except AccessDenied as exc:
        # Périmètre vide OU demande hors-périmètre : refus dur, journalisé (haché).
        log_access_decision(
            actor=principal.user_id,
            decision="deny",
            reason="empty_or_out_of_scope",
            group_source=principal.source,
            group_count=len(principal.group_ids),
            authorized_sets=authorized,
            endpoint="chat/send-message",
        )
        inc_requests(endpoint="chat/send-message", decision="deny")
        raise HTTPException(status_code=403, detail=str(exc))

    effective = (
        safe_payload.get("retrieval_options", {}).get("filters", {}).get("document_set")
    )
    log_access_decision(
        actor=principal.user_id,
        decision="allow",
        reason="proxied",
        group_source=principal.source,
        group_count=len(principal.group_ids),
        authorized_sets=authorized,
        effective_sets=effective if isinstance(effective, list) else None,
        endpoint="chat/send-message",
    )

    question_text = payload.get("message", "") if isinstance(payload, dict) else ""

    # ── STREAMING SSE : si demandé ET activé, on relaie en FLUX (proxy_stream). ──
    # Garde-fous (garde DUR incrémental + override final) et filtre ACL par-document
    # appliqués DANS le flux ; le cache est court-circuité (une réponse streamée
    # n'est pas rejouable en bloc). Cf. docs/STREAMING.md.
    if settings.stream_enabled and isinstance(payload, dict) and payload.get("stream") is True:
        acl = getattr(request.app.state, "doc_acl", None)
        url = f"{settings.onyx_base_url}/chat/send-message"
        headers = upstream_headers(settings.onyx_api_key)
        inc_requests(endpoint="chat/send-message", decision="allow")

        async def _stream_gen():
            try:
                async with request.app.state.http.stream(
                    "POST", url, json=safe_payload, headers=headers,
                ) as resp:
                    async for chunk in proxy_stream(
                        resp.aiter_lines(),
                        question=str(question_text),
                        principal=principal,
                        acl=acl,
                        settings=settings,
                        post_filter=post_filter,
                        doc_acl_filter=filter_citations,
                        extract_answer=extract_answer,
                        apply_filtered_answer=apply_filtered_answer,
                        audit=_audit,
                    ):
                        yield chunk
            except httpx.HTTPError as exc:
                _logger.warning("Erreur de relais (stream) vers Onyx : %s", type(exc).__name__)
                inc_upstream_error()
                yield (json.dumps({"error": "Onyx amont injoignable."}) + "\n").encode("utf-8")

        return StreamingResponse(_stream_gen(), media_type="application/x-ndjson")

    # Latence bout-en-bout mesurée dès l'entrée (couvre cache + amont).
    _t0 = time.perf_counter()

    # ── CACHE — clé RBAC-safe = HMAC(périmètre Document Set TRIÉ ∥ locale ∥
    # question normalisée). On NE met en cache QUE le corps PÉRIMÈTRE-déterministe
    # (réponse Onyx + post-filtre garde-fous), JAMAIS le résultat du filtre ACL
    # par-document (qui est PAR UTILISATEUR) : ce dernier est ré-appliqué à CHAQUE
    # requête (hit ET miss). Ainsi deux utilisateurs au même périmètre mutualisent
    # le coût LLM sans jamais partager une citation que l'un n'a pas le droit de voir.
    cache = getattr(request.app.state, "response_cache", None)
    acl = getattr(request.app.state, "doc_acl", None)
    embed_fn = getattr(request.app.state, "embed_fn", None)
    bypass = should_bypass(payload=payload, headers=request.headers)
    cacheable = cache is not None and bypass is None
    norm_q = normalize_question(str(question_text))
    cache_key: Optional[str] = None
    perimeter: Optional[str] = None
    if cacheable:
        cache_key = make_cache_key(
            settings=settings,
            principal=principal.user_id,
            normalized_question=norm_q,
            authorized_doc_sets=list(authorized),
        )
        perimeter = _perimeter_partition(list(authorized))
    elif cache is not None and bypass is not None:
        inc_cache_bypassed(bypass)

    body: Any
    status_code = 200
    media = "application/json"
    cached = cache.lookup(cache_key) if (cacheable and cache_key) else None
    # Tier SÉMANTIQUE (opt-in) : rattrapage au-dessus du miss EXACT (reformulations).
    # Sûr par construction : recherche bornée à la partition du périmètre + garde
    # anti-divergence (nombres/dates/entités) DANS semantic_lookup. Cf. docs/CACHE.md §13.
    if (cached is None and cacheable and settings.semantic_cache_enabled
            and embed_fn is not None and perimeter is not None):
        cached = cache.semantic_lookup(perimeter, norm_q, embed_fn, raw_question=str(question_text))

    if cached is not None:
        # HIT : corps périmètre-déterministe déjà post-filtré → on saute Onyx + LLM.
        body = cached
        add_cache_tokens_saved(estimate_tokens(body))
        log_access_decision(
            actor=principal.user_id, decision="cache_hit", reason="served_from_cache",
            group_source=principal.source, group_count=len(principal.group_ids),
            authorized_sets=authorized, endpoint="chat/send-message",
        )
    else:
        # MISS : relais vers Onyx.
        url = f"{settings.onyx_base_url}/chat/send-message"
        try:
            resp = await request.app.state.http.post(
                url, json=safe_payload, headers=upstream_headers(settings.onyx_api_key),
            )
        except httpx.HTTPError as exc:
            _logger.warning("Erreur de relais vers Onyx : %s", type(exc).__name__)
            inc_upstream_error()
            raise HTTPException(status_code=502, detail="Onyx amont injoignable.")
        status_code = resp.status_code
        _media_hdr = resp.headers.get("content-type", "application/json")
        media = "application/json" if _media_hdr.startswith("application/json") else _media_hdr
        body = _safe_json(resp)

        if not (200 <= status_code < 300):
            # Non-2xx : relais tel quel, sans cache / ACL / garde-fous.
            observe_latency(time.perf_counter() - _t0)
            inc_requests(endpoint="chat/send-message", decision="allow")
            return JSONResponse(status_code=status_code, content=body, media_type="application/json")

        # ── POST-FILTRE GARDE-FOUS (couche 3, hors-LLM, périmètre-déterministe) ──
        # Dernier point sous notre contrôle avant l'utilisateur. S'exécute APRÈS le
        # LLM : une injection ne peut pas le désactiver. Au moindre invariant violé
        # (fuite de prompt, injection exécutée, write simulé, fait non sourcé) → refus.
        if settings.guardrail_enabled:
            answer, field = extract_answer(body)
            if field is not None:
                context = reconstruct_context(body)
                verdict = post_filter(str(question_text), context, answer)
                log_guardrail_decision(
                    actor=principal.user_id, blocked=verdict.blocked,
                    rule=verdict.rule, reason=verdict.reason, endpoint="chat/send-message",
                )
                inc_guardrail(rule=verdict.rule, blocked=verdict.blocked)
                inc_citation(has_citation=has_citation(verdict.answer))
                if not context:
                    inc_answer_no_context()
                if verdict.blocked:
                    body = apply_filtered_answer(body, field, verdict.answer)

        # STOCKAGE : corps PÉRIMÈTRE-déterministe (pré-filtre ACL par-utilisateur).
        # On indexe aussi l'embedding (best-effort) pour le tier sémantique si activé.
        if cacheable and cache_key:
            cache.store(
                cache_key, body, ttl=settings.cache_ttl_seconds,
                perimeter=perimeter, normalized_question=norm_q, embed_fn=embed_fn,
                raw_question=str(question_text),
            )

    # ── FILTRE ACL PAR-DOCUMENT (PAR UTILISATEUR) — appliqué hit ET miss. ──────
    # Retire de la réponse les citations/documents non autorisés pour CET appelant
    # (RBAC fin FOSS, filtre de SORTIE — cf. docs/RBAC.md). Inactif si pas d'ACL.
    if acl is not None:
        body, _dropped = filter_citations(
            body, principal, acl, _audit,
            strip_uncited=settings.doc_acl_strip_uncited,
            extract_answer=extract_answer,
            apply_filtered_answer=apply_filtered_answer,
            enabled=settings.doc_acl_enabled,
        )

    observe_latency(time.perf_counter() - _t0)
    inc_requests(endpoint="chat/send-message", decision="allow")
    return JSONResponse(
        status_code=status_code, content=body,
        media_type="application/json" if media.startswith("application/json") else media,
    )


@app.post("/v1/feedback")
async def feedback(
    request: Request,
    x_oidc_claims: Optional[str] = Header(default=None, alias="X-OIDC-Claims"),
    x_oidc_proxy_secret: Optional[str] = Header(default=None, alias="X-OIDC-Proxy-Secret"),
) -> dict[str, Any]:
    """Retour utilisateur (thumbs up/down) sur la dernière réponse.

    Incrémente ``onix_gateway_feedback_total{rating}`` (rating ∈ up|down).
    Même résolution d'identité que les autres endpoints (fail-closed).
    Activé uniquement si ``GATEWAY_METRICS_ENABLED=true`` (défaut).
    """
    settings = get_settings()
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Feedback désactivé (métriques off).")

    # Résolution d'identité (fail-closed : pas de feedback anonyme).
    await _principal_and_sets(
        request, x_oidc_claims, endpoint="feedback",
        proxy_secret_header=x_oidc_proxy_secret,
    )

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Corps JSON invalide.")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Le corps doit être un objet JSON.")

    rating = payload.get("rating", "")
    if rating not in ("up", "down"):
        raise HTTPException(
            status_code=422, detail="rating doit être 'up' ou 'down'."
        )

    inc_feedback(rating=rating)
    return {"status": "ok", "rating": rating}


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text}
