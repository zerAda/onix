"""Tests de la journalisation des décisions d'accès (identité HACHÉE).

Vérifie : pseudonymisation HMAC stable, sel honoré, ABSENCE de l'oid/UPN en clair
dans la sortie, et émission allow/deny avec les bons niveaux de log.
"""
from __future__ import annotations

import json
import logging

import app.audit as audit


def _reset(monkeypatch, salt="unit-test-salt"):
    monkeypatch.setenv("GATEWAY_AUDIT_SALT", salt)
    audit.reset_salt_cache()


def test_pseudonymize_is_stable_and_hex(monkeypatch):
    _reset(monkeypatch)
    a = audit.pseudonymize("user-oid-123")
    b = audit.pseudonymize("user-oid-123")
    assert a == b  # déterministe pour un sel donné
    assert len(a) == 16
    int(a, 16)  # hexadécimal valide
    assert a != "user-oid-123"  # jamais la valeur en clair


def test_pseudonymize_differs_by_actor(monkeypatch):
    _reset(monkeypatch)
    assert audit.pseudonymize("alice@contoso.fr") != audit.pseudonymize("bob@contoso.fr")


def test_salt_changes_hash(monkeypatch):
    _reset(monkeypatch, salt="salt-A")
    ha = audit.pseudonymize("same-user")
    _reset(monkeypatch, salt="salt-B")
    hb = audit.pseudonymize("same-user")
    assert ha != hb  # le sel sépare les espaces de hachage


def test_none_actor_is_anonymous(monkeypatch):
    _reset(monkeypatch)
    assert audit.pseudonymize(None) == "anonymous"
    assert audit.pseudonymize("") == "anonymous"


def test_decision_record_never_leaks_plaintext_identity(monkeypatch):
    _reset(monkeypatch)
    rec = audit.log_access_decision(
        actor="secret-upn@contoso.fr",
        decision="allow",
        reason="proxied",
        group_source="claims",
        group_count=2,
        authorized_sets=["clients-nord"],
        effective_sets=["clients-nord"],
        endpoint="chat/send-message",
    )
    blob = json.dumps(rec)
    assert "secret-upn@contoso.fr" not in blob
    assert rec["actor_hash"] == audit.pseudonymize("secret-upn@contoso.fr")
    assert rec["decision"] == "allow"
    assert rec["effective_sets"] == ["clients-nord"]


def test_deny_logs_at_warning(monkeypatch, caplog):
    _reset(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="onix.gateway.audit"):
        audit.log_access_decision(actor="u", decision="deny", reason="empty_or_out_of_scope")
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    # Le message journalisé est du JSON contenant la décision.
    assert any('"decision": "deny"' in r.getMessage() for r in caplog.records)
