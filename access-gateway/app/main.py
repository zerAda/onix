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

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from . import __version__
from .audit import log_access_decision, log_guardrail_decision
from .config import get_settings
from .guardrail import post_filter
from .identity import IdentityError, _TTLCache, resolve_principal
from .graph_client import GraphError
from .mapping import GroupMap, load_group_map
from .onyx_proxy import (
    AccessDenied,
    apply_filtered_answer,
    enforce_document_sets,
    extract_answer,
    reconstruct_context,
    upstream_headers,
)

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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _configure_logging()
    settings = get_settings()
    app.state.group_map = load_group_map(settings.mapping_path)
    app.state.cache = _TTLCache(settings.group_cache_ttl)
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(settings.upstream_timeout))
    _logger.info(
        "gateway prête : source=%s, groupes mappés=%d, deny_if_no_match=%s",
        settings.group_source,
        len(app.state.group_map.by_group),
        settings.deny_if_no_match,
    )
    try:
        yield
    finally:
        await app.state.http.aclose()


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


async def _principal_and_sets(
    request: Request, x_oidc_claims: Optional[str], *, endpoint: str
):
    """Facteur commun : résout l'identité, ses groupes, et ses Document Sets.

    **Fail-closed** : toute impossibilité de résoudre l'identité (401) ou les
    groupes (502) est journalisée comme un DENY et propagée. La décision d'accès
    finale (allow/deny selon le périmètre) est journalisée par l'appelant.
    """
    settings = get_settings()
    try:
        principal = await resolve_principal(
            settings,
            oidc_claims_header=x_oidc_claims,
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
) -> dict[str, Any]:
    """Introspection : qui suis-je, mes groupes, mes Document Sets autorisés."""
    principal, authorized = await _principal_and_sets(
        request, x_oidc_claims, endpoint="authorized-document-sets"
    )
    log_access_decision(
        actor=principal.user_id,
        decision="allow" if authorized else "deny",
        reason="introspection",
        group_source=principal.source,
        group_count=len(principal.group_ids),
        authorized_sets=authorized,
        endpoint="authorized-document-sets",
    )
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
        request, x_oidc_claims, endpoint="chat/send-message"
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

    url = f"{settings.onyx_base_url}/chat/send-message"
    try:
        resp = await request.app.state.http.post(
            url,
            json=safe_payload,
            headers=upstream_headers(settings.onyx_api_key),
        )
    except httpx.HTTPError as exc:
        _logger.warning("Erreur de relais vers Onyx : %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Onyx amont injoignable.")

    body = _safe_json(resp)

    # ── CHEMIN RÉPONSE : POST-FILTRE GARDE-FOUS (couche 3, hors-LLM, DÉPLOYÉ) ──
    # La passerelle est le dernier point sous notre contrôle avant l'utilisateur :
    # on inspecte la réponse de l'assistant Onyx et, au moindre invariant violé
    # (fuite de prompt, exécution d'injection, write simulé, fait non sourcé…), on
    # SUBSTITUE un refus déterministe. S'exécute APRÈS le LLM : une injection ne
    # peut pas le désactiver. N'agit que sur les réponses 2xx exploitables.
    if settings.guardrail_enabled and 200 <= resp.status_code < 300:
        answer, field = extract_answer(body)
        if field is not None:
            question = payload.get("message", "") if isinstance(payload, dict) else ""
            context = reconstruct_context(body)
            verdict = post_filter(str(question), context, answer)
            log_guardrail_decision(
                actor=principal.user_id,
                blocked=verdict.blocked,
                rule=verdict.rule,
                reason=verdict.reason,
                endpoint="chat/send-message",
            )
            if verdict.blocked:
                body = apply_filtered_answer(body, field, verdict.answer)

    media = resp.headers.get("content-type", "application/json")
    return JSONResponse(
        status_code=resp.status_code,
        content=body,
        media_type="application/json" if media.startswith("application/json") else media,
    )


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text}
