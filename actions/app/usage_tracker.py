"""usage_tracker — Événements d'usage (onix-actions).

Porte usage_tracker d'AC360 : construit des événements typés, hashe SHA-256 tout
identifiant (UPN/utilisateur/client) — JAMAIS en clair — et les persiste en
SQLite, avec miroir JSONL optionnel (`ONIX_USAGE_SINK`).
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .admin_state import _connect, _lock, hash_id

VALID_EVENT_TYPES = {
    "conversation_started",
    "message_sent",
    "message_received",
    "rag_search_executed",
    "document_accessed",
    "ocr_started",
    "ocr_completed",
    "ocr_failed",
    "backend_action_called",
    "fiche_generated",
    "audit_documentaire_started",
    "audit_documentaire_completed",
    "task_created",
    "notification_sent",
    "cost_estimated",
    "budget_warning_triggered",
    "user_blocked",
    "user_unblocked",
    "service_emergency_stopped",
}

_VALID_STATUS = {"ok", "error", "blocked", "skipped"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _maybe_hash(raw: Optional[str]) -> Optional[str]:
    return hash_id(raw) if raw else None


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS usage_events ("
            " event_id TEXT PRIMARY KEY, timestamp_utc TEXT NOT NULL,"
            " environment TEXT, event_type TEXT NOT NULL, status TEXT,"
            " user_id_hash TEXT, client_id_hash TEXT, action_name TEXT,"
            " document_count INTEGER, page_count INTEGER,"
            " estimated_tokens_input INTEGER, estimated_tokens_output INTEGER,"
            " estimated_cost_eur REAL, cost_source TEXT,"
            " tokens_measured INTEGER DEFAULT 0,"
            " error_code TEXT, safe_error_message TEXT)"
        )
        # Migration douce : ajoute la colonne FinOps `tokens_measured` aux bases
        # déjà créées avant ce champ (CREATE TABLE IF NOT EXISTS ne la rajoute
        # pas). 1 = comptes MESURÉS (Ollama eval_count) ; 0 = ESTIMÉS (chars/4).
        cols = {row[1] for row in conn.execute("PRAGMA table_info(usage_events)")}
        if "tokens_measured" not in cols:
            conn.execute(
                "ALTER TABLE usage_events ADD COLUMN tokens_measured INTEGER DEFAULT 0"
            )
        conn.commit()


def build_usage_event(
    event_type: str,
    *,
    status: str = "ok",
    environment: Optional[str] = None,
    user_id: Optional[str] = None,
    client_id: Optional[str] = None,
    action_name: Optional[str] = None,
    document_count: int = 0,
    page_count: int = 0,
    estimated_tokens_input: int = 0,
    estimated_tokens_output: int = 0,
    estimated_cost_eur: float = 0.0,
    cost_source: str = "ESTIME",
    measured: bool = False,
    error_code: Optional[str] = None,
    safe_error_message: Optional[str] = None,
    event_id: Optional[str] = None,
    timestamp_utc: Optional[str] = None,
) -> Dict[str, Any]:
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(f"event_type inconnu : {event_type}")
    if status not in _VALID_STATUS:
        raise ValueError(f"status invalide : {status}")

    return {
        "event_id": event_id or str(uuid.uuid4()),
        "timestamp_utc": timestamp_utc or _now_iso(),
        "environment": environment or os.environ.get("ONIX_ENVIRONMENT", "dev"),
        "event_type": event_type,
        "status": status,
        "user_id_hash": _maybe_hash(user_id),
        "client_id_hash": _maybe_hash(client_id),
        "action_name": action_name,
        "document_count": int(document_count),
        "page_count": int(page_count),
        "estimated_tokens_input": int(estimated_tokens_input),
        "estimated_tokens_output": int(estimated_tokens_output),
        "estimated_cost_eur": round(float(estimated_cost_eur), 6),
        "cost_source": cost_source,
        # FinOps : True quand les tokens sont MESURÉS (Ollama eval_count), False
        # quand ils sont ESTIMÉS (chars/4) ou absents. Persiste en 0/1 (SQLite).
        "tokens_measured": 1 if measured else 0,
        "error_code": error_code,
        "safe_error_message": safe_error_message,
    }


def _to_jsonl_sink(event: Dict[str, Any]) -> None:
    sink_path = os.environ.get("ONIX_USAGE_SINK")
    if not sink_path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(sink_path)), exist_ok=True)
        with open(sink_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        pass


def emit_usage_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Persiste un événement d'usage. Renseigne `event['_persisted']` (bool) pour
    que l'appelant SACHE si l'écriture en base a réussi — important pour les
    événements de TRAÇABILITÉ d'accès (RGPD) : on ne doit pas répondre « journalisé »
    si la persistance a silencieusement échoué (disque plein, base verrouillée)."""
    persisted = True
    # Tolérance : un événement construit hors `build_usage_event` peut ne pas
    # porter `tokens_measured` -> défaut « estimé » (0) pour ne pas casser l'INSERT.
    event.setdefault("tokens_measured", 0)
    try:
        with _lock, _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO usage_events("
                " event_id, timestamp_utc, environment, event_type, status,"
                " user_id_hash, client_id_hash, action_name, document_count,"
                " page_count, estimated_tokens_input, estimated_tokens_output,"
                " estimated_cost_eur, cost_source, tokens_measured,"
                " error_code, safe_error_message)"
                " VALUES(:event_id,:timestamp_utc,:environment,:event_type,:status,"
                ":user_id_hash,:client_id_hash,:action_name,:document_count,"
                ":page_count,:estimated_tokens_input,:estimated_tokens_output,"
                ":estimated_cost_eur,:cost_source,:tokens_measured,"
                ":error_code,:safe_error_message)",
                event,
            )
            conn.commit()
    except Exception:
        persisted = False
    _to_jsonl_sink(event)
    event["_persisted"] = persisted
    return event


def track(event_type: str, **kwargs: Any) -> Dict[str, Any]:
    return emit_usage_event(build_usage_event(event_type, **kwargs))


def summary(limit: int = 1000) -> Dict[str, Any]:
    """Agrégats : total, par type, par statut, coût estimé cumulé."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT event_type, status, estimated_cost_eur, estimated_tokens_input,"
            " estimated_tokens_output, tokens_measured FROM usage_events"
            " ORDER BY timestamp_utc DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    by_type: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    total_cost = 0.0
    total_in = 0
    total_out = 0
    # FinOps : ventilation MESURÉ (Ollama eval_count) vs ESTIMÉ (chars/4) pour que
    # le client distingue les chiffres « ground truth » des heuristiques.
    measured_in = 0
    measured_out = 0
    estimated_in = 0
    estimated_out = 0
    measured_events = 0
    for r in rows:
        by_type[r["event_type"]] = by_type.get(r["event_type"], 0) + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        total_cost += float(r["estimated_cost_eur"] or 0.0)
        t_in = int(r["estimated_tokens_input"] or 0)
        t_out = int(r["estimated_tokens_output"] or 0)
        total_in += t_in
        total_out += t_out
        if int(r["tokens_measured"] or 0):
            measured_in += t_in
            measured_out += t_out
            if t_in or t_out:
                measured_events += 1
        else:
            estimated_in += t_in
            estimated_out += t_out
    return {
        "total_events": len(rows),
        "by_type": by_type,
        "by_status": by_status,
        "estimated_cost_eur": round(total_cost, 6),
        "estimated_tokens_input": total_in,
        "estimated_tokens_output": total_out,
        # Ground truth vs heuristique (cf. docs/FINOPS.md).
        "tokens": {
            "measured_input": measured_in,
            "measured_output": measured_out,
            "estimated_input": estimated_in,
            "estimated_output": estimated_out,
            "measured_events": measured_events,
        },
    }
