# -*- coding: utf-8 -*-
"""Tests unitaires de `app.usage_tracker` — validation + honnêteté de persistance.

Deux invariants peu couverts en direct :
  * `build_usage_event` REFUSE un `event_type` inconnu OU un `status` hors liste
    (fail-closed : pas d'événement d'usage/audit mal typé en base) ;
  * `emit_usage_event` pose `_persisted=False` quand l'écriture en base ÉCHOUE
    silencieusement (base verrouillée, disque plein). C'est crucial pour les
    événements de TRAÇABILITÉ d'accès (RGPD) : on ne doit JAMAIS répondre
    « journalisé » si rien n'a été persisté (« zéro mock présenté comme réel »).
"""
from __future__ import annotations

import pytest

from app import usage_tracker
from app.usage_tracker import build_usage_event


def test_build_usage_event_rejette_event_type_inconnu():
    with pytest.raises(ValueError):
        build_usage_event("evenement_bidon", status="ok")


def test_build_usage_event_rejette_status_invalide():
    # event_type valide mais status hors {ok,error,blocked,skipped} -> refus.
    with pytest.raises(ValueError):
        build_usage_event("audit_documentaire_started", status="bidon")


def test_emit_usage_event_persisted_false_si_persistance_echoue(monkeypatch):
    # Force l'échec d'accès base : _connect lève -> capté -> _persisted=False.
    def _boom():
        raise RuntimeError("base inaccessible")

    monkeypatch.setattr(usage_tracker, "_connect", _boom)
    monkeypatch.delenv("ONIX_USAGE_SINK", raising=False)  # pas de sink JSONL parasite

    ev = usage_tracker.emit_usage_event(
        {"event_id": "e1", "event_type": "audit_documentaire_started", "status": "ok"}
    )
    # Honnêteté : on signale clairement que rien n'a été journalisé.
    assert ev["_persisted"] is False
