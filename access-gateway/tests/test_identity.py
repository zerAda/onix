"""Tests de la résolution d'identité/groupes (claims | graph | auto | overage).

Graph est moqué via injection d'un client httpx MockTransport (aucun réseau).
"""
from __future__ import annotations

import httpx
import pytest

import app.config as config
from app.identity import IdentityError, Principal, parse_oidc_claims, resolve_principal
from app.graph_client import GraphError
from conftest import GROUP_NORD, claims, run


def _mk_settings(monkeypatch, **over):
    base = {
        "GATEWAY_GROUP_SOURCE": "claims",
        "GATEWAY_ONYX_BASE_URL": "http://onyx.test:8080",
    }
    base.update(over)
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    config.reset_settings_cache()
    return config.get_settings()


def _graph_client(groups):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(200, json={"value": [{"id": g} for g in groups]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# parsing                                                                      #
# --------------------------------------------------------------------------- #
def test_parse_claims_invalid_json_is_empty():
    assert parse_oidc_claims("{not json}") == {}
    assert parse_oidc_claims(None) == {}


def test_missing_identity_raises():
    s = _mk_settings(monkeypatch=pytest.MonkeyPatch())
    with pytest.raises(IdentityError):
        run(resolve_principal(s, oidc_claims_header=None))


# --------------------------------------------------------------------------- #
# mode claims                                                                  #
# --------------------------------------------------------------------------- #
def test_claims_mode_reads_groups(monkeypatch):
    s = _mk_settings(monkeypatch, GATEWAY_GROUP_SOURCE="claims")
    p = run(resolve_principal(s, oidc_claims_header=claims(groups=[GROUP_NORD])))
    assert isinstance(p, Principal)
    assert p.group_ids == [GROUP_NORD]
    assert p.source == "claims"
    assert p.upn == "nord@contoso.fr"


def test_claims_mode_empty_group_list(monkeypatch):
    s = _mk_settings(monkeypatch, GATEWAY_GROUP_SOURCE="claims")
    p = run(resolve_principal(s, oidc_claims_header=claims(groups=[])))
    assert p.group_ids == []


def test_claims_mode_overage_raises(monkeypatch):
    s = _mk_settings(monkeypatch, GATEWAY_GROUP_SOURCE="claims")
    with pytest.raises(IdentityError):
        run(resolve_principal(s, oidc_claims_header=claims(overage=True)))


# --------------------------------------------------------------------------- #
# mode graph                                                                   #
# --------------------------------------------------------------------------- #
def test_graph_mode(monkeypatch):
    s = _mk_settings(
        monkeypatch,
        GATEWAY_GROUP_SOURCE="graph",
        GATEWAY_GRAPH_TENANT_ID="tid",
        GATEWAY_GRAPH_CLIENT_ID="cid",
        GATEWAY_GRAPH_CLIENT_SECRET="sek",
    )

    async def go():
        async with _graph_client([GROUP_NORD, "g-extra"]) as gc:
            return await resolve_principal(
                s, oidc_claims_header=claims(groups=None), http_client=gc
            )

    p = run(go())
    assert p.source == "graph"
    assert p.group_ids == [GROUP_NORD, "g-extra"]


# --------------------------------------------------------------------------- #
# mode auto : claims si dispo, sinon Graph ; overage -> Graph                  #
# --------------------------------------------------------------------------- #
def test_auto_prefers_claims_when_present(monkeypatch):
    s = _mk_settings(
        monkeypatch,
        GATEWAY_GROUP_SOURCE="auto",
        GATEWAY_GRAPH_TENANT_ID="tid",
        GATEWAY_GRAPH_CLIENT_ID="cid",
        GATEWAY_GRAPH_CLIENT_SECRET="sek",
    )
    p = run(resolve_principal(s, oidc_claims_header=claims(groups=[GROUP_NORD])))
    assert p.source == "claims"
    assert p.group_ids == [GROUP_NORD]


def test_auto_falls_back_to_graph_on_overage(monkeypatch):
    s = _mk_settings(
        monkeypatch,
        GATEWAY_GROUP_SOURCE="auto",
        GATEWAY_GRAPH_TENANT_ID="tid",
        GATEWAY_GRAPH_CLIENT_ID="cid",
        GATEWAY_GRAPH_CLIENT_SECRET="sek",
    )

    async def go():
        async with _graph_client([GROUP_NORD]) as gc:
            return await resolve_principal(
                s, oidc_claims_header=claims(overage=True), http_client=gc
            )

    p = run(go())
    assert p.source == "graph"
    assert p.group_ids == [GROUP_NORD]


def test_auto_without_graph_configured_and_overage_raises(monkeypatch):
    s = _mk_settings(monkeypatch, GATEWAY_GROUP_SOURCE="auto")  # pas de Graph
    with pytest.raises(GraphError):
        run(resolve_principal(s, oidc_claims_header=claims(overage=True)))
