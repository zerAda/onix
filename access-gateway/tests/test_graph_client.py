"""Tests du client Graph avec transport httpx MOQUÉ (aucun réseau réel).

Vérifie : bon endpoint (transitiveMemberOf + OData cast microsoft.graph.group),
$select=id,displayName, en-tête ConsistencyLevel: eventual, pagination
@odata.nextLink, extraction des GUID, et acquisition du jeton (client credentials).
"""
from __future__ import annotations

import httpx
import pytest

import app.config as config
from app.graph_client import GraphError, acquire_app_token, fetch_transitive_group_ids
from conftest import run


def _settings(monkeypatch):
    monkeypatch.setenv("GATEWAY_GRAPH_TENANT_ID", "tid")
    monkeypatch.setenv("GATEWAY_GRAPH_CLIENT_ID", "cid")
    monkeypatch.setenv("GATEWAY_GRAPH_CLIENT_SECRET", "sek")
    config.reset_settings_cache()
    return config.get_settings()


def _token(value: str):
    async def _provider() -> str:
        return value

    return _provider


def test_token_acquisition(monkeypatch):
    settings = _settings(monkeypatch)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "abc.def"})

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await acquire_app_token(settings, client)

    token = run(go())
    assert token == "abc.def"
    assert seen["url"].endswith("/tid/oauth2/v2.0/token")
    assert "grant_type=client_credentials" in seen["body"]
    assert "scope=https%3A%2F%2Fgraph.microsoft.com%2F.default" in seen["body"]


def test_token_failure_raises(monkeypatch):
    settings = _settings(monkeypatch)

    async def go():
        transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "bad"}))
        async with httpx.AsyncClient(transport=transport) as client:
            await acquire_app_token(settings, client)

    with pytest.raises(GraphError):
        run(go())


def test_transitive_groups_with_pagination(monkeypatch):
    settings = _settings(monkeypatch)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if "$skiptoken" not in str(request.url):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": "g1", "displayName": "Nord"},
                        {"id": "g2", "displayName": "Sud"},
                    ],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/next?$skiptoken=XYZ",
                },
            )
        return httpx.Response(200, json={"value": [{"id": "g3", "displayName": "Dir"}]})

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await fetch_transitive_group_ids(
                "user-oid", settings, client=client, token_provider=_token("tok")
            )

    ids = run(go())
    assert ids == ["g1", "g2", "g3"]
    first = calls[0]
    assert "/users/user-oid/transitiveMemberOf/microsoft.graph.group" in str(first.url)
    url = str(first.url)
    assert "%24select=id%2CdisplayName" in url or "$select=id,displayName" in url
    assert first.headers.get("ConsistencyLevel") == "eventual"
    assert first.headers.get("Authorization") == "Bearer tok"


def test_limited_info_object_falls_back_to_displayname(monkeypatch):
    settings = _settings(monkeypatch)

    async def go():
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"value": [{"id": None, "displayName": "Nord"}]})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            return await fetch_transitive_group_ids(
                "u", settings, client=client, token_provider=_token("t")
            )

    assert run(go()) == ["Nord"]


def test_http_error_raises_grapherror(monkeypatch):
    settings = _settings(monkeypatch)

    async def go():
        transport = httpx.MockTransport(lambda r: httpx.Response(403, json={"error": "forbidden"}))
        async with httpx.AsyncClient(transport=transport) as client:
            await fetch_transitive_group_ids(
                "u", settings, client=client, token_provider=_token("t")
            )

    with pytest.raises(GraphError):
        run(go())
