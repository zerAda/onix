"""tasks — Relances / tâches LOCALES (onix-actions).

Remplace l'intégration Planner d'AC360 par un stockage LOCAL (SQLite), sans
dépendance cloud. Un `webhook_url` optionnel permet de POSTER la tâche vers un
système externe (ex: outil de ticketing) au moment de la création — mais rien
n'est requis : le service est autonome.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .admin_state import _connect, _lock, hash_id

_VALID_STATUS = {"open", "done", "cancelled"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tasks ("
            " task_id TEXT PRIMARY KEY, created_utc TEXT NOT NULL,"
            " title TEXT NOT NULL, due_date TEXT, client_id_hash TEXT,"
            " owner_hash TEXT, status TEXT NOT NULL, notes TEXT,"
            " webhook_url TEXT, webhook_status TEXT)"
        )
        conn.commit()


def create_task(
    *,
    title: str,
    due_date: Optional[str] = None,
    client_id: Optional[str] = None,
    owner: Optional[str] = None,
    notes: Optional[str] = None,
    webhook_url: Optional[str] = None,
    webhook_status: Optional[str] = None,
) -> Dict[str, Any]:
    if not (title or "").strip():
        raise ValueError("title requis")
    record = {
        "task_id": str(uuid.uuid4()),
        "created_utc": _now_iso(),
        "title": title.strip()[:500],
        "due_date": (due_date or None),
        "client_id_hash": hash_id(client_id) if client_id else None,
        "owner_hash": hash_id(owner) if owner else None,
        "status": "open",
        "notes": (notes or None),
        "webhook_url": (webhook_url or None),
        "webhook_status": webhook_status,
    }
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO tasks(task_id, created_utc, title, due_date,"
            " client_id_hash, owner_hash, status, notes, webhook_url, webhook_status)"
            " VALUES(:task_id,:created_utc,:title,:due_date,:client_id_hash,"
            ":owner_hash,:status,:notes,:webhook_url,:webhook_status)",
            record,
        )
        conn.commit()
    return record


def list_tasks(status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    query = (
        "SELECT task_id, created_utc, title, due_date, client_id_hash,"
        " owner_hash, status, notes, webhook_status FROM tasks"
    )
    params: List[Any] = []
    if status:
        if status not in _VALID_STATUS:
            raise ValueError(f"status invalide : {status}")
        query += " WHERE status=?"
        params.append(status)
    query += " ORDER BY created_utc DESC LIMIT ?"
    params.append(int(limit))
    with _connect() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def update_webhook_status(task_id: str, status: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE tasks SET webhook_status=? WHERE task_id=?", (status, task_id)
        )
        conn.commit()
