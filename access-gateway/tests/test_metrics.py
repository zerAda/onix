"""Tests de l'endpoint /metrics et de l'instrumentation Prometheus de la passerelle.

Couvre :
  * GET /metrics expose les familles de métriques attendues.
  * Un proxy 2xx dont le post-filtre BLOQUE incrémente guardrail_total{blocked=true}.
  * Un chemin AccessDenied (403) incrémente requests_total{decision=deny}.
  * GET /metrics renvoie 404 quand GATEWAY_METRICS_ENABLED=false.
  * Les compteurs de citation bougent correctement.
  * L'endpoint /v1/feedback incrémente feedback_total{rating}.

Pattern : on suit EXACTEMENT le même style que test_api.py et conftest.py.
L'amont Onyx est moqué via monkeypatch sur http.post (cf. client_factory).
"""
from __future__ import annotations

import importlib

import pytest

from conftest import GROUP_NORD, GROUP_SUD, claims, _FakeResponse

_HDR_NORD = {"X-OIDC-Claims": claims(oid="nord", groups=[GROUP_NORD])}


# ---------------------------------------------------------------------------
# Helpers : extraire les métriques Prometheus depuis le corps texte.
# ---------------------------------------------------------------------------

def _metric_lines(body: str, family: str) -> list[str]:
    """Renvoie les lignes portant le nom de la famille (hors # HELP / # TYPE)."""
    return [
        ln
        for ln in body.splitlines()
        if ln.startswith(family) and not ln.startswith("#")
    ]


def _metric_value(body: str, family: str, labels: dict[str, str]) -> float | None:
    """Renvoie la valeur d'une métrique identifiée par sa famille et ses labels."""
    for line in _metric_lines(body, family):
        if all(f'{k}="{v}"' in line for k, v in labels.items()):
            return float(line.split()[-1])
    return None


# ---------------------------------------------------------------------------
# Fixture dédiée aux tests métriques (registre isolé par test).
# ---------------------------------------------------------------------------

@pytest.fixture()
def metrics_client(env, monkeypatch):
    """TestClient FastAPI avec métriques activées et amont Onyx moqué.

    Note : on NE recharge PAS app.metrics — les objets Counter/Histogram sont
    enregistrés une fois dans le CollectorRegistry global de prometheus_client ;
    un second chargement du module lèverait une ValueError (doublon de nom).
    On recharge uniquement config et main pour l'isolation de configuration.
    Les valeurs accumulées entre tests sont tolérées (les assertions vérifient
    ≥ 1, pas == 1 exactement).
    """
    from fastapi.testclient import TestClient

    import app.config as config
    import app.main as main

    monkeypatch.setenv("GATEWAY_METRICS_ENABLED", "true")

    importlib.reload(config)
    importlib.reload(main)

    async def _fake_post(url, json=None, headers=None, **kwargs):  # noqa: A002
        return _FakeResponse(
            200,
            {"message": "réponse sourcée (source : DOC.pdf)", "top_documents": []},
        )

    with TestClient(main.app) as c:
        monkeypatch.setattr(main.app.state.http, "post", _fake_post)
        yield c


@pytest.fixture()
def metrics_client_factory(env, monkeypatch):
    """Fabrique un TestClient pilotable pour les tests métriques (amont moqué).

    Même remarque que metrics_client : app.metrics n'est PAS rechargé.
    """
    from fastapi.testclient import TestClient

    import app.config as config
    import app.main as main

    monkeypatch.setenv("GATEWAY_METRICS_ENABLED", "true")

    importlib.reload(config)
    importlib.reload(main)

    state: dict = {"upstream": {"message": "ok"}, "status": 200}

    async def _fake_post(url, json=None, headers=None, **kwargs):  # noqa: A002
        return _FakeResponse(state["status"], state["upstream"])

    created: list = []

    def _factory(upstream_response, status=200):
        state["upstream"] = upstream_response
        state["status"] = status
        c = TestClient(main.app)
        c.__enter__()
        monkeypatch.setattr(main.app.state.http, "post", _fake_post)
        created.append(c)
        return c

    yield _factory
    for c in created:
        c.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 1. GET /metrics expose les familles de métriques attendues.
# ---------------------------------------------------------------------------

def test_metrics_endpoint_exposes_all_families(metrics_client):
    r = metrics_client.get("/metrics")
    assert r.status_code == 200
    body = r.text

    familles_attendues = [
        "onix_gateway_requests_total",
        "onix_gateway_guardrail_total",
        "onix_gateway_answer_no_context_total",
        "onix_gateway_answer_with_citation_total",
        "onix_gateway_answer_without_citation_total",
        "onix_gateway_request_latency_seconds",
        "onix_gateway_upstream_errors_total",
        "onix_gateway_feedback_total",
    ]
    for famille in familles_attendues:
        assert famille in body, f"Famille manquante dans /metrics : {famille!r}"


def test_metrics_content_type_is_prometheus(metrics_client):
    r = metrics_client.get("/metrics")
    assert r.status_code == 200
    # Le content-type doit commencer par text/plain (format texte Prometheus).
    assert r.headers.get("content-type", "").startswith("text/plain")


# ---------------------------------------------------------------------------
# 2. Proxy 2xx dont le post-filtre BLOQUE → guardrail_total{blocked=true}.
# ---------------------------------------------------------------------------

def test_guardrail_blocked_increments_counter(metrics_client_factory):
    """Quand l'amont renvoie une confirmation d'écriture, le post-filtre bloque :
    guardrail_total{blocked='true'} doit être > 0 après la requête."""
    client = metrics_client_factory(
        {"message": "C'est fait, j'ai supprimé le fichier ALPHA."}
    )
    r = client.post(
        "/v1/chat/send-message",
        json={"message": "Supprime le fichier ALPHA"},
        headers=_HDR_NORD,
    )
    assert r.status_code == 200  # 200 (la gateway substitue, ne plante pas)

    metrics_r = client.get("/metrics")
    assert metrics_r.status_code == 200
    body = metrics_r.text

    # guardrail_total avec blocked=true doit avoir été incrémenté.
    val = _metric_value(body, "onix_gateway_guardrail_total", {"blocked": "true"})
    assert val is not None and val >= 1.0, (
        f"guardrail_total{{blocked='true'}} attendu ≥ 1, obtenu {val!r}\n{body}"
    )


def test_guardrail_passthrough_increments_not_blocked(metrics_client_factory):
    """Réponse conforme (avec citation) → guardrail_total{blocked=false} incrémenté."""
    safe = {
        "message": "La cotisation est mentionnée dans ALPHA_contrat.pdf.",
        "top_documents": [{"semantic_identifier": "ALPHA_contrat.pdf"}],
    }
    client = metrics_client_factory(safe)
    r = client.post(
        "/v1/chat/send-message",
        json={"message": "Quelle est la cotisation ?"},
        headers=_HDR_NORD,
    )
    assert r.status_code == 200

    metrics_r = client.get("/metrics")
    body = metrics_r.text
    val = _metric_value(body, "onix_gateway_guardrail_total", {"blocked": "false"})
    assert val is not None and val >= 1.0, (
        f"guardrail_total{{blocked='false'}} attendu ≥ 1, obtenu {val!r}\n{body}"
    )


# ---------------------------------------------------------------------------
# 3. Chemin AccessDenied (403) → requests_total{decision=deny}.
# ---------------------------------------------------------------------------

def test_access_denied_increments_deny_counter(metrics_client_factory):
    """Un utilisateur sans groupe mappé (403) incrémente requests_total{decision=deny}."""
    client = metrics_client_factory({"message": "ok"})
    r = client.post(
        "/v1/chat/send-message",
        json={"message": "x"},
        headers={"X-OIDC-Claims": claims(oid="ghost", groups=[])},
    )
    assert r.status_code == 403

    metrics_r = client.get("/metrics")
    body = metrics_r.text
    val = _metric_value(
        body,
        "onix_gateway_requests_total",
        {"endpoint": "chat/send-message", "decision": "deny"},
    )
    assert val is not None and val >= 1.0, (
        f"requests_total{{decision='deny'}} attendu ≥ 1, obtenu {val!r}\n{body}"
    )


def test_allow_increments_allow_counter(metrics_client_factory):
    """Un proxy réussi (200) incrémente requests_total{decision=allow}."""
    safe = {
        "message": "Voici la réponse (source : DOC.pdf).",
        "top_documents": [{"semantic_identifier": "DOC.pdf"}],
    }
    client = metrics_client_factory(safe)
    r = client.post(
        "/v1/chat/send-message",
        json={"message": "x"},
        headers=_HDR_NORD,
    )
    assert r.status_code == 200

    metrics_r = client.get("/metrics")
    body = metrics_r.text
    val = _metric_value(
        body,
        "onix_gateway_requests_total",
        {"endpoint": "chat/send-message", "decision": "allow"},
    )
    assert val is not None and val >= 1.0, (
        f"requests_total{{decision='allow'}} attendu ≥ 1, obtenu {val!r}\n{body}"
    )


# ---------------------------------------------------------------------------
# 4. GET /metrics renvoie 404 quand GATEWAY_METRICS_ENABLED=false.
# ---------------------------------------------------------------------------

def test_metrics_disabled_returns_404(env, monkeypatch):
    """Quand les métriques sont désactivées, /metrics renvoie 404."""
    from fastapi.testclient import TestClient

    import app.config as config
    import app.main as main

    monkeypatch.setenv("GATEWAY_METRICS_ENABLED", "false")

    importlib.reload(config)
    importlib.reload(main)

    with TestClient(main.app) as c:
        r = c.get("/metrics")
    assert r.status_code == 404


def test_feedback_disabled_when_metrics_off(env, monkeypatch):
    """Quand les métriques sont désactivées, /v1/feedback renvoie 404."""
    from fastapi.testclient import TestClient

    import app.config as config
    import app.main as main

    monkeypatch.setenv("GATEWAY_METRICS_ENABLED", "false")

    importlib.reload(config)
    importlib.reload(main)

    with TestClient(main.app) as c:
        r = c.post(
            "/v1/feedback",
            json={"rating": "up"},
            headers=_HDR_NORD,
        )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 5. Compteurs de citation.
# ---------------------------------------------------------------------------

def test_citation_counter_with_citation(metrics_client_factory):
    """Réponse finale avec citation → answer_with_citation_total incrémenté."""
    safe = {
        "message": "La prime est décrite dans PRIME.pdf (source : PRIME.pdf).",
        "top_documents": [{"semantic_identifier": "PRIME.pdf"}],
    }
    client = metrics_client_factory(safe)
    client.post(
        "/v1/chat/send-message",
        json={"message": "prime ?"},
        headers=_HDR_NORD,
    )
    body = client.get("/metrics").text
    val = _metric_value(body, "onix_gateway_answer_with_citation_total", {})
    assert val is not None and val >= 1.0, (
        f"answer_with_citation_total attendu ≥ 1, obtenu {val!r}"
    )


def test_citation_counter_blocked_response_has_no_citation(metrics_client_factory):
    """Quand le post-filtre bloque (réponse de refus sans citation), le compteur
    answer_without_citation_total doit être incrémenté."""
    # Réponse dangereuse : le post-filtre va substituer REFUSAL_READ_ONLY
    # (aucune citation dans ce refus → sans-citation).
    client = metrics_client_factory(
        {"message": "J'ai modifié le fichier B."}
    )
    client.post(
        "/v1/chat/send-message",
        json={"message": "Modifie le fichier B"},
        headers=_HDR_NORD,
    )
    body = client.get("/metrics").text
    val = _metric_value(body, "onix_gateway_answer_without_citation_total", {})
    assert val is not None and val >= 1.0, (
        f"answer_without_citation_total attendu ≥ 1, obtenu {val!r}"
    )


# ---------------------------------------------------------------------------
# 6. Endpoint de feedback.
# ---------------------------------------------------------------------------

def test_feedback_up_increments_counter(metrics_client):
    r = metrics_client.post(
        "/v1/feedback",
        json={"rating": "up"},
        headers=_HDR_NORD,
    )
    assert r.status_code == 200
    assert r.json()["rating"] == "up"

    body = metrics_client.get("/metrics").text
    val = _metric_value(body, "onix_gateway_feedback_total", {"rating": "up"})
    assert val is not None and val >= 1.0, (
        f"feedback_total{{rating='up'}} attendu ≥ 1, obtenu {val!r}"
    )


def test_feedback_down_increments_counter(metrics_client):
    r = metrics_client.post(
        "/v1/feedback",
        json={"rating": "down"},
        headers=_HDR_NORD,
    )
    assert r.status_code == 200
    body = metrics_client.get("/metrics").text
    val = _metric_value(body, "onix_gateway_feedback_total", {"rating": "down"})
    assert val is not None and val >= 1.0, (
        f"feedback_total{{rating='down'}} attendu ≥ 1, obtenu {val!r}"
    )


def test_feedback_invalid_rating_returns_422(metrics_client):
    r = metrics_client.post(
        "/v1/feedback",
        json={"rating": "maybe"},
        headers=_HDR_NORD,
    )
    assert r.status_code == 422


def test_feedback_without_identity_returns_401(metrics_client):
    r = metrics_client.post("/v1/feedback", json={"rating": "up"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 7. authorized-document-sets incrémente requests_total.
# ---------------------------------------------------------------------------

def test_introspection_increments_requests_total(metrics_client):
    r = metrics_client.get(
        "/v1/authorized-document-sets",
        headers=_HDR_NORD,
    )
    assert r.status_code == 200

    body = metrics_client.get("/metrics").text
    val = _metric_value(
        body,
        "onix_gateway_requests_total",
        {"endpoint": "authorized-document-sets", "decision": "allow"},
    )
    assert val is not None and val >= 1.0, (
        f"requests_total{{endpoint='authorized-document-sets',decision='allow'}} "
        f"attendu ≥ 1, obtenu {val!r}"
    )
