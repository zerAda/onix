"""Tests de la chaîne d'audit tamper-evident (`app.audit_log`).

Couvre la politique **fail-closed anti-downgrade** de `verify_chain()` :
  * une chaîne HMAC normale vérifie (non-régression) ;
  * une chaîne keyless pure (sans clé) vérifie en best-effort (non-régression) ;
  * un attaquant qui réécrit une ligne en SHA-256 keyless ALORS QU'UNE CLÉ EST
    CONFIGURÉE est détecté comme DOWNGRADE (rupture) — c'est le coeur du fix M1 ;
  * une ligne HMAC alors que la clé a disparu est invérifiable (rupture).

Isolation : on pointe `ONIX_ACTIONS_DB` vers un fichier SQLite temporaire et on
recharge `app.admin_state` puis `app.audit_log` pour relire les env vars (même
mécanique que `conftest.py`).
"""
from __future__ import annotations

import importlib

import pytest


def _reload_audit(monkeypatch, tmp_path, *, hmac_key):
    """Prépare une base d'audit ISOLÉE et recharge le module `audit_log`.

    Si `hmac_key` est None, la clé HMAC est retirée (mode keyless) ; sinon elle
    est définie. Retourne le module `app.audit_log` fraîchement rechargé."""
    monkeypatch.setenv("ONIX_ACTIONS_DB", str(tmp_path / "audit.sqlite"))
    monkeypatch.delenv("ONIX_ACTIONS_DB_URL", raising=False)  # forcer SQLite
    if hmac_key is None:
        monkeypatch.delenv("ONIX_ACTIONS_AUDIT_HMAC_KEY", raising=False)
    else:
        monkeypatch.setenv("ONIX_ACTIONS_AUDIT_HMAC_KEY", hmac_key)
    # Recharger admin_state EN PREMIER (il réexporte _connect/_lock liés à db),
    # puis audit_log qui en dépend — sinon verrous/connexions divergents.
    import app.db as db
    import app.admin_state as admin_state
    import app.audit_log as al

    importlib.reload(db)
    importlib.reload(admin_state)
    importlib.reload(al)
    return al


def _exemple_record(al):
    """Un enregistrement portant tous les champs signés (valeurs factices)."""
    return {k: f"v_{k}" for k in al._SIGNED_FIELDS}


# --- Non-régression : chaîne HMAC normale -----------------------------------
def test_verify_chain_hmac_normale_ok(monkeypatch, tmp_path):
    al = _reload_audit(monkeypatch, tmp_path, hmac_key="cle-de-test-32-octets-minimum!!")
    al.append_audit(_exemple_record(al))
    al.append_audit(dict(_exemple_record(al), action_id="v_action_id_2", action="B"))
    res = al.verify_chain()
    assert res["ok"] is True
    assert res["count"] == 2


# --- Non-régression : chaîne keyless pure -----------------------------------
def test_verify_chain_keyless_pure_ok(monkeypatch, tmp_path):
    al = _reload_audit(monkeypatch, tmp_path, hmac_key=None)
    al.append_audit(_exemple_record(al))
    res = al.verify_chain()
    assert res["ok"] is True
    assert res["count"] == 1


# --- Coeur du fix M1 : downgrade keyless détecté quand la clé est présente ---
def test_verify_chain_detecte_downgrade_keyless_quand_cle_presente(monkeypatch, tmp_path):
    # Base d'audit isolée + clé HMAC configurée.
    al = _reload_audit(monkeypatch, tmp_path, hmac_key="cle-de-test-32-octets-minimum!!")
    rec = _exemple_record(al)
    al.append_audit(rec)  # écrit en hmac-sha256
    # Attaquant : modifie le contenu + réécrit entry_hash en SHA-256 keyless +
    # algo='sha256' (recalculable SANS la clé). Sans le fix, la chaîne "vérifie".
    forged = dict(rec, action="ESCALADE_PRIV", result="ok")
    forged_hash = al.hashlib.sha256(
        (al._GENESIS + al._canonical(forged)).encode("utf-8")
    ).hexdigest()
    with al._lock, al._connect() as conn:
        conn.execute(
            "UPDATE admin_audit SET action=?, result=?, entry_hash=?, algo='sha256'"
            " WHERE seq=1",
            ("ESCALADE_PRIV", "ok", forged_hash),
        )
        conn.commit()
    res = al.verify_chain()
    assert res["ok"] is False
    assert "downgrade" in (res.get("reason") or "").lower()


# --- Fail-closed : ligne HMAC mais clé disparue = invérifiable = rupture -----
def test_verify_chain_hmac_sans_cle_est_invérifiable(monkeypatch, tmp_path):
    # On écrit en HMAC...
    al = _reload_audit(monkeypatch, tmp_path, hmac_key="cle-de-test-32-octets-minimum!!")
    al.append_audit(_exemple_record(al))
    # ... puis la clé disparaît (rotation/perte). La ligne hmac-sha256 ne peut
    # plus être recalculée : fail-closed -> rupture, jamais "ok" silencieux.
    al = _reload_audit(monkeypatch, tmp_path, hmac_key=None)
    res = al.verify_chain()
    assert res["ok"] is False
    assert "clé" in (res.get("reason") or "").lower() or "cle" in (res.get("reason") or "").lower()
