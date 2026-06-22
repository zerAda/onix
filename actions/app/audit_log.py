"""audit_log — Journal d'audit administrateur INVIOLABLE (append-only, chaîné).

WS2 — Journalisation d'accès & audit tamper-evident :

  * **Chaînage HMAC** du journal `admin_audit` : chaque enregistrement porte un
    `prev_hash` (hash du précédent) et un `entry_hash = HMAC(secret, prev_hash ||
    contenu_canonique)`. Toute modification/suppression d'une ligne casse la
    chaîne en aval -> détectable par `verify_chain()`. C'est un journal
    **append-only tamper-evident** (OWASP ASVS V7 : intégrité des logs).
  * **Helpers d'accès** : `record_document_accessed` / `record_rag_search`
    émettent des événements d'usage typés avec **UPN hashés** (jamais en clair),
    pour tracer « qui a accédé à quoi » sans stocker d'identité personnelle.

Le secret de chaînage est `ONIX_ACTIONS_AUDIT_HMAC_KEY`. S'il est absent, le
chaînage **dégrade** vers un hash simple (SHA-256) — toujours tamper-evident
contre une réécriture naïve, mais sans la garantie cryptographique liée au
secret ; un avertissement est journalisé. En production, définir la clé.

Schéma SQLite (créé/migré ici, table `admin_audit` partagée avec admin_state) :
colonnes de base + `prev_hash`, `entry_hash`, `seq` (numéro de séquence).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any, Dict, List, Optional

from .admin_state import _connect, _lock, hash_id
from . import db
from . import usage_tracker

_logger = logging.getLogger("onix.actions.audit")

# Valeur d'amorçage de la chaîne (premier prev_hash).
_GENESIS = "0" * 64

# Colonnes signées, dans un ORDRE FIXE (le canonical form en dépend).
_SIGNED_FIELDS = (
    "action_id",
    "timestamp_utc",
    "admin_id_hash",
    "action",
    "scope",
    "target_hash",
    "reason",
    "result",
)


def _audit_secret() -> Optional[str]:
    return (os.environ.get("ONIX_ACTIONS_AUDIT_HMAC_KEY") or "").strip() or None


def ensure_schema() -> None:
    """Ajoute les colonnes de chaînage à `admin_audit` si absentes (migration
    idempotente, compatible avec une base AC360 préexistante)."""
    with _lock, _connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS admin_audit ("
            " action_id TEXT PRIMARY KEY, timestamp_utc TEXT NOT NULL,"
            " admin_id_hash TEXT, action TEXT, scope TEXT, target_hash TEXT,"
            " reason TEXT, result TEXT)"
        )
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(admin_audit)").fetchall()}
        if "seq" not in cols:
            conn.execute("ALTER TABLE admin_audit ADD COLUMN seq INTEGER")
        if "prev_hash" not in cols:
            conn.execute("ALTER TABLE admin_audit ADD COLUMN prev_hash TEXT")
        if "entry_hash" not in cols:
            conn.execute("ALTER TABLE admin_audit ADD COLUMN entry_hash TEXT")
        if "algo" not in cols:
            # Marqueur d'algorithme de chaînage PAR LIGNE. ATTENTION (M1) : cet
            # algo stocké NE pilote PAS la vérification (sinon downgrade keyless
            # silencieux possible). `verify_chain()` impose l'algo selon la
            # présence d'une clé et traite toute divergence comme une rupture.
            conn.execute("ALTER TABLE admin_audit ADD COLUMN algo TEXT")
        conn.commit()


def _canonical(record: Dict[str, Any]) -> str:
    """Sérialisation canonique déterministe des champs signés (ordre fixe)."""
    payload = {k: record.get(k) for k in _SIGNED_FIELDS}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_entry_hash(
    prev_hash: str, record: Dict[str, Any], algo: Optional[str] = None
) -> str:
    """Hash chaîné d'un enregistrement selon `algo` :
      * « hmac-sha256 » : HMAC(secret, prev_hash || canonical) ;
      * « sha256 »      : SHA-256(prev_hash || canonical) (repli sans clé).
    Si `algo` est None, on choisit selon la présence d'une clé (écriture)."""
    material = (prev_hash + _canonical(record)).encode("utf-8")
    secret = _audit_secret()
    effective = algo or ("hmac-sha256" if secret else "sha256")
    if effective == "hmac-sha256":
        if not secret:
            # Demande de vérif HMAC sans clé disponible : impossible de recalculer.
            return "<no-key>"
        return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()
    return hashlib.sha256(material).hexdigest()


# Clé d'avis (advisory lock) Postgres pour sérialiser l'append de la chaîne
# d'audit ENTRE répliques (arbitraire mais stable ; espace bigint).
_AUDIT_ADVISORY_LOCK_KEY = 0x0117A0D17  # "onix audit"


def _last_chain(conn: Any) -> tuple[int, str]:
    """Retourne (dernier seq, dernier entry_hash) ou (0, GENESIS) si vide.

    En Postgres, on prend d'abord un **verrou d'avis transactionnel**
    (`pg_advisory_xact_lock`) : il SÉRIALISE le cycle lecture-du-dernier-maillon +
    insertion ENTRE répliques (le verrou applicatif `_lock` ne couvre qu'un
    process). Le verrou est relâché automatiquement au COMMIT/ROLLBACK de la
    transaction courante — donc la fenêtre critique est exactement l'append. En
    SQLite (mono-process), `_lock` + l'écriture sérialisée suffisent."""
    if db.is_postgres():
        # Placeholder SQLite-style `?` (traduit en %s) + paramètre => pas de
        # collision avec le doublement des `%` de l'adaptateur.
        conn.execute("SELECT pg_advisory_xact_lock(?)", (_AUDIT_ADVISORY_LOCK_KEY,))
    row = conn.execute(
        "SELECT seq, entry_hash FROM admin_audit"
        " WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    if not row or row["seq"] is None:
        return 0, _GENESIS
    return int(row["seq"]), (row["entry_hash"] or _GENESIS)


def append_audit(record: Dict[str, Any]) -> Dict[str, Any]:
    """Insère un enregistrement d'audit en chaînant son hash au précédent.

    `record` doit porter les champs `_SIGNED_FIELDS` (tels que produits par
    `admin_state.apply_control`). Retourne le record enrichi de `seq`,
    `prev_hash`, `entry_hash`. Append-only : on n'UPDATE/DELETE jamais."""
    if _audit_secret() is None:
        _logger.warning(
            "Journal d'audit chaîné sans clé HMAC (ONIX_ACTIONS_AUDIT_HMAC_KEY "
            "absente) : repli SHA-256, garantie cryptographique réduite."
        )
    ensure_schema()
    algo = "hmac-sha256" if _audit_secret() else "sha256"
    with _lock, _connect() as conn:
        last_seq, prev_hash = _last_chain(conn)
        seq = last_seq + 1
        entry_hash = compute_entry_hash(prev_hash, record, algo)
        enriched = dict(record)
        enriched.update(
            {"seq": seq, "prev_hash": prev_hash, "entry_hash": entry_hash, "algo": algo}
        )
        conn.execute(
            "INSERT INTO admin_audit(action_id, timestamp_utc, admin_id_hash,"
            " action, scope, target_hash, reason, result, seq, prev_hash, entry_hash, algo)"
            " VALUES(:action_id,:timestamp_utc,:admin_id_hash,:action,:scope,"
            ":target_hash,:reason,:result,:seq,:prev_hash,:entry_hash,:algo)",
            enriched,
        )
        conn.commit()
    return enriched


def verify_chain() -> Dict[str, Any]:
    """Recalcule la chaîne et détecte toute altération (modification, suppression,
    réordonnancement). Retourne {ok, count, broken_at?}.

    `broken_at` est le `seq` du premier enregistrement dont le hash recalculé ne
    correspond pas (ou dont le `prev_hash` ne suit pas la chaîne)."""
    ensure_schema()
    with _connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM admin_audit WHERE seq IS NOT NULL ORDER BY seq ASC"
            ).fetchall()
        ]
    prev = _GENESIS
    for idx, row in enumerate(rows, start=1):
        if int(row.get("seq") or -1) != idx:
            return {"ok": False, "count": len(rows), "broken_at": row.get("seq"),
                    "reason": "seq non contiguë"}
        if (row.get("prev_hash") or "") != prev:
            return {"ok": False, "count": len(rows), "broken_at": row.get("seq"),
                    "reason": "prev_hash incohérent"}
        # Politique anti-downgrade FAIL-CLOSED : l'algo de vérification ne doit
        # JAMAIS être dicté par l'algo STOCKÉ PAR LIGNE. Sinon un attaquant qui
        # écrit en base met algo='sha256' (keyless), recalcule entry_hash SANS la
        # clé HMAC, et la chaîne « vérifie » → dégradation silencieuse HMAC→keyless.
        key_present = _audit_secret() is not None
        stored_algo = (row.get("algo") or "").strip().lower()
        if key_present:
            # Clé configurée => politique HMAC stricte. Une ligne keyless (sha256)
            # ou tout autre algo est une tentative de DOWNGRADE -> rupture.
            if stored_algo and stored_algo != "hmac-sha256":
                return {"ok": False, "count": len(rows), "broken_at": row.get("seq"),
                        "reason": f"algo downgrade détecté (clé présente, ligne en '{stored_algo}')"}
            verify_algo = "hmac-sha256"
        else:
            # Pas de clé : best-effort sha256. Une ligne HMAC est invérifiable
            # (clé disparue/rotée) -> rupture explicite, jamais "ok" silencieux.
            if stored_algo == "hmac-sha256":
                return {"ok": False, "count": len(rows), "broken_at": row.get("seq"),
                        "reason": "ligne hmac-sha256 mais clé absente : vérification impossible"}
            verify_algo = "sha256"
        recomputed = compute_entry_hash(prev, row, verify_algo)
        if recomputed != (row.get("entry_hash") or ""):
            return {"ok": False, "count": len(rows), "broken_at": row.get("seq"),
                    "reason": "entry_hash incohérent"}
        prev = row["entry_hash"]
    return {"ok": True, "count": len(rows), "head_hash": prev}


# ---------------------------------------------------------------------------
# Helpers de journalisation d'accès (UPN hashés)
# ---------------------------------------------------------------------------
def record_document_accessed(
    *,
    user_id: Optional[str],
    document_id: Optional[str] = None,
    client_id: Optional[str] = None,
    action_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Émet `document_accessed` (UPN + document hashés). « Qui a accédé à quoi »
    sans stocker d'identité personnelle (RGPD : minimisation)."""
    return usage_tracker.track(
        "document_accessed",
        user_id=user_id,
        client_id=client_id,
        action_name=action_name or "document_accessed",
        document_count=1,
        # Le document est tracé via un hash dans le champ d'erreur sûr réutilisé
        # comme étiquette opaque (jamais l'identifiant en clair).
        safe_error_message=(("doc:" + (hash_id(document_id) or ""))[:64] if document_id else None),
    )


def record_rag_search(
    *,
    user_id: Optional[str],
    query: Optional[str] = None,
    client_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Émet `rag_search_executed` (UPN hashé). On NE stocke PAS la requête en
    clair (PII potentielle) : seulement sa longueur, à des fins de volumétrie."""
    qlen = len(query) if isinstance(query, str) else 0
    return usage_tracker.track(
        "rag_search_executed",
        user_id=user_id,
        client_id=client_id,
        action_name="rag_search",
        page_count=qlen,
    )
