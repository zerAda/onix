"""admin_state — Kill-switch, feature flags et blocage utilisateurs (onix-actions).

Porte la logique d'AC360 (feature_flags + admin_controls), généricisée et
*persistée* : les contrôles administrateur (`/admin/control`) mutent réellement
un état SQLite qui GATE les endpoints (global + par-fonction + par-utilisateur).

Deux sources d'état, combinées (le plus restrictif gagne) :
  1. variables d'environnement `ONIX_*_ENABLED` (valeur par défaut au démarrage) ;
  2. surcharges runtime en base (table `admin_state`), posées par un admin via
     l'API et qui survivent au redémarrage du process.

Aucun identifiant en clair : les UPN/utilisateurs sont hashés SHA-256.
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

# WS-CW1 — couche d'accès DB factorisée (SQLite par défaut, Postgres opt-in).
# `_connect` et `_lock` sont RÉEXPORTÉS depuis `db` pour que les modules
# historiques (`tasks`, `usage_tracker`, `audit_log`, `retention`) qui font
# `from .admin_state import _connect, _lock, hash_id` continuent de fonctionner
# sans modification, quel que soit le backend.
from . import db
from .db import _connect, _lock  # noqa: F401 (réexport pour compat)

_logger = logging.getLogger("onix.actions.admin")

# Fonctions « métier » gatées (alignées sur AC360 + ajouts onix-actions).
FEATURES = ("audit", "generate", "tasks", "notify", "usage", "cost", "ocr", "llm")

FEATURE_ENV = {f: f"ONIX_{f.upper()}_ENABLED" for f in FEATURES}
_GLOBAL_ENV = "ONIX_GLOBAL_ENABLED"

_TRUE = {"1", "true", "yes", "on", "enabled"}
_FALSE = {"0", "false", "no", "off", "disabled"}

# Actions admin reconnues -> portée valide.
_ACTION_SCOPES: Dict[str, Any] = {
    "enable_global": "global",
    "disable_global": "global",
    "emergency_stop": "global",       # alias destructif de disable_global
    "enable_feature": set(FEATURES),
    "disable_feature": set(FEATURES),
    "block_user": "user",
    "unblock_user": "user",
}

def db_path() -> str:
    """Chemin du fichier SQLite (mode par défaut). Conservé pour rétro-compat :
    plusieurs tests lisent `admin_state.db_path()` pour ouvrir la base directement.
    En mode Postgres, ce chemin n'est pas utilisé (cf. db.connect)."""
    return db.sqlite_path()


def hash_id(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    return hashlib.sha256(str(raw).strip().lower().encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS admin_state ("
            " key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_utc TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS admin_audit ("
            " action_id TEXT PRIMARY KEY, timestamp_utc TEXT NOT NULL,"
            " admin_id_hash TEXT, action TEXT, scope TEXT, target_hash TEXT,"
            " reason TEXT, result TEXT)"
        )
        conn.commit()
    # WS2 — colonnes de chaînage HMAC du journal d'audit (migration idempotente).
    from . import audit_log

    audit_log.ensure_schema()


def _get_state(key: str) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM admin_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def _set_state(key: str, value: str) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO admin_state(key, value, updated_utc) VALUES(?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
            " updated_utc=excluded.updated_utc",
            (key, value, _now_iso()),
        )
        conn.commit()


def _env_flag(env_name: str, default: bool = True) -> bool:
    val = os.environ.get(env_name)
    if val is None or val.strip() == "":
        return default
    norm = val.strip().lower()
    if norm in _TRUE:
        return True
    if norm in _FALSE:
        return False
    # WS2 — FAIL-CLOSED sur valeur inconnue : une typo (« ON », « tru »…) ne doit
    # PAS ouvrir une fonction par défaut. On coupe (False) et on le journalise.
    # (AC360 faisait du fail-open ; WS2 inverse ce choix par sécurité.)
    _logger.warning(
        "Flag %s = valeur inconnue (%r) -> fail-closed (désactivé).",
        env_name,
        val.strip()[:32],
    )
    return False


def _flag_effective(key: str, env_name: str, default: bool = True) -> bool:
    """Surcharge runtime (base) prioritaire ; sinon variable d'env ; sinon défaut."""
    override = _get_state(key)
    if override is not None:
        return override.strip().lower() in _TRUE
    return _env_flag(env_name, default)


def is_global_enabled() -> bool:
    return _flag_effective("global_enabled", _GLOBAL_ENV, default=True)


def is_feature_enabled(feature: str) -> bool:
    if not is_global_enabled():
        return False
    env_name = FEATURE_ENV.get(feature)
    if env_name is None:
        return True
    return _flag_effective(f"feature:{feature}", env_name, default=True)


def is_user_blocked(user_id_hash: Optional[str]) -> bool:
    if not user_id_hash:
        return False
    return _get_state(f"blocked_user:{user_id_hash}") == "1"


def is_allowed(
    feature: str,
    user_id_hash: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    if not is_global_enabled():
        return False, "global_disabled"
    if not is_feature_enabled(feature):
        return False, "feature_disabled"
    if is_user_blocked(user_id_hash):
        return False, "user_blocked"
    return True, None


_USER_MESSAGES = {
    "global_disabled": "Le service onix-actions est temporairement suspendu par un administrateur.",
    "feature_disabled": "Cette fonctionnalité est temporairement désactivée par un administrateur.",
    "user_blocked": "Votre accès est actuellement suspendu. Contactez votre administrateur.",
}


def blocked_message(reason: Optional[str]) -> str:
    return _USER_MESSAGES.get(reason or "", "Action non disponible pour le moment.")


def _scope_valid(action: str, scope: str) -> bool:
    expected = _ACTION_SCOPES.get(action)
    if expected is None:
        return False
    if isinstance(expected, set):
        return scope in expected
    return scope == expected


def apply_control(
    *,
    admin_id: str,
    action: str,
    scope: str,
    target_id: Optional[str] = None,
    reason: Optional[str] = None,
    action_id: Optional[str] = None,
    timestamp_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Applique un contrôle admin et persiste l'état. Authn/authz (clé API +
    droit admin) sont assurées par la couche FastAPI : ici on suppose l'appelant
    déjà autorisé. Retourne un journal hashé (jamais d'identifiant en clair)."""
    valid = action in _ACTION_SCOPES and _scope_valid(action, scope)
    result = "applied" if valid else "noop"

    if valid:
        if action in ("disable_global", "emergency_stop"):
            _set_state("global_enabled", "false")
        elif action == "enable_global":
            _set_state("global_enabled", "true")
        elif action == "disable_feature":
            _set_state(f"feature:{scope}", "false")
        elif action == "enable_feature":
            _set_state(f"feature:{scope}", "true")
        elif action == "block_user" and target_id:
            _set_state(f"blocked_user:{hash_id(target_id)}", "1")
        elif action == "unblock_user" and target_id:
            _set_state(f"blocked_user:{hash_id(target_id)}", "0")
        else:
            result = "noop"

    # WS2 — `reason` est un champ LIBRE : on le passe par la porte de redaction
    # PII avant persistance (un admin pourrait y coller un e-mail/IBAN/NIR).
    from .safe_logger import redact_text

    safe_reason = redact_text(reason) if reason else None
    record = {
        "action_id": action_id or str(uuid.uuid4()),
        "timestamp_utc": timestamp_utc or _now_iso(),
        "admin_id_hash": hash_id(admin_id),
        "action": action if action in _ACTION_SCOPES else "disable_global",
        "scope": scope,
        "target_hash": hash_id(target_id) if target_id else None,
        "reason": safe_reason,
        "result": result,
    }
    # WS2 — journal d'audit append-only CHAÎNÉ (HMAC tamper-evident). Import
    # paresseux pour éviter une dépendance circulaire (audit_log -> admin_state).
    from . import audit_log

    return audit_log.append_audit(record)


def current_state() -> Dict[str, Any]:
    """État effectif de tous les flags + utilisateurs bloqués (hashés)."""
    with _connect() as conn:
        blocked = [
            r["key"].split("blocked_user:", 1)[1]
            for r in conn.execute(
                "SELECT key FROM admin_state WHERE key LIKE 'blocked_user:%' AND value='1'"
            ).fetchall()
        ]
        recent = [
            dict(r)
            for r in conn.execute(
                "SELECT action, scope, target_hash, result, timestamp_utc"
                " FROM admin_audit ORDER BY timestamp_utc DESC LIMIT 10"
            ).fetchall()
        ]
    return {
        "global_enabled": is_global_enabled(),
        "features": {f: is_feature_enabled(f) for f in FEATURES},
        "blocked_users_hashed": blocked,
        "recent_actions": recent,
    }
