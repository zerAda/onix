"""Fixtures pytest de la passerelle : env isolé, mapping temporaire, et un
TestClient FastAPI dont les dépendances réseau (Graph + Onyx amont) sont moquées
— AUCUN appel réseau réel n'est effectué.
"""
from __future__ import annotations

import importlib
import json
import os
import sys

import pytest

# Rendre le package `app` importable (access-gateway/ est la racine).
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def run(coro):
    """Exécute une coroutine dans les tests sans dépendre d'un plugin async
    (asyncio.run sur une boucle neuve à chaque appel)."""
    import asyncio

    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Claims OIDC de test (ce que le reverse-proxy/IdP injecterait, déjà vérifiés). #
# --------------------------------------------------------------------------- #
GROUP_NORD = "11111111-1111-1111-1111-111111111111"
GROUP_SUD = "22222222-2222-2222-2222-222222222222"


def claims(*, oid="user-nord-oid", upn="nord@contoso.fr", groups=None, overage=False) -> str:
    body = {"oid": oid, "upn": upn, "sub": oid}
    if overage:
        body["_claim_names"] = {"groups": "src1"}
        body["_claim_sources"] = {"src1": {"endpoint": "https://graph.microsoft.com/..."}}
    elif groups is not None:
        body["groups"] = groups
    return json.dumps(body)


@pytest.fixture()
def mapping_file(tmp_path):
    path = tmp_path / "group_map.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "default_document_sets": [],
                "groups": {
                    GROUP_NORD: {"document_sets": ["clients-nord"]},
                    GROUP_SUD: {"document_sets": ["clients-sud"]},
                },
            }
        ),
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture()
def env(tmp_path, mapping_file, monkeypatch):
    """Environnement isolé : mode 'claims' par défaut (pas de Graph requis)."""
    monkeypatch.setenv("GATEWAY_ONYX_BASE_URL", "http://onyx.test:8080")
    monkeypatch.setenv("GATEWAY_ONYX_API_KEY", "onyx-test-key")
    monkeypatch.setenv("GATEWAY_GROUP_SOURCE", "claims")
    monkeypatch.setenv("GATEWAY_MAPPING_PATH", mapping_file)
    monkeypatch.setenv("GATEWAY_DENY_IF_NO_MATCH", "true")
    monkeypatch.setenv("GATEWAY_GROUP_CACHE_TTL", "0")
    # Anti-spoof M7 : en TEST on tolère l'en-tête X-OIDC-Claims sans preuve proxy
    # (override dev). Un test dédié (test_failclosed) exerce le refus en PROD.
    monkeypatch.setenv("GATEWAY_ALLOW_UNAUTHENTICATED_HEADER", "true")
    return tmp_path


class _FakeResponse:
    """Réponse httpx minimale simulée (pour le relais Onyx amont)."""

    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"answer": "ok"}
        self.headers = headers or {"content-type": "application/json"}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


@pytest.fixture()
def client(env, monkeypatch):
    """TestClient FastAPI avec amont Onyx moqué.

    On capture le dernier payload relayé à Onyx dans `client.last_upstream` pour
    vérifier que le filtre document_set a bien été FORCÉ.
    """
    from fastapi.testclient import TestClient

    import app.config as config
    import app.main as main

    importlib.reload(config)
    importlib.reload(main)

    captured: dict = {}

    async def _fake_post(url, json=None, headers=None, **kwargs):  # noqa: A002
        captured["url"] = url
        captured["payload"] = json
        captured["headers"] = headers
        return _FakeResponse(200, {"answer": "relayed", "echo_filters": json.get("retrieval_options")})

    with TestClient(main.app) as c:
        # Remplace la méthode POST du client httpx partagé par notre faux relais.
        monkeypatch.setattr(main.app.state.http, "post", _fake_post)
        c.last_upstream = captured
        yield c


@pytest.fixture()
def client_factory(env, monkeypatch):
    """Fabrique un TestClient dont la RÉPONSE de l'amont Onyx est PILOTABLE.

    Utile pour prouver le **post-filtre déployé** : on fait renvoyer à l'amont une
    réponse d'assistant dangereuse (write simulé, fuite de prompt, fait non
    sourcé…) et on vérifie que la **gateway** la substitue par un refus AVANT de
    la renvoyer au client. La réponse amont est paramétrable par test.
    """
    from fastapi.testclient import TestClient

    import app.config as config
    import app.main as main

    importlib.reload(config)
    importlib.reload(main)

    state: dict = {"upstream": {"message": "ok"}, "status": 200, "captured": {}}

    async def _fake_post(url, json=None, headers=None, **kwargs):  # noqa: A002
        state["captured"]["url"] = url
        state["captured"]["payload"] = json
        return _FakeResponse(state["status"], state["upstream"])

    def _build(upstream_response, status=200):
        state["upstream"] = upstream_response
        state["status"] = status
        c = TestClient(main.app)
        c.__enter__()
        monkeypatch.setattr(main.app.state.http, "post", _fake_post)
        c.captured = state["captured"]
        return c

    created: list = []

    def _factory(upstream_response, status=200):
        c = _build(upstream_response, status)
        created.append(c)
        return c

    yield _factory
    for c in created:
        c.__exit__(None, None, None)
