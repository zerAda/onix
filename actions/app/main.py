"""onix-actions — Couche applicative d'onix (audit / génération / tâches /
notification / usage / coût / administration).

Microservice FastAPI interne (réseau onix-net, AUCUN port hôte), appelé par
l'assistant Onyx via Actions OpenAPI. 100 % local : aucun Azure / M365 / cloud.

Tous les endpoints (hors /health) sont :
  * authentifiés par clé API (header X-API-Key) ;
  * gatés par l'état d'administration (kill-switch global + flag par fonction +
    blocage utilisateur) — un flag coupé renvoie 403.

Ce module porte la LOGIQUE d'AC360 (moteur d'audit, génération .docx, trackers,
contrôles admin) en la généricisant intégralement.
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel, Field

from . import admin_state, audit_log, cost_tracker, docgen, dlp, notify as notify_mod
from . import fabric_reference
from . import rag_local
from . import ocr as ocr_mod
from . import retention as retention_mod
from . import safe_logger
from . import security
from . import tasks as tasks_mod
from . import usage_tracker
from .audit_engine import audit as run_audit
from .audit_engine import build_review_fiche
from .audit_engine import extract_canonical_fields
from .caller_identity import CallerContext
from .security import require_admin, require_caller, validate_upload

_logger = logging.getLogger("onix.actions")


def _configure_logging() -> None:
    """Configure le logging applicatif (niveau via ONIX_LOG_LEVEL) si aucun
    handler n'est déjà posé — permet de VOIR le mode d'extraction réellement
    utilisé (llm vs heuristique) et les avertissements notify/llm. Idempotent et
    non destructif vis-à-vis d'une configuration existante (ex: uvicorn).

    WS2 : installe le filtre de REDACTION PII sur le logger `onix.actions` (et
    donc tous ses enfants `onix.actions.*`) — aucun log ne peut fuiter une donnée
    personnelle (JWT/IBAN/NIR/email) ni servir à injecter de fausses lignes."""
    level_name = os.environ.get("ONIX_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    app_logger = logging.getLogger("onix.actions")
    app_logger.setLevel(level)
    safe_logger.install("onix.actions")  # redaction PII + anti-CRLF (idempotent)
    if not app_logger.handlers and not logging.getLogger().handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        app_logger.addHandler(handler)
        app_logger.propagate = False


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _configure_logging()
    # Préflight FAIL-CLOSED (HARD-03) : refus de démarrer sans clé HMAC d'audit
    # (sauf override dev ONIX_ACTIONS_AUDIT_KEY_OPTIONAL) — sinon la chaîne d'audit
    # serait en SHA-256 keyless, forgeable (cf. M1 / verify_chain).
    audit_log.preflight_audit_key()
    admin_state.init_db()
    usage_tracker.init_db()
    tasks_mod.init_db()
    yield


app = FastAPI(
    title="onix-actions",
    version="1.0.0",
    description="Couche applicative locale d'onix : audit, génération, tâches, "
    "notification, usage, coût, administration. 100 % local, sans cloud.",
    lifespan=_lifespan,
)

# WS2 — rate-limiting par appelant. Le quota est appliqué DANS la dépendance
# `require_caller` (via `security.enforce_rate_limit`), APRÈS résolution de
# l'identité : un middleware slowapi classique ne verrait que l'IP (les
# dépendances s'exécutent après le middleware). On n'installe donc PAS de
# middleware/handler slowapi (qui resterait inerte ici) ; l'enforcement réel est
# la fenêtre glissante par appelant de `security`. slowapi reste une dépendance
# de référence pour le format de quota et un futur store partagé (Redis).


# ---------------------------------------------------------------------------
# Câblage inter-WS — métriques Prometheus (cf. docs/OBSERVABILITY.md §5)
# ---------------------------------------------------------------------------
# Endpoint /metrics au format texte Prometheus, scrapé par le job `onix-actions`
# (http://actions:8100/metrics). Les noms ci-dessous correspondent EXACTEMENT aux
# requêtes des dashboards Grafana et des règles d'alerte WS6 (contrat figé).
#
# Sécurité (style WS2) : /metrics n'expose AUCUNE donnée personnelle — uniquement
# des compteurs/jauges agrégés, et les labels `endpoint` sont les CHEMINS DE ROUTE
# (gabarits, ex. "/download/{job_id}") et non les valeurs réelles → ni PII, ni
# cardinalité non bornée. L'endpoint est volontairement NON authentifié : le
# service n'a aucun port hôte (réseau interne onix-net uniquement), c'est la même
# posture que /health. À NE PAS exposer publiquement (cf. OBSERVABILITY.md §5).
#
# Création IDEMPOTENTE : ce module est rechargé (importlib.reload) par la suite de
# tests pour relire les variables d'environnement. Recréer un collecteur déjà
# enregistré lèverait « Duplicated timeseries » ; on purge donc toute série de
# même nom du registre par défaut avant de (re)créer la métrique.
def _metric(cls, name, doc, labels=None):
    # prometheus_client stocke le nom de base SANS le suffixe `_total` (Counter).
    # On purge le collecteur dont le nom de base correspond à l'un ou l'autre.
    base = name[: -len("_total")] if name.endswith("_total") else name
    for collector in list(getattr(REGISTRY, "_names_to_collectors", {}).values()):
        cname = getattr(collector, "_name", None)
        if cname is not None and cname in (name, base):
            try:
                REGISTRY.unregister(collector)
            except KeyError:
                pass
    return cls(name, doc, labels) if labels else cls(name, doc)


REQS = _metric(
    Counter, "onix_http_requests_total", "Requêtes HTTP servies par onix-actions.",
    ["endpoint", "method", "status"],
)
LATENCY = _metric(
    Histogram, "onix_http_request_duration_seconds", "Latence des requêtes HTTP (secondes).",
    ["endpoint"],
)
KILLSWITCH = _metric(
    Counter, "onix_killswitch_blocked_total", "Requêtes bloquées par le kill-switch (HTTP 403).",
    ["feature", "reason"],
)
BUDGET_SPENT = _metric(Gauge, "onix_budget_spent_eur", "Coût estimé cumulé (EUR).")
BUDGET_LIMIT = _metric(Gauge, "onix_budget_limit_eur", "Budget alloué (EUR).")
BUDGET_RATIO = _metric(Gauge, "onix_budget_ratio", "Ratio consommé du budget (0–1+).")
UP = _metric(Gauge, "onix_up", "Vivacité applicative onix-actions (1 = up).")
UP.set(1)


@app.middleware("http")
async def _metrics_mw(request: Request, call_next):
    """Compte chaque requête + observe sa latence. Le label `endpoint` est le
    GABARIT de route (pas l'URL réelle) → borne la cardinalité et évite toute
    fuite de valeur dans une métrique."""
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    REQS.labels(path, request.method, response.status_code).inc()
    LATENCY.labels(path).observe(time.perf_counter() - start)
    return response


# ---------------------------------------------------------------------------
# Gating commun
# ---------------------------------------------------------------------------
def _gate(feature: str, caller_id: Optional[str] = None) -> None:
    """Lève 403 si la fonction est coupée (global / feature / utilisateur)."""
    user_hash = admin_state.hash_id(caller_id) if caller_id else None
    allowed, reason = admin_state.is_allowed(feature, user_id_hash=user_hash)
    if not allowed:
        # Observabilité WS6 : compte le blocage AVANT de lever le 403 (réutilisé
        # par l'alerte KillSwitchBlockingTraffic et le panneau « 403 par raison »).
        KILLSWITCH.labels(feature, reason or "unknown").inc()
        raise HTTPException(status_code=403, detail=admin_state.blocked_message(reason))


def _select_reference_record(records: list, client_key: Optional[str]) -> dict:
    """Choisit l'enregistrement de référence parmi une liste : filtré par
    `client_key` (sur le nom du client) si fourni, sinon le premier."""
    if not records:
        raise HTTPException(status_code=404, detail="Référence vide.")
    if client_key:
        from .audit_engine import normalize_name

        target = normalize_name(client_key)
        for rec in records:
            if isinstance(rec, dict) and normalize_name(rec.get("nom_client")) == target:
                return rec
        raise HTTPException(status_code=404, detail="Client introuvable dans la référence.")
    first = records[0]
    if not isinstance(first, dict):
        raise HTTPException(status_code=400, detail="Référence : objet attendu.")
    return first


def _load_reference(
    reference: Optional[Any],
    reference_path: Optional[str],
    client_key: Optional[str],
) -> dict:
    """Résout l'enregistrement de référence : inline (dict OU liste) prioritaire,
    sinon fichier monté (JSON/CSV). Dans les deux cas, une liste est filtrée par
    `client_key` sur le nom du client (à défaut, le premier enregistrement)."""
    if reference is not None:
        # Inline : accepte un objet unique ou une liste d'objets (comme un
        # fichier .json), et applique la même résolution par client_key.
        records = reference if isinstance(reference, list) else [reference]
        return _select_reference_record(records, client_key)
    if not reference_path:
        raise HTTPException(
            status_code=400,
            detail="Aucune référence fournie (champ 'reference' ou 'reference_path').",
        )
    safe = os.path.abspath(reference_path)
    allowed_root = os.path.abspath(os.environ.get("ONIX_REFERENCE_DIR", "/data/reference"))
    if not (safe == allowed_root or safe.startswith(allowed_root + os.sep)):
        raise HTTPException(status_code=400, detail="Chemin de référence hors périmètre.")
    if not os.path.isfile(safe):
        raise HTTPException(status_code=404, detail="Fichier de référence introuvable.")
    try:
        if safe.lower().endswith(".json"):
            with open(safe, "r", encoding="utf-8") as f:
                data = json.load(f)
            records = data if isinstance(data, list) else [data]
        elif safe.lower().endswith(".csv"):
            import csv

            with open(safe, "r", encoding="utf-8") as f:
                records = list(csv.DictReader(f))
        else:
            raise HTTPException(status_code=400, detail="Référence : .json ou .csv attendu.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Référence illisible.")

    return _select_reference_record(records, client_key)


# ---------------------------------------------------------------------------
# Modèles I/O
# ---------------------------------------------------------------------------
class AuditDocument(BaseModel):
    nom_client: Optional[str] = None
    plafond_hospitalisation: Optional[Any] = None
    date_effet: Optional[str] = None
    numero_contrat: Optional[str] = None
    motif_operation: Optional[str] = None


class AuditRequest(BaseModel):
    # Soit un document déjà extrait (champs canoniques), soit un texte brut.
    document: Optional[Dict[str, Any]] = None
    text: Optional[str] = Field(default=None, description="Texte brut à extraire.")
    reference: Optional[Dict[str, Any]] = None
    reference_path: Optional[str] = None
    client_key: Optional[str] = Field(
        default=None, description="Nom de client pour filtrer une référence multi-lignes."
    )
    use_llm: bool = Field(default=False, description="Extraction des champs via Ollama.")
    caller_id: Optional[str] = Field(default=None, description="Identifiant appelant (hashé).")


class RagAskRequest(BaseModel):
    question: str = Field(description="Question en langage naturel.")
    documents: List[Dict[str, Any]] = Field(
        default_factory=list, description="Corpus { id, content } à interroger (souverain)."
    )
    top_k: int = Field(default=1, ge=1, le=10, description="Nombre de documents à récupérer.")
    caller_id: Optional[str] = Field(default=None, description="Identifiant appelant (hashé).")


class ReconcileBatchRequest(BaseModel):
    # Lot de contrats déjà extraits (champs canoniques) à réconcilier contre le SI.
    items: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Lot { document: {champs canoniques}, client_key } à réconcilier.",
    )
    caller_id: Optional[str] = Field(default=None, description="Identifiant appelant (hashé).")


class FicheRequest(BaseModel):
    client_name: str
    summary: str = ""
    alert_points: str = ""
    extra_sections: Optional[Dict[str, str]] = None
    caller_id: Optional[str] = None


class TaskRequest(BaseModel):
    title: str
    due_date: Optional[str] = None
    client_id: Optional[str] = None
    notes: Optional[str] = None
    webhook_url: Optional[str] = None
    caller_id: Optional[str] = None


class NotifyRequest(BaseModel):
    provider: str = "webhook"
    message: str
    subject: Optional[str] = None
    url: Optional[str] = None
    to: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None
    caller_id: Optional[str] = None


class UsageRequest(BaseModel):
    event_type: str
    status: str = "ok"
    user_id: Optional[str] = None
    client_id: Optional[str] = None
    action_name: Optional[str] = None
    document_count: int = 0
    page_count: int = 0
    estimated_tokens_input: int = 0
    estimated_tokens_output: int = 0
    estimated_cost_eur: float = 0.0
    measured: bool = False  # True = tokens MESURÉS (ground truth), False = estimés.


class CostEstimateRequest(BaseModel):
    cost_center: str
    quantity: float
    unit: str = "request"
    client_id: Optional[str] = None
    use_case: Optional[str] = None


class AdminControlRequest(BaseModel):
    admin_id: str = "admin"
    action: str
    scope: str = "global"
    target_id: Optional[str] = None
    reason: Optional[str] = None


class AccessLogRequest(BaseModel):
    """Journalisation d'accès (UPN hashés) : `document_accessed` /
    `rag_search_executed`. Aucun identifiant ni requête en clair n'est persisté."""

    event: str = Field(description="document_accessed | rag_search_executed")
    user_id: Optional[str] = Field(default=None, description="UPN (hashé avant stockage).")
    client_id: Optional[str] = None
    document_id: Optional[str] = Field(default=None, description="ID document (hashé).")
    query: Optional[str] = Field(default=None, description="Requête RAG (NON stockée en clair).")


class RetentionPurgeRequest(BaseModel):
    days: Optional[int] = Field(default=None, description="TTL en jours (défaut ONIX_RETENTION_DAYS).")
    purge_files: bool = Field(default=True, description="Purger aussi les .docx expirés.")


class SubjectErasureRequest(BaseModel):
    """Effacement ciblé d'un sujet (RGPD art. 17)."""

    subject_id: Optional[str] = Field(default=None, description="Identifiant en clair (hashé ici).")
    subject_hash: Optional[str] = Field(default=None, description="Hash du sujet (alternative).")
    erase_files: bool = Field(default=True, description="Effacer aussi les .docx du sujet.")


# ---------------------------------------------------------------------------
# 8. Santé
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "onix-actions",
        "version": app.version,
        "ocr": ocr_mod.ocr_capabilities(),
        "global_enabled": admin_state.is_global_enabled(),
    }


@app.get("/metrics")
def metrics() -> Response:
    """Expose les métriques au format texte Prometheus (cf. OBSERVABILITY.md §5).

    Non authentifié À DESSEIN (réseau interne, aucun port hôte — même posture que
    /health). Rafraîchit les jauges FinOps depuis usage_tracker/cost_tracker à la
    volée : ce sont des AGRÉGATS (coût/budget), jamais de donnée personnelle."""
    spent = usage_tracker.summary().get("estimated_cost_eur", 0.0)
    budget = cost_tracker.check_budget(spent)
    BUDGET_SPENT.set(spent)
    if budget.get("budget_eur"):
        BUDGET_LIMIT.set(budget["budget_eur"])
        BUDGET_RATIO.set((budget.get("ratio_pct") or 0) / 100.0)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------------------
# 1. Audit documentaire
# ---------------------------------------------------------------------------
def _record_llm_usage(
    usage: "Any", *, measured: bool, who: Optional[str] = None
) -> None:
    """Enregistre l'usage/coût LLM dans usage_tracker avec les VRAIS comptes de
    tokens quand `measured=True` (issus d'Ollama : `prompt_eval_count` /
    `eval_count`), sinon une estimation chars/4 (`measured=False`).

    Le coût est valorisé via les centres `llm_token_input` / `llm_token_output`
    (rate card en €/token). On persiste le flag `measured` pour que le FinOps
    distingue le ground truth de l'estimation (cf. docs/FINOPS.md)."""
    cost_in = cost_tracker.estimate_cost(
        "llm_token_input", usage.input_tokens, unit="token", measured=measured
    )
    cost_out = cost_tracker.estimate_cost(
        "llm_token_output", usage.output_tokens, unit="token", measured=measured
    )
    total_cost = cost_in["estimated_cost_eur"] + cost_out["estimated_cost_eur"]
    usage_tracker.track(
        "backend_action_called",
        user_id=who,
        action_name="llm_extract",
        estimated_tokens_input=usage.input_tokens,
        estimated_tokens_output=usage.output_tokens,
        estimated_cost_eur=total_cost,
        cost_source=cost_in["cost_source"],
        measured=measured,
    )


def _resolve_document_fields(
    document: Optional[dict], text: Optional[str], use_llm: bool,
    *, who: Optional[str] = None,
) -> tuple[Dict[str, Any], str]:
    """Construit les champs canoniques du document à partir d'un dict déjà
    extrait, ou d'un texte brut (LLM si demandé, sinon heuristique OCR-like).

    Retourne (champs, mode) où `mode` est le chemin RÉELLEMENT utilisé :
      * "provided"  : document déjà extrait fourni ;
      * "llm"       : extraction par Ollama réussie ;
      * "heuristic" : extraction « clé: valeur » (par défaut OU repli si LLM raté).
    Le mode est journalisé pour l'observabilité (LLM réel vs repli).

    FinOps : sur le chemin "llm", les tokens RÉELS d'Ollama sont enregistrés
    (`measured=True`) ; sur le repli "heuristic" (aucun appel LLM abouti), on
    enregistre une estimation chars/4 (`measured=False`)."""
    if document:
        return document, "provided"
    if not text:
        raise HTTPException(status_code=400, detail="Fournir 'document' ou 'text'.")
    if use_llm:
        _gate("llm")
        try:
            from .llm import extract_fields_llm_with_usage

            fields, usage = extract_fields_llm_with_usage(text)
            if fields:
                # GROUND TRUTH : comptes réels Ollama (ou estimation marquée si la
                # réponse n'a pas renvoyé les compteurs -> usage.measured=False).
                _record_llm_usage(usage, measured=usage.measured, who=who)
                _logger.info(
                    "Extraction document via LLM (mode=llm, champs=%d, in=%d, out=%d, measured=%s)",
                    len(fields), usage.input_tokens, usage.output_tokens, usage.measured,
                )
                return fields, "llm"
            # JSON valide mais aucun champ exploitable -> repli heuristique propre.
            _logger.info("LLM sans champ exploitable -> repli heuristique.")
        except Exception as e:
            # Repli propre sur l'heuristique : « en mieux » mais jamais bloquant.
            _logger.warning("Extraction LLM échouée (%s) -> repli heuristique.", type(e).__name__)
    # Heuristique locale : libellés "clé: valeur" -> champs canoniques. Aucun
    # appel LLM -> tokens ESTIMÉS (chars/4), enregistrés comme NON mesurés.
    from .llm import LLMUsage, estimate_tokens

    _record_llm_usage(
        LLMUsage(
            input_tokens=estimate_tokens(text), output_tokens=0, measured=False
        ),
        measured=False,
        who=who,
    )
    pseudo_ocr = {"fields": ocr_mod._kv_pairs_from_text(text), "tables": []}
    return extract_canonical_fields(pseudo_ocr), "heuristic"


def _effective_caller(caller: CallerContext, body_caller_id: Optional[str]) -> Optional[str]:
    """Identité retenue pour la traçabilité : l'identité VÉRIFIÉE prime ; en
    repli (clé de service seule), on accepte l'étiquette du corps pour conserver
    la granularité d'usage. Jamais l'inverse (une identité vérifiée ne peut être
    usurpée par un `caller_id` de corps)."""
    if not caller.is_service:
        return caller.caller_id
    return body_caller_id or (None if caller.caller_id == "service" else caller.caller_id)


@app.post("/audit")
def audit_endpoint(
    req: AuditRequest, caller: CallerContext = Depends(require_caller)
) -> Dict[str, Any]:
    who = _effective_caller(caller, req.caller_id)
    _gate("audit", who)
    usage_tracker.track("audit_documentaire_started", user_id=who, action_name="audit")
    document, mode = _resolve_document_fields(req.document, req.text, req.use_llm, who=who)
    reference = _load_reference(req.reference, req.reference_path, req.client_key)
    result = run_audit({"document": document, "reference": reference})
    result["_extraction_mode"] = mode
    usage_tracker.track(
        "audit_documentaire_completed",
        user_id=who,
        client_id=result.get("client_document"),
        action_name="audit",
        document_count=1,
    )
    return result


@app.post("/audit/file")
async def audit_file_endpoint(
    file: UploadFile = File(...),
    reference: Optional[str] = Form(default=None),
    reference_path: Optional[str] = Form(default=None),
    client_key: Optional[str] = Form(default=None),
    use_llm: bool = Form(default=False),
    caller_id: Optional[str] = Form(default=None),
    caller: CallerContext = Depends(require_caller),
) -> Dict[str, Any]:
    """Audit à partir d'un FICHIER (PDF/image) : OCR local -> extraction ->
    comparaison. Dégrade proprement si l'OCR est indisponible."""
    who = _effective_caller(caller, caller_id)
    _gate("audit", who)
    _gate("ocr", who)
    data = await file.read()
    validate_upload(file.filename or "", len(data))

    # WS2 — journal d'accès : « qui a accédé à quel document » (UPN + nom de
    # fichier hashés, jamais en clair).
    audit_log.record_document_accessed(
        user_id=who, document_id=file.filename, action_name="audit_file"
    )
    usage_tracker.track("ocr_started", user_id=who, action_name="audit_file")
    ocr_out = ocr_mod.extract(data, file.filename or "document")
    mode = ocr_out["metadata"]["extraction_mode"]
    if mode == "unavailable":
        usage_tracker.track("ocr_failed", status="error", user_id=who,
                            error_code="ocr_unavailable")
        if not use_llm:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "Extraction OCR indisponible",
                    "reason": ocr_out["metadata"].get("reason"),
                    "hint": "Activez l'OCR (tesseract/poppler) ou fournissez un texte/JSON déjà extrait.",
                },
            )
    usage_tracker.track("ocr_completed", user_id=who,
                        page_count=ocr_out["metadata"].get("pages", 0))

    # Champs : LLM sur le texte si demandé, sinon extraction canonique OCR.
    if use_llm and ocr_out.get("text"):
        document, _extract_mode = _resolve_document_fields(None, ocr_out["text"], True, who=who)
    else:
        document = extract_canonical_fields(ocr_out)

    if reference:
        try:
            ref_inline = json.loads(reference)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400,
                detail="Champ 'reference' invalide : JSON attendu (objet ou liste d'objets).",
            )
    else:
        ref_inline = None
    reference_record = _load_reference(ref_inline, reference_path, client_key)
    result = run_audit({"document": document, "reference": reference_record})
    result["_ocr_mode"] = mode
    usage_tracker.track("audit_documentaire_completed", user_id=who,
                        client_id=result.get("client_document"), document_count=1)
    return result


@app.post("/audit/reconcile/file")
async def audit_reconcile_file_endpoint(
    file: UploadFile = File(...),
    client_key: str = Form(...),
    use_llm: bool = Form(default=False),
    caller_id: Optional[str] = Form(default=None),
    caller: CallerContext = Depends(require_caller),
) -> Dict[str, Any]:
    """**RÉCONCILIATION contrat ↔ SI Fabric** (POC AC360) : OCR du document →
    champs → **référence LUE DANS LE SI FABRIC** (OneLake, par `client_key`) →
    audit → **verdict d'écarts**.

    Diffère de `/audit/file` : la référence n'est **PAS** fournie par l'appelant,
    elle est récupérée dans le SI Fabric (le cœur de la réconciliation). Client
    absent du SI / source non configurée ⇒ verdict `CLIENT_NON_TROUVE` (fail-closed,
    cf. `fabric_reference.fetch_client_reference`)."""
    who = _effective_caller(caller, caller_id)
    _gate("audit", who)
    _gate("ocr", who)
    data = await file.read()
    validate_upload(file.filename or "", len(data))
    audit_log.record_document_accessed(
        user_id=who, document_id=file.filename, action_name="audit_reconcile"
    )
    usage_tracker.track("ocr_started", user_id=who, action_name="audit_reconcile")
    ocr_out = ocr_mod.extract(data, file.filename or "document")
    mode = ocr_out["metadata"]["extraction_mode"]
    if mode == "unavailable" and not use_llm:
        usage_tracker.track("ocr_failed", status="error", user_id=who,
                            error_code="ocr_unavailable")
        raise HTTPException(
            status_code=422,
            detail={"error": "Extraction OCR indisponible",
                    "reason": ocr_out["metadata"].get("reason"),
                    "hint": "Activez l'OCR (tesseract/poppler) ou utilisez use_llm."},
        )
    if use_llm and ocr_out.get("text"):
        document, _ = _resolve_document_fields(None, ocr_out["text"], True, who=who)
    else:
        document = extract_canonical_fields(ocr_out)

    # ── Le maillon AC360 : la référence vient du SI Fabric, PAS de l'appelant. ──
    reference = fabric_reference.fetch_client_reference(client_key) or {}
    result = run_audit({"document": document, "reference": reference})
    result["_ocr_mode"] = mode
    result["_reference_source"] = (
        "fabric_si" if fabric_reference.fabric_reference_configured() else "non_configuree"
    )
    # Fiche de revue humaine prête à arbitrer (écarts + reco) sur verdict non conforme.
    result["fiche_revue"] = build_review_fiche(result, client_key=client_key)
    usage_tracker.track("audit_documentaire_completed", user_id=who,
                        client_id=result.get("client_document"), document_count=1)
    return result


# Borne fail-closed du nombre de contrats par lot (réponses bornées, anti-abus).
_MAX_RECONCILE_BATCH = 200


@app.post("/audit/reconcile/batch")
def audit_reconcile_batch_endpoint(
    req: ReconcileBatchRequest, caller: CallerContext = Depends(require_caller)
) -> Dict[str, Any]:
    """**RÉCONCILIATION DE PORTEFEUILLE** (lot) : réconcilie d'un coup une LISTE de
    contrats déjà extraits (``{document, client_key}``) contre le SI Fabric → liste
    de fiches de revue + **synthèse** (compteurs par verdict + à-revoir + invalides).

    Variante batch de `/audit/reconcile/file` : **pas d'OCR** (les champs sont fournis),
    la référence de CHAQUE client est lue dans le SI Fabric (`reconcile_batch`).
    **Fail-closed** : lot trop volumineux ⇒ 400 ; SI non configuré / client absent ⇒
    `CLIENT_NON_TROUVE` par contrat ; item sans document ⇒ `INVALIDE`. Lecture seule."""
    who = _effective_caller(caller, req.caller_id)
    _gate("audit", who)
    if len(req.items) > _MAX_RECONCILE_BATCH:
        raise HTTPException(
            status_code=400,
            detail={"error": "Lot trop volumineux",
                    "max": _MAX_RECONCILE_BATCH, "recu": len(req.items),
                    "hint": "Découpez le portefeuille en lots plus petits."},
        )
    usage_tracker.track("reconcile_batch_started", user_id=who,
                        action_name="reconcile_batch", document_count=len(req.items))
    rapport = fabric_reference.reconcile_batch(req.items)
    rapport["_reference_source"] = (
        "fabric_si" if fabric_reference.fabric_reference_configured() else "non_configuree"
    )
    usage_tracker.track("reconcile_batch_completed", user_id=who,
                        action_name="reconcile_batch",
                        document_count=rapport["synthese"]["total"])
    return rapport


@app.post("/rag/ask")
def rag_ask_endpoint(
    req: RagAskRequest, caller: CallerContext = Depends(require_caller)
) -> Dict[str, Any]:
    """RAG **non-agentique** souverain : récupère le(s) document(s) pertinent(s) du
    corpus fourni puis génère une réponse **grounded** en local (Ollama). Contourne le
    mur agentique d'Onyx 4.1.1 (#12, cf. [`app/rag_local.py`](rag_local.py)).
    **Fail-closed** : aucune source pertinente ⇒ refus explicite (`grounded=False`) ;
    génération KO ⇒ `grounded=False`, jamais d'invention."""
    who = _effective_caller(caller, req.caller_id)
    _gate("llm", who)
    usage_tracker.track("rag_ask_started", user_id=who, action_name="rag_ask")
    result = rag_local.answer(
        req.question, req.documents, generator=rag_local.ollama_generator, top_k=req.top_k
    )
    # La requête a réussi (200) ; l'issue métier (grounded / refus fail-closed) est
    # portée par le corps de réponse, pas par le statut d'usage.
    usage_tracker.track("rag_ask_completed", user_id=who, action_name="rag_ask",
                        document_count=len(result.get("sources") or []))
    return result


# ---------------------------------------------------------------------------
# 1bis. Audit OCR ASYNCHRONE (file Celery) — gated ONIX_QUEUE_ENABLED (WS-CW1)
# ---------------------------------------------------------------------------
@app.post("/audit/file/async", status_code=202)
async def audit_file_async_endpoint(
    file: UploadFile = File(...),
    reference: Optional[str] = Form(default=None),
    client_key: Optional[str] = Form(default=None),
    caller_id: Optional[str] = Form(default=None),
    caller: CallerContext = Depends(require_caller),
) -> Dict[str, Any]:
    """Met en file un audit OCR long (gros PDF / lot) et renvoie `202 Accepted` +
    `task_id`. L'API ne bloque pas : le pool `actions-worker` (Celery) traite la
    tâche et le résultat est récupérable via `GET /jobs/{task_id}`.

    Activé par `ONIX_QUEUE_ENABLED=true` (sinon `503`). Les mêmes garde-fous que
    l'audit synchrone s'appliquent (auth, kill-switch, validation d'upload)."""
    from . import celery_app

    if not celery_app.queue_enabled():
        raise HTTPException(
            status_code=503,
            detail="File asynchrone désactivée (ONIX_QUEUE_ENABLED non actif).",
        )
    who = _effective_caller(caller, caller_id)
    _gate("audit", who)
    _gate("ocr", who)
    data = await file.read()
    validate_upload(file.filename or "", len(data))
    audit_log.record_document_accessed(
        user_id=who, document_id=file.filename, action_name="audit_file_async"
    )

    ref_inline = None
    if reference:
        try:
            ref_inline = json.loads(reference)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=400,
                detail="Champ 'reference' invalide : JSON attendu (objet ou liste).",
            )

    import base64

    file_b64 = base64.b64encode(data).decode("ascii")
    async_result = celery_app.audit_file_async.delay(
        file_b64, file.filename or "document", ref_inline, {"client_key": client_key}
    )
    usage_tracker.track("backend_action_called", user_id=who, action_name="audit_file_async_enqueue")
    return {
        "status": "accepted",
        "task_id": async_result.id,
        "status_url": f"/jobs/{async_result.id}",
    }


@app.get("/jobs/{task_id}")
def job_status_endpoint(
    task_id: str, _: CallerContext = Depends(require_caller)
) -> Dict[str, Any]:
    """Statut/résultat d'une tâche asynchrone (file Celery). États Celery :
    PENDING / STARTED / SUCCESS / FAILURE / RETRY. En SUCCESS, `result` porte le
    verdict d'audit. Gated par `ONIX_QUEUE_ENABLED`."""
    from . import celery_app

    if not celery_app.queue_enabled():
        raise HTTPException(
            status_code=503,
            detail="File asynchrone désactivée (ONIX_QUEUE_ENABLED non actif).",
        )
    _gate("audit")
    res = celery_app.celery.AsyncResult(task_id)
    state = res.state
    payload: Dict[str, Any] = {"task_id": task_id, "state": state}
    if state == "SUCCESS":
        payload["result"] = res.result
    elif state == "FAILURE":
        # Ne pas fuiter de trace interne : message borné, sûr.
        payload["error"] = "task_failed"
    return payload


# ---------------------------------------------------------------------------
# 2. Génération de fiche .docx + téléchargement
# ---------------------------------------------------------------------------
@app.post("/generate/fiche")
def generate_fiche_endpoint(
    req: FicheRequest, caller: CallerContext = Depends(require_caller)
) -> Dict[str, Any]:
    who = _effective_caller(caller, req.caller_id)
    _gate("generate", who)
    # NB : le CONTENU de la fiche (.docx) est l'output explicitement demandé par
    # l'utilisateur — on ne le redacte donc PAS (ce serait corrompre le livrable).
    # La redaction PII vise les LOGS et les champs persistés en base d'usage/audit
    # (ici seul le client est tracé, et il l'est SOUS FORME HASHÉE).
    try:
        out = docgen.generate_fiche(
            req.client_name, req.summary, req.alert_points,
            extra_sections=req.extra_sections,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    usage_tracker.track("fiche_generated", user_id=who,
                        client_id=req.client_name, action_name="generate_fiche")
    return {
        "status": "success",
        "job_id": out["job_id"],
        "filename": out["filename"],
        "download_url": f"/download/{out['job_id']}",
    }


_DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


@app.get("/download/{job_id}")
def download_endpoint(
    job_id: str, caller: CallerContext = Depends(require_caller)
) -> Response:
    """Télécharge le `.docx` d'un job. Lit depuis le backend actif (disque local
    par défaut, S3/MinIO si `ONIX_OBJECT_STORE=s3`) → fonctionne en multi-réplica
    (toute réplique sert le fichier, qu'elle l'ait généré ou non)."""
    _gate("generate", caller.caller_id if not caller.is_service else None)
    audit_log.record_document_accessed(
        user_id=(None if caller.is_service else caller.caller_id),
        document_id=job_id,
        action_name="download",
    )
    docx_files = docgen.list_job_docx(job_id)
    if not docx_files:
        raise HTTPException(status_code=404, detail="Job introuvable ou sans fichier.")
    filename = docx_files[0]
    try:
        content = docgen.read_download(job_id, filename)
    except (PermissionError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    return Response(
        content=content,
        media_type=_DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# 3. Tâches / relances locales
# ---------------------------------------------------------------------------
@app.post("/tasks")
def create_task_endpoint(
    req: TaskRequest, caller: CallerContext = Depends(require_caller)
) -> Dict[str, Any]:
    who = _effective_caller(caller, req.caller_id)
    _gate("tasks", who)
    # WS2 — DLP egress : si un webhook_url est fourni, il DOIT être autorisé par
    # l'allowlist (refus AVANT toute création/appel) pour empêcher l'exfiltration.
    if req.webhook_url:
        try:
            dlp.check_egress(req.webhook_url)
        except dlp.EgressDenied as e:
            raise HTTPException(status_code=403, detail=f"Destination refusée (DLP) : {e}")
    # `notes` est un champ LIBRE -> redaction PII avant persistance.
    safe_notes = safe_logger.redact_text(req.notes) if req.notes else None
    try:
        record = tasks_mod.create_task(
            title=req.title, due_date=req.due_date, client_id=req.client_id,
            owner=who, notes=safe_notes, webhook_url=req.webhook_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Push optionnel vers un système externe (déjà allowlisté ci-dessus).
    if req.webhook_url:
        res = notify_mod.send_webhook(
            f"Nouvelle tâche: {req.title}" + (f" (échéance {req.due_date})" if req.due_date else ""),
            url=req.webhook_url,
        )
        tasks_mod.update_webhook_status(record["task_id"], res.get("status", "unknown"))
        record["webhook_status"] = res.get("status")
    usage_tracker.track("task_created", user_id=who, action_name="create_task")
    record.pop("webhook_url", None)
    return record


@app.get("/tasks")
def list_tasks_endpoint(
    status: Optional[str] = None, _: CallerContext = Depends(require_caller)
) -> Dict[str, Any]:
    _gate("tasks")
    try:
        items = tasks_mod.list_tasks(status=status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"count": len(items), "tasks": items}


# ---------------------------------------------------------------------------
# 4. Notification
# ---------------------------------------------------------------------------
@app.post("/notify")
def notify_endpoint(
    req: NotifyRequest, caller: CallerContext = Depends(require_caller)
) -> Dict[str, Any]:
    who = _effective_caller(caller, req.caller_id)
    _gate("notify", who)
    # WS2 — DLP egress sur le provider webhook : la cible (req.url ou défaut) doit
    # être allowlistée. Le provider SMTP exige STARTTLS par défaut (cf. notify.py).
    # On ne contrôle QUE s'il y a réellement une cible : sans URL, aucun egress
    # n'a lieu (notify renvoie 'skipped'), donc rien à filtrer.
    if (req.provider or "webhook").lower() == "webhook":
        target = req.url or os.environ.get("ONIX_NOTIFY_WEBHOOK", "").strip()
        if target:
            try:
                dlp.check_egress(target)
            except dlp.EgressDenied as e:
                raise HTTPException(status_code=403, detail=f"Destination refusée (DLP) : {e}")
    result = notify_mod.notify(
        provider=req.provider, message=req.message, subject=req.subject,
        url=req.url, to=req.to, extra=req.extra,
    )
    usage_tracker.track(
        "notification_sent",
        status="ok" if result.get("status") in ("sent", "skipped") else "error",
        user_id=who, action_name=f"notify_{req.provider}",
    )
    return result


# ---------------------------------------------------------------------------
# 5. Usage
# ---------------------------------------------------------------------------
@app.post("/usage")
def usage_endpoint(
    req: UsageRequest, _: CallerContext = Depends(require_caller)
) -> Dict[str, Any]:
    _gate("usage")
    # `action_name` / `safe_error_message` peuvent être libres -> redaction.
    try:
        event = usage_tracker.track(
            req.event_type, status=req.status, user_id=req.user_id,
            client_id=req.client_id,
            action_name=safe_logger.redact_text(req.action_name) if req.action_name else None,
            document_count=req.document_count, page_count=req.page_count,
            estimated_tokens_input=req.estimated_tokens_input,
            estimated_tokens_output=req.estimated_tokens_output,
            estimated_cost_eur=req.estimated_cost_eur,
            measured=req.measured,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"recorded": True, "event_id": event["event_id"]}


@app.get("/usage/summary")
def usage_summary_endpoint(_: CallerContext = Depends(require_caller)) -> Dict[str, Any]:
    _gate("usage")
    return usage_tracker.summary()


# ---------------------------------------------------------------------------
# 6. Coût (FinOps)
# ---------------------------------------------------------------------------
@app.get("/cost")
def cost_endpoint(_: CallerContext = Depends(require_caller)) -> Dict[str, Any]:
    _gate("cost")
    summary = usage_tracker.summary()
    spent = summary.get("estimated_cost_eur", 0.0)
    return {
        "rate_card": cost_tracker.load_rate_card(),
        "spent_eur": spent,
        "budget": cost_tracker.check_budget(spent),
        # FinOps : ventilation tokens MESURÉS (Ollama eval_count) vs ESTIMÉS
        # (chars/4) — pour des chiffres crédibles côté client (cf. docs/FINOPS.md).
        "tokens": summary.get("tokens", {}),
    }


@app.post("/cost/estimate")
def cost_estimate_endpoint(
    req: CostEstimateRequest, _: CallerContext = Depends(require_caller)
) -> Dict[str, Any]:
    _gate("cost")
    try:
        est = cost_tracker.estimate_cost(
            req.cost_center, req.quantity, unit=req.unit,
            client_id=req.client_id, use_case=req.use_case,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    usage_tracker.track("cost_estimated", estimated_cost_eur=est["estimated_cost_eur"])
    return est


# ---------------------------------------------------------------------------
# 7. Administration (kill-switch / flags / blocage)
# ---------------------------------------------------------------------------
@app.post("/admin/control")
def admin_control_endpoint(
    req: AdminControlRequest, _: CallerContext = Depends(require_admin)
) -> Dict[str, Any]:
    record = admin_state.apply_control(
        admin_id=req.admin_id, action=req.action, scope=req.scope,
        target_id=req.target_id, reason=req.reason,
    )
    if record["result"] == "noop":
        return JSONResponse(status_code=400, content=record)
    return record


@app.get("/admin/state")
def admin_state_endpoint(_: CallerContext = Depends(require_admin)) -> Dict[str, Any]:
    return admin_state.current_state()


@app.get("/admin/audit/verify")
def admin_audit_verify_endpoint(_: CallerContext = Depends(require_admin)) -> Dict[str, Any]:
    """WS2 — vérifie l'intégrité du journal d'audit CHAÎNÉ (tamper-evident).
    `ok=false` + `broken_at` si une ligne a été modifiée/supprimée/réordonnée."""
    return audit_log.verify_chain()


# ---------------------------------------------------------------------------
# 9. Journalisation d'accès (RGPD : traçabilité, UPN hashés)
# ---------------------------------------------------------------------------
@app.post("/access/log")
def access_log_endpoint(
    req: AccessLogRequest, caller: CallerContext = Depends(require_caller)
) -> Dict[str, Any]:
    """Émet un événement d'accès (`document_accessed` / `rag_search_executed`).
    Identité et identifiants HASHÉS ; la requête RAG n'est jamais stockée en clair."""
    who = _effective_caller(caller, req.user_id)
    _gate("usage")
    event = (req.event or "").strip()
    if event == "document_accessed":
        rec = audit_log.record_document_accessed(
            user_id=who, document_id=req.document_id, client_id=req.client_id
        )
    elif event == "rag_search_executed":
        rec = audit_log.record_rag_search(
            user_id=who, query=req.query, client_id=req.client_id
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="event doit être 'document_accessed' ou 'rag_search_executed'.",
        )
    # Ne JAMAIS répondre « journalisé » si la persistance a échoué (sinon une
    # trace d'accès RGPD est perdue à l'insu de l'appelant / de la conformité).
    if not rec.get("_persisted", True):
        raise HTTPException(
            status_code=500, detail="Échec de persistance du journal d'accès."
        )
    return {"recorded": True, "event_id": rec["event_id"], "event_type": rec["event_type"]}


# ---------------------------------------------------------------------------
# 10. Rétention & effacement (RGPD art. 5-1-e & art. 17) — réservé admin
# ---------------------------------------------------------------------------
@app.post("/admin/retention/purge")
def retention_purge_endpoint(
    req: RetentionPurgeRequest, _: CallerContext = Depends(require_admin)
) -> Dict[str, Any]:
    """Purge par âge (TTL) : usage_events, tâches terminées, .docx expirés."""
    return retention_mod.purge_by_age(days=req.days, purge_files=req.purge_files)


@app.post("/admin/retention/erase")
def retention_erase_endpoint(
    req: SubjectErasureRequest, _: CallerContext = Depends(require_admin)
) -> Dict[str, Any]:
    """Effacement ciblé d'un sujet (droit à l'effacement, art. 17). Le sujet est
    désigné par son identifiant en clair (hashé ici) OU par son hash."""
    try:
        return retention_mod.erase_subject(
            subject_id=req.subject_id,
            subject_hash=req.subject_hash,
            erase_files=req.erase_files,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
