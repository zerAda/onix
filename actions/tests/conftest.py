"""Fixtures pytest : isole la base SQLite et les répertoires par test, et fournit
un client FastAPI authentifié avec une clé API de test."""
from __future__ import annotations

import importlib
import os
import sys

import pytest

# Rendre le package `app` importable (actions/ est la racine).
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

API_KEY = "test-key-0123456789"


@pytest.fixture(autouse=True)
def _default_audit_key_optional(monkeypatch):
    """HARD-03 : par défaut TOUS les tests autorisent le repli SHA-256 (préflight
    audit non bloquant), sinon `_lifespan` refuserait de démarrer sans clé HMAC.
    Un test qui PROUVE le fail-closed prod repose ce flag à "false" après coup
    (via `_client_with(..., ONIX_ACTIONS_AUDIT_KEY_OPTIONAL="false")`)."""
    monkeypatch.setenv("ONIX_ACTIONS_AUDIT_KEY_OPTIONAL", "true")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Environnement isolé : DB/jobs/reference dans tmp, clé API et flags par défaut.

    WS2 — pour que la suite HISTORIQUE (admin via clé de service ; webhooks/SMTP
    vers 127.0.0.1) reste verte, on configure ici un profil de test PERMISSIF
    explicite. Les NOUVEAUX tests WS2 (test_security_rgpd.py) repartent au
    contraire des DÉFAUTS fail-closed (clé admin obligatoire, egress refusé) pour
    prouver le durcissement."""
    monkeypatch.setenv("ONIX_ACTIONS_API_KEY", API_KEY)
    monkeypatch.setenv("ONIX_ACTIONS_DB", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("ONIX_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("ONIX_REFERENCE_DIR", str(tmp_path / "reference"))
    # Compat : la clé admin distincte reste OPTIONNELLE pour ces tests (la clé de
    # service fait admin), conformément au comportement historique.
    monkeypatch.setenv("ONIX_ACTIONS_ADMIN_KEY_OPTIONAL", "true")
    monkeypatch.delenv("ONIX_ACTIONS_ADMIN_KEY", raising=False)
    # HARD-03 : ces tests historiques tournent sans clé HMAC d'audit (repli SHA-256
    # assumé) — on autorise explicitement le repli pour que le préflight ne refuse
    # pas le démarrage. Les tests de prod fail-closed le remettent à "false".
    monkeypatch.setenv("ONIX_ACTIONS_AUDIT_KEY_OPTIONAL", "true")
    # Compat egress : les tests d'intégration poussent vers 127.0.0.1 en http.
    monkeypatch.setenv("ONIX_EGRESS_ALLOWLIST", "127.0.0.1,localhost")
    monkeypatch.setenv("ONIX_EGRESS_ALLOW_HTTP", "true")
    monkeypatch.setenv("ONIX_EGRESS_ALLOW_PRIVATE_IP", "true")
    # Pas d'identité vérifiée exigée (clé de service seule) ni de quota gênant.
    monkeypatch.setenv("ONIX_ACTIONS_REQUIRE_CALLER_IDENTITY", "false")
    monkeypatch.delenv("ONIX_ACTIONS_CALLER_HMAC_SECRET", raising=False)
    monkeypatch.setenv("ONIX_ACTIONS_RATE_LIMIT", "10000/minute")
    # Repartir d'un état de flags neutre.
    for var in list(os.environ):
        if var.startswith("ONIX_") and var.endswith("_ENABLED"):
            monkeypatch.delenv(var, raising=False)
    return tmp_path


@pytest.fixture()
def client(env):
    """TestClient FastAPI avec DB initialisée (startup déclenché)."""
    from fastapi.testclient import TestClient

    # Réimport propre pour que les modules relisent les env vars. IMPORTANT :
    # on recharge admin_state EN PREMIER puis TOUS les modules qui lient ses
    # internes (_lock/_connect/hash_id) par valeur — sinon ils garderaient une
    # référence au _lock de l'ancien module (verrous divergents). Ordre =
    # admin_state -> dépendants -> security -> main.
    import app.admin_state as admin_state

    importlib.reload(admin_state)
    for modname in (
        "app.usage_tracker", "app.tasks", "app.cost_tracker",
        "app.audit_log", "app.retention",
    ):
        importlib.reload(importlib.import_module(modname))
    import app.security as security
    import app.main as main

    importlib.reload(security)
    importlib.reload(main)
    security.reset_rate_limits()

    with TestClient(main.app) as c:
        c.headers.update({"X-API-Key": API_KEY})
        yield c
