# -*- coding: utf-8 -*-
"""Tests du contrôle FAIL-CLOSED de la config Alertmanager (M4, scope monitoring).

Verrouille le cœur de `scripts/check-alertmanager-config.py` (jusqu'ici sans test) :
le rendu **refuse** toute URL absente/vide/non-http (fail-closed, comme l'entrypoint
conteneur), et un receiver **VIDE** (référencé par une route mais sans
`webhook_configs`) est détecté — c'était l'ancienne vuln des « alertes dans le vide ».
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Le script a un nom avec tirets -> chargement par chemin (pas d'import direct).
_MOD_PATH = Path(__file__).resolve().parent.parent / "check-alertmanager-config.py"
_spec = importlib.util.spec_from_file_location("check_alertmanager_config", _MOD_PATH)
cac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cac)


def test_render_substitue_url_valide():
    rendered = cac._render("receiver: r\nurl: ${ALERT_WEBHOOK_URL}", "https://hooks.test/x")
    assert "https://hooks.test/x" in rendered
    assert "${ALERT_WEBHOOK_URL}" not in rendered


@pytest.mark.parametrize("bad", [None, "", "   ", "not-a-url", "ftp://x"])
def test_render_failclosed_url_invalide(bad):
    # Sans URL http(s) valide -> refus bruyant (CheckError), jamais de rendu silencieux.
    with pytest.raises(cac.CheckError):
        cac._render("url: ${ALERT_WEBHOOK_URL}", bad)


def test_assert_routes_receiver_vide_echoue():
    # Receiver référencé par une route mais VIDE -> sortie fail-closed (SystemExit).
    rendered = "route:\n  receiver: vide\nreceivers:\n  - name: vide\n"
    with pytest.raises(SystemExit):
        cac._assert_routes_have_webhook(rendered)


def test_assert_routes_receiver_avec_webhook_ok():
    # Receiver avec un webhook_configs + url réelle -> aucune erreur.
    rendered = (
        "route:\n  receiver: reel\n"
        "receivers:\n  - name: reel\n    webhook_configs:\n      - url: https://h/x\n"
    )
    cac._assert_routes_have_webhook(rendered)  # ne doit pas lever
