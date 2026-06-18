"""Tests du client Microsoft Fabric / OneLake / Power BI — transport httpx MOQUÉ
(aucun réseau réel) + fournisseur de jeton injecté.

Couvre :
  * Acquisition du jeton par AUDIENCE (Fabric / storage / Power BI) — scope correct.
  * Échec d'acquisition / Fabric non configuré → FabricError.
  * list_workspaces / list_items / list_workspace_role_assignments : bons endpoints,
    pagination Fabric (continuationToken ET continuationUri).
  * onelake_list_paths : bon filesystem/directory, pagination ADLS via l'en-tête
    x-ms-continuation.
  * onelake_read_file : bonne URL (NOM + GUID), renvoie les octets bruts.
  * get_principal_effective_access : bon endpoint preview, filtre principalId.
  * list_powerbi_datasets : myorg vs groups/{id}, pagination OData @odata.nextLink.
  * Erreurs HTTP → FabricError partout (fail-loud côté client).
"""
from __future__ import annotations

import httpx
import pytest

import app.config as config
from app.fabric_client import (
    AUDIENCE_FABRIC,
    AUDIENCE_POWERBI,
    AUDIENCE_STORAGE,
    FabricClient,
    FabricError,
    acquire_token,
)
from conftest import run


def _settings(monkeypatch):
    monkeypatch.setenv("GATEWAY_GRAPH_TENANT_ID", "tid")
    monkeypatch.setenv("GATEWAY_GRAPH_CLIENT_ID", "cid")
    monkeypatch.setenv("GATEWAY_GRAPH_CLIENT_SECRET", "sek")
    config.reset_settings_cache()
    return config.get_settings()


def _token_provider(value: str = "tok"):
    """Provider injecté : renvoie un jeton constant quelle que soit l'audience."""
    async def _provider(audience: str) -> str:  # noqa: ARG001
        return value

    return _provider


def _client(handler, settings) -> FabricClient:
    """FabricClient à transport moqué + jeton injecté (aucun réseau)."""
    transport = httpx.MockTransport(handler)
    httpx_client = httpx.AsyncClient(transport=transport)
    return FabricClient(
        settings, client=httpx_client, token_provider=_token_provider()
    )


# --------------------------------------------------------------------------- #
# 1. Acquisition du jeton par audience.                                        #
# --------------------------------------------------------------------------- #
def test_acquire_token_scope_per_audience(monkeypatch):
    settings = _settings(monkeypatch)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "abc.def"})

    async def go(audience):
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await acquire_token(settings, client, audience)

    # Fabric.
    assert run(go(AUDIENCE_FABRIC)) == "abc.def"
    assert seen["url"].endswith("/tid/oauth2/v2.0/token")
    assert "grant_type=client_credentials" in seen["body"]
    assert "scope=https%3A%2F%2Fapi.fabric.microsoft.com%2F.default" in seen["body"]
    # Storage (OneLake).
    run(go(AUDIENCE_STORAGE))
    assert "scope=https%3A%2F%2Fstorage.azure.com%2F.default" in seen["body"]
    # Power BI.
    run(go(AUDIENCE_POWERBI))
    assert "powerbi%2Fapi%2F.default" in seen["body"]


def test_acquire_token_failure_raises(monkeypatch):
    settings = _settings(monkeypatch)

    async def go():
        transport = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": "bad"}))
        async with httpx.AsyncClient(transport=transport) as client:
            await acquire_token(settings, client, AUDIENCE_FABRIC)

    with pytest.raises(FabricError):
        run(go())


def test_acquire_token_not_configured_raises(monkeypatch):
    for var in ("GATEWAY_GRAPH_TENANT_ID", "GATEWAY_GRAPH_CLIENT_ID", "GATEWAY_GRAPH_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    config.reset_settings_cache()
    settings = config.get_settings()
    assert settings.fabric_configured is False

    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        ) as client:
            await acquire_token(settings, client, AUDIENCE_FABRIC)

    with pytest.raises(FabricError):
        run(go())


def test_token_memoized_per_audience(monkeypatch):
    """Le jeton réel n'est demandé qu'UNE fois par audience (mémoïsation)."""
    settings = _settings(monkeypatch)
    token_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/oauth2/v2.0/token"):
            token_calls["n"] += 1
            return httpx.Response(200, json={"access_token": f"t{token_calls['n']}"})
        return httpx.Response(200, json={"value": []})

    async def go():
        transport = httpx.MockTransport(handler)
        httpx_client = httpx.AsyncClient(transport=transport)
        fab = FabricClient(settings, client=httpx_client)  # provider réel → token endpoint
        try:
            await fab.list_workspaces()
            await fab.list_workspaces()  # 2e appel : même audience, pas de re-token
        finally:
            await fab.aclose()

    run(go())
    assert token_calls["n"] == 1  # un seul appel token pour l'audience Fabric


# --------------------------------------------------------------------------- #
# 2. Fabric REST (contrôle) — endpoints + pagination.                         #
# --------------------------------------------------------------------------- #
def test_list_workspaces_endpoint_and_auth(monkeypatch):
    settings = _settings(monkeypatch)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"value": [{"id": "w1"}, {"id": "w2"}]})

    async def go():
        fab = _client(handler, settings)
        try:
            return await fab.list_workspaces()
        finally:
            await fab.aclose()

    ws = run(go())
    assert [w["id"] for w in ws] == ["w1", "w2"]
    assert seen["url"].endswith("/v1/workspaces")
    assert seen["auth"] == "Bearer tok"


def test_list_workspaces_pagination_continuation_token(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if "continuationToken" not in str(request.url):
            return httpx.Response(
                200,
                json={"value": [{"id": "w1"}], "continuationToken": "CT1"},
            )
        return httpx.Response(200, json={"value": [{"id": "w2"}]})

    async def go():
        fab = _client(handler, settings)
        try:
            return await fab.list_workspaces()
        finally:
            await fab.aclose()

    assert [w["id"] for w in run(go())] == ["w1", "w2"]


def test_list_items_pagination_continuation_uri(monkeypatch):
    """continuationUri (URL absolue) est suivie en priorité."""
    settings = _settings(monkeypatch)
    next_uri = "https://api.fabric.microsoft.com/v1/workspaces/ws1/items?next=2"

    def handler(request: httpx.Request) -> httpx.Response:
        if "next=2" not in str(request.url):
            return httpx.Response(
                200,
                json={"value": [{"id": "i1"}], "continuationUri": next_uri},
            )
        return httpx.Response(200, json={"value": [{"id": "i2"}]})

    async def go():
        fab = _client(handler, settings)
        try:
            return await fab.list_items("ws1")
        finally:
            await fab.aclose()

    assert [i["id"] for i in run(go())] == ["i1", "i2"]


def test_list_workspace_role_assignments_endpoint(monkeypatch):
    settings = _settings(monkeypatch)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={"value": [{"principal": {"id": "p1", "type": "User"}, "role": "Viewer"}]},
        )

    async def go():
        fab = _client(handler, settings)
        try:
            return await fab.list_workspace_role_assignments("ws1")
        finally:
            await fab.aclose()

    ra = run(go())
    assert ra[0]["role"] == "Viewer"
    assert seen["url"].endswith("/v1/workspaces/ws1/roleAssignments")


def test_list_items_requires_workspace_id(monkeypatch):
    settings = _settings(monkeypatch)

    async def go():
        fab = _client(lambda r: httpx.Response(200, json={"value": []}), settings)
        try:
            await fab.list_items("")
        finally:
            await fab.aclose()

    with pytest.raises(FabricError):
        run(go())


def test_fabric_http_error_raises(monkeypatch):
    settings = _settings(monkeypatch)

    async def go():
        fab = _client(lambda r: httpx.Response(403, json={"error": "no"}), settings)
        try:
            await fab.list_workspaces()
        finally:
            await fab.aclose()

    with pytest.raises(FabricError):
        run(go())


# --------------------------------------------------------------------------- #
# 3. OneLake (données, ADLS Gen2 DFS).                                         #
# --------------------------------------------------------------------------- #
def test_onelake_list_paths_filesystem_and_directory(monkeypatch):
    settings = _settings(monkeypatch)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={"paths": [{"name": "Files/a.csv"}, {"name": "Files/b.csv"}]},
        )

    async def go():
        fab = _client(handler, settings)
        try:
            return await fab.onelake_list_paths("myws", "mylake", "Lakehouse")
        finally:
            await fab.aclose()

    paths = run(go())
    assert [p["name"] for p in paths] == ["Files/a.csv", "Files/b.csv"]
    url = seen["url"]
    assert "onelake.dfs.fabric.microsoft.com/myws" in url
    assert "resource=filesystem" in url
    assert "recursive=true" in url
    # directory = {item}.{itemtype}/{subpath}, url-encodé.
    assert "directory=mylake.Lakehouse%2FFiles" in url


def test_onelake_list_paths_pagination_header(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if "continuation=" not in str(request.url):
            return httpx.Response(
                200,
                json={"paths": [{"name": "p1"}]},
                headers={"x-ms-continuation": "TOKEN2"},
            )
        return httpx.Response(200, json={"paths": [{"name": "p2"}]})

    async def go():
        fab = _client(handler, settings)
        try:
            return await fab.onelake_list_paths("ws", "it", "Lakehouse")
        finally:
            await fab.aclose()

    assert [p["name"] for p in run(go())] == ["p1", "p2"]


def test_onelake_read_file_url_with_name(monkeypatch):
    settings = _settings(monkeypatch)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b"hello-bytes")

    async def go():
        fab = _client(handler, settings)
        try:
            return await fab.onelake_read_file("myws", "mylake", "Lakehouse", "Files/a.csv")
        finally:
            await fab.aclose()

    data = run(go())
    assert data == b"hello-bytes"
    assert seen["url"].endswith("/myws/mylake.Lakehouse/Files/a.csv")


def test_onelake_read_file_url_with_guid(monkeypatch):
    """Avec un GUID d'item, item_type vide → pas de suffixe .type."""
    settings = _settings(monkeypatch)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, content=b"x")

    async def go():
        fab = _client(handler, settings)
        try:
            return await fab.onelake_read_file("WSGUID", "ITEMGUID", "", "Files/x")
        finally:
            await fab.aclose()

    run(go())
    assert seen["url"].endswith("/WSGUID/ITEMGUID/Files/x")


def test_onelake_read_file_error_raises(monkeypatch):
    settings = _settings(monkeypatch)

    async def go():
        fab = _client(lambda r: httpx.Response(404, content=b""), settings)
        try:
            await fab.onelake_read_file("ws", "it", "Lakehouse", "Files/x")
        finally:
            await fab.aclose()

    with pytest.raises(FabricError):
        run(go())


# --------------------------------------------------------------------------- #
# 4. OneLake accès effectif (securityPolicy/principalAccess, PREVIEW).         #
# --------------------------------------------------------------------------- #
def test_get_principal_effective_access_endpoint(monkeypatch):
    settings = _settings(monkeypatch)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"hasAccess": True})

    async def go():
        fab = _client(handler, settings)
        try:
            return await fab.get_principal_effective_access("ws1", "art1", "prin1")
        finally:
            await fab.aclose()

    access = run(go())
    assert access["hasAccess"] is True
    url = seen["url"]
    assert "/v1.0/workspaces/ws1/artifacts/art1/securityPolicy/principalAccess" in url
    assert "principalId=prin1" in url


def test_get_principal_effective_access_preview_404_raises(monkeypatch):
    settings = _settings(monkeypatch)

    async def go():
        fab = _client(lambda r: httpx.Response(404, json={}), settings)
        try:
            await fab.get_principal_effective_access("ws", "a", "p")
        finally:
            await fab.aclose()

    with pytest.raises(FabricError):
        run(go())


# --------------------------------------------------------------------------- #
# 5. Power BI REST — myorg vs groups, pagination OData.                        #
# --------------------------------------------------------------------------- #
def test_list_powerbi_datasets_myorg(monkeypatch):
    settings = _settings(monkeypatch)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"value": [{"id": "d1"}]})

    async def go():
        fab = _client(handler, settings)
        try:
            return await fab.list_powerbi_datasets()
        finally:
            await fab.aclose()

    ds = run(go())
    assert ds[0]["id"] == "d1"
    assert seen["url"].endswith("/v1.0/myorg/datasets")


def test_list_powerbi_datasets_in_group_with_pagination(monkeypatch):
    settings = _settings(monkeypatch)
    next_link = "https://api.powerbi.com/v1.0/myorg/groups/g1/datasets?$skip=1"

    def handler(request: httpx.Request) -> httpx.Response:
        if "$skip=1" not in str(request.url):
            assert "/v1.0/myorg/groups/g1/datasets" in str(request.url)
            return httpx.Response(
                200, json={"value": [{"id": "d1"}], "@odata.nextLink": next_link}
            )
        return httpx.Response(200, json={"value": [{"id": "d2"}]})

    async def go():
        fab = _client(handler, settings)
        try:
            return await fab.list_powerbi_datasets(workspace_id="g1")
        finally:
            await fab.aclose()

    assert [d["id"] for d in run(go())] == ["d1", "d2"]
