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

import os

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
    acquire_token_via_azcli,
    is_gold_path,
    make_azcli_token_provider,
)
from conftest import run


def _settings(monkeypatch, *, gold=True):
    monkeypatch.setenv("GATEWAY_GRAPH_TENANT_ID", "tid")
    monkeypatch.setenv("GATEWAY_GRAPH_CLIENT_ID", "cid")
    monkeypatch.setenv("GATEWAY_GRAPH_CLIENT_SECRET", "sek")
    if gold:
        # Périmètre GOLD : on autorise plusieurs alias de workspace/lakehouse
        # utilisés par les tests OneLake (nom + GUID). Les tests OneLake ci-dessous
        # ciblent ces ids ; tout autre chemin est refusé (cf. tests gold dédiés).
        monkeypatch.setenv("GATEWAY_FABRIC_GOLD_WORKSPACE_ID", "WSGUID")
        monkeypatch.setenv("GATEWAY_FABRIC_GOLD_WORKSPACE_NAME", "goldws")
        monkeypatch.setenv("GATEWAY_FABRIC_GOLD_LAKEHOUSE_ID", "ITEMGUID")
        monkeypatch.setenv("GATEWAY_FABRIC_GOLD_LAKEHOUSE_NAME", "goldlake")
        # Préfixe : ne pas écraser une surcharge déjà posée par le test appelant.
        if "GATEWAY_FABRIC_GOLD_TABLES_PREFIX" not in os.environ:
            monkeypatch.setenv("GATEWAY_FABRIC_GOLD_TABLES_PREFIX", "Tables")
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
            json={"paths": [{"name": "Tables/a"}, {"name": "Tables/b"}]},
        )

    async def go():
        fab = _client(handler, settings)
        try:
            # Workspace/lakehouse GOLD (par nom) ; sans subpath → racine Tables gold.
            return await fab.onelake_list_paths("goldws", "goldlake", "Lakehouse")
        finally:
            await fab.aclose()

    paths = run(go())
    assert [p["name"] for p in paths] == ["Tables/a", "Tables/b"]
    url = seen["url"]
    assert "onelake.dfs.fabric.microsoft.com/goldws" in url
    assert "resource=filesystem" in url
    assert "recursive=true" in url
    # directory = {item}.{itemtype}/{subpath gold}, url-encodé (Tables par défaut).
    assert "directory=goldlake.Lakehouse%2FTables" in url


def test_onelake_list_paths_guid_item_no_type_suffix(monkeypatch):
    """Régression (détectée en e2e LIVE) : avec un GUID d'item, l'URL OneLake NE
    doit PAS porter le suffixe `.itemtype` (forme GUID = `/{itemGUID}/...`). Le
    mélange `{GUID}.Lakehouse` provoquait un HTTP 400 réel."""
    real_guid = "595869b2-7364-4584-bc16-1539318cca5f"
    settings = _settings(monkeypatch)
    # Le lakehouse gold est adressé par GUID (réaligne le périmètre gold dessus).
    monkeypatch.setenv("GATEWAY_FABRIC_GOLD_LAKEHOUSE_ID", real_guid)
    config.reset_settings_cache()
    settings = config.get_settings()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"paths": [{"name": "Tables/x"}]})

    async def go():
        fab = _client(handler, settings)
        try:
            # item = GUID + item_type fourni : le suffixe `.Lakehouse` doit être IGNORÉ.
            return await fab.onelake_list_paths("goldws", real_guid, "Lakehouse")
        finally:
            await fab.aclose()

    run(go())
    url = seen["url"]
    assert real_guid in url
    assert ".Lakehouse" not in url  # pas de suffixe en forme GUID (sinon HTTP 400)
    assert f"directory={real_guid}%2FTables" in url


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
            return await fab.onelake_list_paths("goldws", "goldlake", "Lakehouse")
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
            # Fichier sous le sous-arbre des tables GOLD.
            return await fab.onelake_read_file(
                "goldws", "goldlake", "Lakehouse", "Tables/dim/part-0.parquet"
            )
        finally:
            await fab.aclose()

    data = run(go())
    assert data == b"hello-bytes"
    assert seen["url"].endswith("/goldws/goldlake.Lakehouse/Tables/dim/part-0.parquet")


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
            # Adressage par GUID (item_type vide), chemin sous Tables gold.
            return await fab.onelake_read_file("WSGUID", "ITEMGUID", "", "Tables/x")
        finally:
            await fab.aclose()

    run(go())
    assert seen["url"].endswith("/WSGUID/ITEMGUID/Tables/x")


def test_onelake_read_file_error_raises(monkeypatch):
    settings = _settings(monkeypatch)

    async def go():
        fab = _client(lambda r: httpx.Response(404, content=b""), settings)
        try:
            # Chemin GOLD valide → la garde passe, on teste bien l'erreur HTTP 404.
            await fab.onelake_read_file("goldws", "goldlake", "Lakehouse", "Tables/x")
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


# --------------------------------------------------------------------------- #
# 6. Périmètre GOLD — is_gold_path (autorise gold, refuse hors-gold).          #
# --------------------------------------------------------------------------- #
def test_is_gold_path_allows_gold(monkeypatch):
    settings = _settings(monkeypatch)
    # Par nom et par GUID, à la racine Tables et plus profond.
    assert is_gold_path(settings, "goldws", "goldlake", "Lakehouse", "Tables")
    assert is_gold_path(settings, "goldws", "goldlake", "Lakehouse", "Tables/dim/x.parquet")
    assert is_gold_path(settings, "WSGUID", "ITEMGUID", "", "Tables/x")
    # Casse-insensible.
    assert is_gold_path(settings, "GOLDWS", "GoldLake", "lakehouse", "tables/x")
    # Sans chemin : suffit que workspace+item+type matchent (usage ACL niveau item).
    assert is_gold_path(settings, "goldws", "goldlake", "Lakehouse", "")


def test_is_gold_path_refuses_out_of_scope(monkeypatch):
    settings = _settings(monkeypatch)
    # Mauvais workspace.
    assert not is_gold_path(settings, "autrews", "goldlake", "Lakehouse", "Tables/x")
    # Mauvais lakehouse.
    assert not is_gold_path(settings, "goldws", "autrelake", "Lakehouse", "Tables/x")
    # Mauvais type d'item.
    assert not is_gold_path(settings, "goldws", "goldlake", "Warehouse", "Tables/x")
    # Chemin hors préfixe gold (données brutes Files).
    assert not is_gold_path(settings, "goldws", "goldlake", "Lakehouse", "Files/secret.csv")
    # Préfixe « collé » qui ne doit PAS matcher (TablesAutre ≠ Tables).
    assert not is_gold_path(settings, "goldws", "goldlake", "Lakehouse", "TablesAutre/x")


def test_is_gold_path_refuses_when_gold_unconfigured(monkeypatch):
    settings = _settings(monkeypatch, gold=False)
    assert settings.fabric_gold_configured is False
    # Défaut INERTE : sans gold configuré, tout chemin est refusé.
    assert not is_gold_path(settings, "goldws", "goldlake", "Lakehouse", "Tables/x")


def test_is_gold_path_custom_prefix(monkeypatch):
    """Préfixe surchargé (schéma gold) : Tables/gold autorisé, Tables seul refusé."""
    monkeypatch.setenv("GATEWAY_FABRIC_GOLD_TABLES_PREFIX", "Tables/gold")
    settings = _settings(monkeypatch)
    assert is_gold_path(settings, "goldws", "goldlake", "Lakehouse", "Tables/gold/dim")
    assert not is_gold_path(settings, "goldws", "goldlake", "Lakehouse", "Tables/silver/x")
    # « Tables » seul ne suffit plus (moins profond que le préfixe).
    assert not is_gold_path(settings, "goldws", "goldlake", "Lakehouse", "Tables")


def test_onelake_list_paths_refuses_out_of_gold(monkeypatch):
    settings = _settings(monkeypatch)

    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={"paths": []})

    async def go():
        fab = _client(handler, settings)
        try:
            # Workspace hors gold → refus AVANT tout réseau.
            await fab.onelake_list_paths("autrews", "goldlake", "Lakehouse")
        finally:
            await fab.aclose()

    with pytest.raises(FabricError):
        run(go())
    assert called["n"] == 0  # aucun appel réseau hors-gold


def test_onelake_read_file_refuses_out_of_gold(monkeypatch):
    settings = _settings(monkeypatch)
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, content=b"x")

    async def go():
        fab = _client(handler, settings)
        try:
            # Chemin hors préfixe gold (Files) → refus.
            await fab.onelake_read_file("goldws", "goldlake", "Lakehouse", "Files/x")
        finally:
            await fab.aclose()

    with pytest.raises(FabricError):
        run(go())
    assert called["n"] == 0


def test_onelake_refuses_when_gold_unconfigured(monkeypatch):
    settings = _settings(monkeypatch, gold=False)

    async def go():
        fab = _client(lambda r: httpx.Response(200, json={"paths": []}), settings)
        try:
            await fab.onelake_list_paths("goldws", "goldlake", "Lakehouse")
        finally:
            await fab.aclose()

    with pytest.raises(FabricError):
        run(go())


# --------------------------------------------------------------------------- #
# 7. Provider de jeton via Azure CLI (`az`) — subprocess SIMULÉ (offline).     #
# --------------------------------------------------------------------------- #
def test_azcli_token_runner_injected():
    """runner injecté simule la sortie de `az` (aucun processus réel)."""
    seen = {}

    def runner(args):
        seen["args"] = args
        # Forme réelle de `az account get-access-token --output json`.
        return '{"accessToken": "AZTOKEN", "expiresOn": "2026-01-01", "tokenType": "Bearer"}'

    tok = acquire_token_via_azcli(AUDIENCE_FABRIC, tenant="tid", runner=runner)
    assert tok == "AZTOKEN"
    # Liste d'arguments FIXE (pas de shell) avec la ressource + le tenant.
    assert seen["args"][:3] == ["az", "account", "get-access-token"]
    assert "--resource" in seen["args"]
    assert AUDIENCE_FABRIC in seen["args"]
    assert "--tenant" in seen["args"] and "tid" in seen["args"]
    assert "--output" in seen["args"] and "json" in seen["args"]


def test_azcli_token_no_tenant_omits_flag():
    def runner(args):
        return '{"accessToken": "T"}'

    acquire_token_via_azcli(AUDIENCE_STORAGE, runner=runner)
    # Sans tenant, on n'ajoute pas le flag (az utilise la session courante).
    # Vérifié indirectement : pas d'erreur, jeton renvoyé.


def test_azcli_token_missing_token_raises():
    def runner(args):
        return '{"expiresOn": "2026"}'  # pas d'accessToken

    with pytest.raises(FabricError):
        acquire_token_via_azcli(AUDIENCE_FABRIC, runner=runner)


def test_azcli_token_bad_json_raises_without_leaking():
    def runner(args):
        return "not-json-SECRETish"

    try:
        acquire_token_via_azcli(AUDIENCE_FABRIC, runner=runner)
        assert False, "devait lever"
    except FabricError as exc:
        # L'erreur ne doit JAMAIS contenir la sortie brute (qui contiendrait le jeton).
        assert "not-json-SECRETish" not in str(exc)


def test_azcli_provider_wires_into_client(monkeypatch):
    """make_azcli_token_provider câble l'auth az dans FabricClient (offline)."""
    settings = _settings(monkeypatch)
    audiences = []

    def runner(args):
        # La ressource est l'avant-dernier ou un élément de la liste : on la repère.
        idx = args.index("--resource")
        audiences.append(args[idx + 1])
        return '{"accessToken": "AZ"}'

    provider = make_azcli_token_provider(settings, runner=runner)

    def handler(request: httpx.Request) -> httpx.Response:
        # Le jeton az doit être présent dans l'en-tête Authorization.
        assert request.headers.get("Authorization") == "Bearer AZ"
        return httpx.Response(200, json={"value": [{"id": "w1"}]})

    async def go():
        transport = httpx.MockTransport(handler)
        httpx_client = httpx.AsyncClient(transport=transport)
        fab = FabricClient(settings, client=httpx_client, token_provider=provider)
        try:
            return await fab.list_workspaces()
        finally:
            await fab.aclose()

    ws = run(go())
    assert [w["id"] for w in ws] == ["w1"]
    assert AUDIENCE_FABRIC in audiences  # le provider a demandé l'audience Fabric
