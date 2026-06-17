"""Tests d'INTÉGRATION du câblage `main.py` : cache RBAC-safe + filtre ACL par-doc.

Les agents ont livré et testé les MODULES (`cache.py`, `doc_acl.py`) en isolation.
Ici on prouve que `main.py` les BRANCHE correctement dans le chemin de requête —
la partie qu'aucun agent ne pouvait tester (contrainte « ne pas toucher main.py ») :

  * un HIT de cache évite réellement l'aller-retour amont (économie tokens/latence) ;
  * deux périmètres RBAC différents ne PARTAGENT JAMAIS une entrée de cache ;
  * une intention d'écriture n'est jamais mise en cache (ni servie depuis le cache) ;
  * le filtre ACL par-document retire les citations non autorisées (par utilisateur),
    et il est ré-appliqué APRÈS le cache (donc jamais mutualisé entre utilisateurs).
"""
from __future__ import annotations

import importlib
import json

import pytest

from conftest import GROUP_NORD, GROUP_SUD, _FakeResponse, claims


def _make_client(monkeypatch, *, cache=True, doc_acl_path=None, upstream=None):
    """Construit un TestClient avec cache et/ou ACL activés, amont Onyx moqué.

    L'amont compte ses appels (`client.calls["n"]`) pour PROUVER les hits de cache.
    `upstream` peut être un dict fixe OU une fonction n -> dict (réponse par appel).
    """
    if cache:
        monkeypatch.setenv("GATEWAY_CACHE_ENABLED", "true")
        monkeypatch.setenv("GATEWAY_CACHE_HMAC_SECRET", "0123456789abcdef0123456789abcdef")
        monkeypatch.setenv("GATEWAY_CACHE_REDIS_URL", "")  # → backend mémoire (LRU)
    else:
        monkeypatch.setenv("GATEWAY_CACHE_ENABLED", "false")
    if doc_acl_path:
        monkeypatch.setenv("GATEWAY_DOC_ACL_ENABLED", "true")
        monkeypatch.setenv("GATEWAY_DOC_ACL_PATH", doc_acl_path)
    else:
        monkeypatch.setenv("GATEWAY_DOC_ACL_ENABLED", "false")

    import app.config as config
    import app.main as main

    importlib.reload(config)
    importlib.reload(main)

    from fastapi.testclient import TestClient

    calls = {"n": 0, "payloads": []}
    default = upstream if upstream is not None else {
        "answer": "Résumé du dossier disponible (source: dossier).",
    }

    async def _fake_post(url, json=None, headers=None, **kwargs):  # noqa: A002
        calls["n"] += 1
        calls["payloads"].append(json)
        body = default(calls["n"]) if callable(default) else default
        return _FakeResponse(200, body)

    c = TestClient(main.app)
    c.__enter__()
    monkeypatch.setattr(main.app.state.http, "post", _fake_post)
    c.calls = calls
    return c


@pytest.fixture()
def mk(env, monkeypatch):
    created = []

    def _f(**kw):
        c = _make_client(monkeypatch, **kw)
        created.append(c)
        return c

    yield _f
    for c in created:
        c.__exit__(None, None, None)


def _ask(c, message, *, groups):
    return c.post(
        "/v1/chat/send-message",
        headers={"X-OIDC-Claims": claims(groups=groups)},
        json={"message": message},
    )


def test_cache_hit_avoids_upstream(mk):
    """2e question identique (même périmètre) → servie par le cache, 0 appel amont."""
    c = mk(cache=True)
    r1 = _ask(c, "Quelle est l'échéance du dossier ?", groups=[GROUP_NORD])
    r2 = _ask(c, "Quelle est l'échéance du dossier ?", groups=[GROUP_NORD])
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json()
    assert c.calls["n"] == 1  # le 2e appel n'a PAS touché Onyx/LLM


def test_cache_rbac_isolation_by_perimeter(mk):
    """Périmètres différents (Nord vs Sud) → clés différentes → aucun partage."""
    c = mk(cache=True, upstream=lambda n: {"answer": f"réponse #{n} (source: doc)"})
    r_nord = _ask(c, "Question commune ?", groups=[GROUP_NORD])
    r_sud = _ask(c, "Question commune ?", groups=[GROUP_SUD])
    assert c.calls["n"] == 2  # 2 périmètres → 2 appels amont (pas de hit croisé)
    assert r_nord.json() != r_sud.json()  # Sud ne reçoit JAMAIS le cache de Nord


def test_same_perimeter_shares_cache(mk):
    """Deux utilisateurs DIFFÉRENTS au MÊME périmètre mutualisent le cache."""
    c = mk(cache=True, upstream=lambda n: {"answer": f"réponse #{n} (source: doc)"})
    r_a = c.post("/v1/chat/send-message",
                 headers={"X-OIDC-Claims": claims(oid="a", upn="a@x.fr", groups=[GROUP_NORD])},
                 json={"message": "Même question ?"})
    r_b = c.post("/v1/chat/send-message",
                 headers={"X-OIDC-Claims": claims(oid="b", upn="b@x.fr", groups=[GROUP_NORD])},
                 json={"message": "Même question ?"})
    assert c.calls["n"] == 1  # même périmètre → 1 seul appel amont
    assert r_a.json() == r_b.json()


def test_write_intent_is_never_cached(mk):
    """Une intention d'écriture n'est ni servie ni stockée (bypass)."""
    c = mk(cache=True, upstream=lambda n: {"answer": f"ok #{n}"})
    _ask(c, "Modifie le contrat du client ALPHA", groups=[GROUP_NORD])
    _ask(c, "Modifie le contrat du client ALPHA", groups=[GROUP_NORD])
    assert c.calls["n"] == 2  # write_intent → jamais de hit


def test_cache_control_no_store_bypasses(mk):
    """`Cache-Control: no-store` force le contournement du cache."""
    c = mk(cache=True, upstream=lambda n: {"answer": f"v#{n} (source: d)"})
    for _ in range(2):
        c.post("/v1/chat/send-message",
               headers={"X-OIDC-Claims": claims(groups=[GROUP_NORD]), "Cache-Control": "no-store"},
               json={"message": "Question non cachable ?"})
    assert c.calls["n"] == 2


def test_doc_acl_drops_unauthorized_doc(mk, tmp_path):
    """Le filtre ACL retire, pour un utilisateur Nord, la citation du doc Sud."""
    acl = tmp_path / "doc_acl.json"
    acl.write_text(
        json.dumps({
            "DOC_NORD": {"groups": [GROUP_NORD]},
            "DOC_SUD": {"groups": [GROUP_SUD]},
        }),
        encoding="utf-8",
    )
    upstream = {
        "answer": "Voici les éléments du dossier (source: documents).",
        "documents": [
            {"document_id": "DOC_NORD", "semantic_identifier": "nord.pdf"},
            {"document_id": "DOC_SUD", "semantic_identifier": "sud.pdf"},
        ],
    }
    c = mk(cache=False, doc_acl_path=str(acl), upstream=upstream)
    r = _ask(c, "Donne-moi le dossier", groups=[GROUP_NORD])
    ids = [d.get("document_id") for d in r.json().get("documents", [])]
    assert ids == ["DOC_NORD"]  # DOC_SUD retiré pour un utilisateur Nord


def test_doc_acl_isolation_between_users(mk, tmp_path):
    """Même réponse amont, deux utilisateurs → documents filtrés DIFFÉREMMENT."""
    acl = tmp_path / "doc_acl.json"
    acl.write_text(
        json.dumps({
            "DOC_NORD": {"groups": [GROUP_NORD]},
            "DOC_SUD": {"groups": [GROUP_SUD]},
        }),
        encoding="utf-8",
    )
    upstream = {
        "answer": "Éléments (source: documents).",
        "documents": [
            {"document_id": "DOC_NORD"},
            {"document_id": "DOC_SUD"},
        ],
    }
    c = mk(cache=False, doc_acl_path=str(acl), upstream=upstream)
    r_nord = _ask(c, "Dossier ?", groups=[GROUP_NORD])
    r_sud = _ask(c, "Dossier ?", groups=[GROUP_SUD])
    assert [d["document_id"] for d in r_nord.json()["documents"]] == ["DOC_NORD"]
    assert [d["document_id"] for d in r_sud.json()["documents"]] == ["DOC_SUD"]
