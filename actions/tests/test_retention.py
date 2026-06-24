# -*- coding: utf-8 -*-
"""Tests rétention / effacement RGPD (art. 5-1-e & art. 17) — `app.retention`.

Invariant CLÉ : l'effacement d'un sujet (droit à l'effacement, art. 17) NE TOUCHE
JAMAIS au journal d'audit administrateur chaîné (obligation de traçabilité +
intégrité de la chaîne ; il ne porte de toute façon que des hash). On vérifie aussi
le fail-closed sans identifiant et le bornage fail-safe de la durée de rétention.

Isolation : base SQLite temporaire + rechargement des modules (même mécanique que
`test_audit_log.py`), admin_state EN PREMIER puis ses dépendants.
"""
from __future__ import annotations

import importlib

import pytest


def _reload(monkeypatch, tmp_path):
    monkeypatch.setenv("ONIX_ACTIONS_DB", str(tmp_path / "ret.sqlite"))
    monkeypatch.delenv("ONIX_ACTIONS_DB_URL", raising=False)
    monkeypatch.setenv("ONIX_ACTIONS_AUDIT_HMAC_KEY", "cle-de-test-32-octets-minimum!!")
    import app.db as db
    import app.admin_state as admin_state
    import app.audit_log as al
    import app.usage_tracker as ut
    import app.retention as ret

    for m in (db, admin_state, al, ut, ret):
        importlib.reload(m)
    return admin_state, al, ut, ret


def test_erase_subject_ne_touche_pas_au_journal_audit(monkeypatch, tmp_path):
    admin_state, al, ut, ret = _reload(monkeypatch, tmp_path)
    al.append_audit({k: f"v_{k}" for k in al._SIGNED_FIELDS})
    al.append_audit(dict({k: f"v_{k}" for k in al._SIGNED_FIELDS}, action_id="a2"))
    assert al.verify_chain()["ok"] is True

    def _audit_count():
        with al._connect() as c:
            return c.execute("SELECT COUNT(*) FROM admin_audit").fetchone()[0]

    before = _audit_count()
    # Une trace d'usage rattachée au sujet, puis effacement art.17.
    ut.track("audit_documentaire_started", user_id="user@corp.local", action_name="x")
    ret.erase_subject("user@corp.local", erase_files=False)

    # Le journal d'audit chaîné reste INTACT (count + chaîne vérifiable) — invariant clé.
    assert _audit_count() == before
    assert al.verify_chain()["ok"] is True


def test_erase_subject_failclosed_sans_identifiant(monkeypatch, tmp_path):
    _, _, _, ret = _reload(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        ret.erase_subject(None, subject_hash=None)


def test_retention_days_borne_et_failsafe(monkeypatch, tmp_path):
    _, _, _, ret = _reload(monkeypatch, tmp_path)
    monkeypatch.setenv("ONIX_RETENTION_DAYS", "30")
    assert ret._retention_days() == 30
    monkeypatch.setenv("ONIX_RETENTION_DAYS", "0")
    assert ret._retention_days() == 365   # <= 0 -> défaut
    monkeypatch.setenv("ONIX_RETENTION_DAYS", "abc")
    assert ret._retention_days() == 365   # illisible -> défaut
    monkeypatch.delenv("ONIX_RETENTION_DAYS", raising=False)
    assert ret._retention_days() == 365
