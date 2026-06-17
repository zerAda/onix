"""Tests d'INTÉGRATION du câblage du tier SÉMANTIQUE dans `main.py`.

L'agent a testé le moteur sémantique (`cache.semantic_lookup` + garde
anti-divergence) en isolation ; ici on prouve que `main.py` le BRANCHE
correctement : un miss EXACT sur une REFORMULATION déclenche un hit sémantique
(0 appel amont), tandis qu'une divergence factuelle (un nombre/une année en plus)
est REFUSÉE → miss → appel amont. L'embedding est mocké (aucun Ollama réel).
"""
from __future__ import annotations

import importlib

import pytest

from conftest import GROUP_NORD, claims


def _make_semantic_client(monkeypatch, upstream):
    monkeypatch.setenv("GATEWAY_CACHE_ENABLED", "true")
    monkeypatch.setenv("GATEWAY_CACHE_HMAC_SECRET", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("GATEWAY_CACHE_REDIS_URL", "")
    monkeypatch.setenv("GATEWAY_SEMANTIC_CACHE_ENABLED", "true")
    monkeypatch.setenv("GATEWAY_DOC_ACL_ENABLED", "false")

    import app.config as config
    import app.main as main

    importlib.reload(config)
    importlib.reload(main)

    from fastapi.testclient import TestClient
    from conftest import _FakeResponse

    calls = {"n": 0}

    async def _fake_post(url, json=None, headers=None, **kwargs):  # noqa: A002
        calls["n"] += 1
        body = upstream(calls["n"]) if callable(upstream) else upstream
        return _FakeResponse(200, body)

    c = TestClient(main.app)
    c.__enter__()
    monkeypatch.setattr(main.app.state.http, "post", _fake_post)
    # Embedding DÉTERMINISTE mocké : vecteur constant → toute paire de questions a
    # une similarité cosinus = 1.0. C'est donc le GARDE ANTI-DIVERGENCE (sur le
    # TEXTE) qui décide seul d'accepter/refuser — exactement ce qu'on veut prouver.
    monkeypatch.setattr(main.app.state, "embed_fn", lambda _text: [1.0, 0.0, 0.0])
    c.calls = calls
    return c


@pytest.fixture()
def sem_client(env, monkeypatch):
    created = []

    def _f(upstream):
        c = _make_semantic_client(monkeypatch, upstream)
        created.append(c)
        return c

    yield _f
    for c in created:
        c.__exit__(None, None, None)


def _ask(c, message):
    return c.post(
        "/v1/chat/send-message",
        headers={"X-OIDC-Claims": claims(groups=[GROUP_NORD])},
        json={"message": message},
    )


def test_semantic_hit_on_reformulation(sem_client):
    """Une reformulation (même périmètre, mêmes faits) → HIT sémantique, 0 amont."""
    c = sem_client(lambda n: {"answer": f"réponse #{n} (source: doc)"})
    r1 = _ask(c, "Quelle est l'échéance du dossier ?")            # miss → amont #1 + indexé
    r2 = _ask(c, "Quelle échéance pour ce dossier, déjà ?")       # reformulation → hit sémantique
    assert c.calls["n"] == 1                                       # 2e requête NON relayée
    assert r2.json().get("answer", "").endswith("#1 (source: doc)")  # on sert bien la réponse #1


def test_semantic_rejects_numeric_divergence(sem_client):
    """Même similarité d'embedding MAIS un nombre/année en plus → REFUS (miss)."""
    c = sem_client(lambda n: {"answer": f"réponse #{n} (source: doc)"})
    _ask(c, "Quelle est l'échéance du dossier ?")                 # amont #1 + indexé
    r2 = _ask(c, "Quelle est l'échéance 2025 du dossier ?")       # divergence (2025) → refus
    assert c.calls["n"] == 2                                       # divergence → amont #2 (pas de hit)
    assert r2.json().get("answer", "").endswith("#2 (source: doc)")


def test_semantic_rejects_entity_divergence(sem_client):
    """Un nom d'entité différent (ALPHA vs BETA) → REFUS même si embedding proche."""
    c = sem_client(lambda n: {"answer": f"réponse #{n} (source: doc)"})
    _ask(c, "Donne l'échéance du client ALPHA")                  # amont #1 + indexé
    r2 = _ask(c, "Donne l'échéance du client BETA")              # entité divergente → refus
    assert c.calls["n"] == 2
    assert r2.json().get("answer", "").endswith("#2 (source: doc)")


def test_semantic_disabled_no_crossing(env, monkeypatch):
    """Tier sémantique OFF → une reformulation est un simple miss (amont rappelé)."""
    monkeypatch.setenv("GATEWAY_CACHE_ENABLED", "true")
    monkeypatch.setenv("GATEWAY_CACHE_HMAC_SECRET", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("GATEWAY_SEMANTIC_CACHE_ENABLED", "false")
    import app.config as config
    import app.main as main

    importlib.reload(config)
    importlib.reload(main)
    from fastapi.testclient import TestClient
    from conftest import _FakeResponse

    calls = {"n": 0}

    async def _fake_post(url, json=None, headers=None, **kwargs):  # noqa: A002
        calls["n"] += 1
        return _FakeResponse(200, {"answer": f"r#{calls['n']} (source: d)"})

    with TestClient(main.app) as c:
        monkeypatch.setattr(main.app.state.http, "post", _fake_post)
        _ask(c, "Quelle est l'échéance du dossier ?")
        _ask(c, "Quelle échéance pour ce dossier ?")
    assert calls["n"] == 2  # pas de tier sémantique → 2 appels amont
