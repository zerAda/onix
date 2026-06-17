"""Tests de l'ACL par-document auto-dérivée de SharePoint via Graph (OFFLINE).

AUCUN appel réseau réel : le transport httpx est MOQUÉ (`httpx.MockTransport`)
et le fournisseur de jeton est injecté (constante). Couvre :

  * `fetch_item_principals` : parse `grantedToV2.{user,group,siteGroup}` vers les
    bons sets ; tolère les champs manquants ; inclut les permissions HÉRITÉES ;
    ignore les permissions sans rôle de lecture ; gère `grantedToIdentitiesV2`
    (liste) et la pagination `@odata.nextLink`.
  * `build_graph_acl` : construit des `_Entry` correctes ; un item en échec
    (GraphError) ou de mapping invalide est OMIS → refusé sous default deny.
  * `GraphDocACL.is_authorized` : match groupe casse-insensible (comme
    `StaticDocACL`) + override user (UPN/oid).
  * `CompositeDocACL` OR-merge : un doc autorisé par l'UNE des sources passe.
  * TTL/refresh avec une HORLOGE injectée (pas de sleep).
  * Isolation RBAC : User A (G1) vs User B (G2) → sets autorisés différents sur
    les mêmes documents candidats.
  * Le CLI `scripts/sync-doc-acl.py` écrit un `doc_acl.json` valide depuis un
    mapping + Graph moqué, relisible par `StaticDocACL.from_file`.
"""
from __future__ import annotations

import importlib.util
import json
import os
from types import SimpleNamespace

import httpx
import pytest

import app.config as config
from app.doc_acl import CompositeDocACL, StaticDocACL, _Entry
from app.graph_acl import (
    GraphDocACL,
    GraphSession,
    build_graph_acl,
    entries_to_acl_obj,
    fetch_item_principals,
    load_mapping,
)
from app.graph_client import GraphError
from conftest import run


# --------------------------------------------------------------------------- #
# Helpers — Principal minimal, GraphSession moquée, fabriques de permissions.  #
# --------------------------------------------------------------------------- #
def _principal(*, user_id="u1", upn="alice@contoso.fr", groups=("G1",)):
    return SimpleNamespace(user_id=user_id, upn=upn, group_ids=list(groups))


def _settings(monkeypatch):
    monkeypatch.setenv("GATEWAY_GRAPH_TENANT_ID", "tid")
    monkeypatch.setenv("GATEWAY_GRAPH_CLIENT_ID", "cid")
    monkeypatch.setenv("GATEWAY_GRAPH_CLIENT_SECRET", "sek")
    config.reset_settings_cache()
    return config.get_settings()


def _token(value: str = "tok"):
    async def _provider() -> str:
        return value

    return _provider


def _graph_session(handler, settings) -> GraphSession:
    """GraphSession à transport moqué + jeton constant (aucun réseau)."""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return GraphSession(client=client, settings=settings, token_provider=_token())


def _perm(roles, *, user=None, group=None, site_group=None, inherited=False, as_list=False):
    """Construit un objet *permission* Graph minimal."""
    ident: dict = {}
    if user is not None:
        ident["user"] = {"id": user}
    if group is not None:
        ident["group"] = {"id": group}
    if site_group is not None:
        ident["siteGroup"] = {"id": site_group}
    perm: dict = {"roles": roles}
    if as_list:
        perm["grantedToIdentitiesV2"] = [ident]
    else:
        perm["grantedToV2"] = ident
    if inherited:
        perm["inheritedFrom"] = {"driveId": "d", "id": "parent"}
    return perm


# --------------------------------------------------------------------------- #
# 1. fetch_item_principals — parsing grantedToV2 (user / group / siteGroup).   #
# --------------------------------------------------------------------------- #
def test_fetch_parses_user_group_sitegroup(monkeypatch):
    settings = _settings(monkeypatch)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "value": [
                    _perm(["read"], user="USER-OID-1"),
                    _perm(["write"], group="GROUP-OID-1"),
                    _perm(["read"], site_group="42"),
                ]
            },
        )

    async def go():
        graph = _graph_session(handler, settings)
        try:
            return await fetch_item_principals(graph, "site1", "drive1", "item1")
        finally:
            await graph.client.aclose()

    users, groups = run(go())
    # Identités lowercased (comparaison casse-insensible).
    assert users == {"user-oid-1"}
    assert groups == {"group-oid-1", "42"}  # group Entra + siteGroup
    # Bon endpoint + jeton injecté présent.
    assert "/sites/site1/drives/drive1/items/item1/permissions" in seen["url"]
    assert seen["auth"] == "Bearer tok"


def test_fetch_tolerates_missing_fields(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {"roles": ["read"]},  # grantedToV2 absent
                    {"roles": ["read"], "grantedToV2": {}},  # identitySet vide
                    {"roles": ["read"], "grantedToV2": {"user": {}}},  # user sans id
                    _perm(["read"], user="ok-user"),  # une seule identité valide
                ]
            },
        )

    async def go():
        graph = _graph_session(handler, settings)
        try:
            return await fetch_item_principals(graph, "s", "d", "i")
        finally:
            await graph.client.aclose()

    users, groups = run(go())
    assert users == {"ok-user"}
    assert groups == set()


def test_fetch_includes_inherited_permissions(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    _perm(["read"], group="direct-grp"),
                    _perm(["read"], group="inherited-grp", inherited=True),
                ]
            },
        )

    async def go():
        graph = _graph_session(handler, settings)
        try:
            return await fetch_item_principals(graph, "s", "d", "i")
        finally:
            await graph.client.aclose()

    _, groups = run(go())
    # L'héritage de lecture donne bien l'accès → inclus.
    assert groups == {"direct-grp", "inherited-grp"}


def test_fetch_ignores_permissions_without_read_role(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    _perm([], user="no-role"),  # aucun rôle
                    {"roles": ["restricted"], "grantedToV2": {"user": {"id": "weird"}}},
                    {"link": {"scope": "anonymous"}},  # lien anonyme, pas de roles
                    _perm(["read"], user="legit"),
                ]
            },
        )

    async def go():
        graph = _graph_session(handler, settings)
        try:
            return await fetch_item_principals(graph, "s", "d", "i")
        finally:
            await graph.client.aclose()

    users, _ = run(go())
    assert users == {"legit"}


def test_fetch_handles_granted_to_identities_list(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "roles": ["read"],
                        "grantedToIdentitiesV2": [
                            {"user": {"id": "u-a"}},
                            {"group": {"id": "g-b"}},
                        ],
                    }
                ]
            },
        )

    async def go():
        graph = _graph_session(handler, settings)
        try:
            return await fetch_item_principals(graph, "s", "d", "i")
        finally:
            await graph.client.aclose()

    users, groups = run(go())
    assert users == {"u-a"}
    assert groups == {"g-b"}


def test_fetch_follows_pagination(monkeypatch):
    settings = _settings(monkeypatch)
    next_url = "https://graph.microsoft.com/v1.0/next?$skiptoken=ABC"

    def handler(request: httpx.Request) -> httpx.Response:
        if "$skiptoken" not in str(request.url):
            return httpx.Response(
                200,
                json={
                    "value": [_perm(["read"], user="page1")],
                    "@odata.nextLink": next_url,
                },
            )
        return httpx.Response(200, json={"value": [_perm(["read"], user="page2")]})

    async def go():
        graph = _graph_session(handler, settings)
        try:
            return await fetch_item_principals(graph, "s", "d", "i")
        finally:
            await graph.client.aclose()

    users, _ = run(go())
    assert users == {"page1", "page2"}


def test_fetch_http_error_raises_grapherror(monkeypatch):
    settings = _settings(monkeypatch)

    async def go():
        graph = _graph_session(
            lambda r: httpx.Response(403, json={"error": "forbidden"}), settings
        )
        try:
            await fetch_item_principals(graph, "s", "d", "i")
        finally:
            await graph.client.aclose()

    with pytest.raises(GraphError):
        run(go())


def test_fetch_requires_ids(monkeypatch):
    settings = _settings(monkeypatch)

    async def go():
        graph = _graph_session(lambda r: httpx.Response(200, json={"value": []}), settings)
        try:
            await fetch_item_principals(graph, "", "d", "i")
        finally:
            await graph.client.aclose()

    with pytest.raises(GraphError):
        run(go())


# --------------------------------------------------------------------------- #
# 2. build_graph_acl — entrées correctes ; item en échec OMIS (deny).          #
# --------------------------------------------------------------------------- #
def test_build_graph_acl_builds_entries(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "item-nord" in url:
            return httpx.Response(200, json={"value": [_perm(["read"], group="G-NORD")]})
        if "item-sud" in url:
            return httpx.Response(
                200, json={"value": [_perm(["read"], group="G-SUD", site_group=None)]}
            )
        return httpx.Response(404, json={"error": "not found"})

    mapping = {
        "_version": 1,  # clé méta ignorée
        "doc-nord": {"site_id": "s", "drive_id": "d", "item_id": "item-nord"},
        "doc-sud": {"site_id": "s", "drive_id": "d", "item_id": "item-sud"},
    }

    async def go():
        graph = _graph_session(handler, settings)
        try:
            return await build_graph_acl(graph, mapping, default_policy="deny")
        finally:
            await graph.client.aclose()

    acl = run(go())
    assert len(acl) == 2
    assert acl.is_authorized("doc-nord", _principal(groups=["g-nord"]))  # casse-insensible
    assert not acl.is_authorized("doc-nord", _principal(groups=["g-sud"]))
    assert acl.is_authorized("doc-sud", _principal(groups=["G-SUD"]))


def test_build_graph_acl_omits_failing_item(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if "item-ok" in str(request.url):
            return httpx.Response(200, json={"value": [_perm(["read"], group="G-OK")]})
        # item-ko : Graph renvoie une erreur.
        return httpx.Response(500, json={"error": "boom"})

    mapping = {
        "doc-ok": {"site_id": "s", "drive_id": "d", "item_id": "item-ok"},
        "doc-ko": {"site_id": "s", "drive_id": "d", "item_id": "item-ko"},
    }

    async def go():
        graph = _graph_session(handler, settings)
        try:
            return await build_graph_acl(graph, mapping, default_policy="deny")
        finally:
            await graph.client.aclose()

    acl = run(go())
    # doc-ok présent ; doc-ko OMIS → refusé sous default deny.
    assert acl.is_authorized("doc-ok", _principal(groups=["G-OK"]))
    assert not acl.is_authorized("doc-ko", _principal(groups=["G-OK", "anything"]))
    assert len(acl) == 1


def test_build_graph_acl_skips_invalid_mapping_entry(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [_perm(["read"], user="u-ok")]})

    mapping = {
        "doc-good": {"site_id": "s", "drive_id": "d", "item_id": "i"},
        "doc-bad1": {"site_id": "s", "drive_id": "d"},  # item_id manquant
        "doc-bad2": "not-an-object",
    }

    async def go():
        graph = _graph_session(handler, settings)
        try:
            return await build_graph_acl(graph, mapping)
        finally:
            await graph.client.aclose()

    acl = run(go())
    assert len(acl) == 1
    assert acl.is_authorized("doc-good", _principal(user_id="u-ok", upn=None, groups=[]))
    assert not acl.is_authorized("doc-bad1", _principal(groups=["x"]))


# --------------------------------------------------------------------------- #
# 3. GraphDocACL.is_authorized — groupe (casse-insensible) + override user.    #
# --------------------------------------------------------------------------- #
def test_graphdocacl_group_match_case_insensitive():
    acl = GraphDocACL(
        {"doc1": _Entry(groups=frozenset({"g-nord"}), users=frozenset())}
    )
    assert acl.is_authorized("doc1", _principal(groups=["G-NORD"]))
    assert not acl.is_authorized("doc1", _principal(groups=["G-SUD"]))


def test_graphdocacl_user_override_by_upn_and_oid():
    acl = GraphDocACL(
        {
            "doc1": _Entry(
                groups=frozenset({"g3"}),
                users=frozenset({"alice@contoso.fr", "oid-bob"}),
            )
        }
    )
    # UPN nominatif autorise même hors-groupe.
    assert acl.is_authorized("doc1", _principal(upn="alice@contoso.fr", groups=["other"]))
    # oid (user_id) autorise aussi.
    assert acl.is_authorized(
        "doc1", _principal(user_id="oid-bob", upn="bob@x", groups=["other"])
    )
    # Sinon refusé (ni groupe ni override).
    assert not acl.is_authorized(
        "doc1", _principal(user_id="oid-eve", upn="eve@x", groups=["other"])
    )


def test_graphdocacl_unknown_doc_follows_default_policy():
    deny = GraphDocACL({}, default_policy="deny")
    allow = GraphDocACL({}, default_policy="allow")
    assert not deny.is_authorized("absent", _principal(groups=["x"]))
    assert allow.is_authorized("absent", _principal(groups=["x"]))


def test_graphdocacl_invalid_default_policy_raises():
    with pytest.raises(ValueError):
        GraphDocACL({}, default_policy="maybe")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 4. CompositeDocACL OR-merge — statique + Graph.                              #
# --------------------------------------------------------------------------- #
def test_composite_or_merge_static_and_graph():
    static = StaticDocACL.from_obj({"doc1": {"groups": ["G1"]}}, default_policy="deny")
    graph = GraphDocACL(
        {"doc1": _Entry(groups=frozenset({"g2"}), users=frozenset())},
        default_policy="deny",
    )
    composite = CompositeDocACL([static, graph])
    # G1 via static, G2 via graph → les deux passent (OR).
    assert composite.is_authorized("doc1", _principal(groups=["G1"]))
    assert composite.is_authorized("doc1", _principal(groups=["G2"]))
    # G99 dans aucune source → refus.
    assert not composite.is_authorized("doc1", _principal(groups=["G99"]))


def test_composite_authorized_ids_union():
    static = StaticDocACL.from_obj({"docA": {"groups": ["G1"]}})
    graph = GraphDocACL({"docB": _Entry(groups=frozenset({"g1"}), users=frozenset())})
    composite = CompositeDocACL([static, graph])
    allowed = composite.authorized_ids(["docA", "docB", "docC"], _principal(groups=["G1"]))
    assert allowed == {"docA", "docB"}  # docC dans aucune source


# --------------------------------------------------------------------------- #
# 5. TTL / refresh avec horloge injectée.                                     #
# --------------------------------------------------------------------------- #
class _FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_ttl_stale_after_expiry_with_injected_clock():
    clock = _FakeClock(1000.0)
    acl = GraphDocACL(
        {"doc1": _Entry(groups=frozenset({"g1"}), users=frozenset())},
        ttl_seconds=900,
        clock=clock,
    )
    assert not acl.is_stale()  # tout juste chargé
    clock.advance(899)
    assert not acl.is_stale()
    clock.advance(2)  # 901s > TTL
    assert acl.is_stale()


def test_refresh_resets_ttl_and_swaps_entries():
    clock = _FakeClock(0.0)
    acl = GraphDocACL(
        {"old": _Entry(groups=frozenset({"g1"}), users=frozenset())},
        ttl_seconds=100,
        clock=clock,
    )
    clock.advance(150)
    assert acl.is_stale()
    # Refresh : nouveau contenu + TTL réarmé.
    acl.refresh({"new": _Entry(groups=frozenset({"g2"}), users=frozenset())})
    assert not acl.is_stale()
    assert acl.is_authorized("new", _principal(groups=["g2"]))
    assert not acl.is_authorized("old", _principal(groups=["g1"]))  # ancien retiré


def test_empty_constructor_is_stale_until_first_refresh():
    clock = _FakeClock(0.0)
    acl = GraphDocACL(clock=clock)  # aucune entrée fournie → jamais chargé
    assert acl.is_stale()
    acl.refresh({"d": _Entry(groups=frozenset({"g"}), users=frozenset())})
    assert not acl.is_stale()


def test_ttl_zero_never_stale():
    clock = _FakeClock(0.0)
    acl = GraphDocACL(
        {"doc": _Entry(groups=frozenset({"g"}), users=frozenset())},
        ttl_seconds=0,
        clock=clock,
    )
    clock.advance(10**9)
    assert not acl.is_stale()


# --------------------------------------------------------------------------- #
# 6. Isolation RBAC — User A (G1) vs User B (G2) sur mêmes candidats.          #
# --------------------------------------------------------------------------- #
def test_rbac_isolation_two_users_distinct_authorized_sets(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "i-nord" in url:
            return httpx.Response(200, json={"value": [_perm(["read"], group="G1")]})
        if "i-sud" in url:
            return httpx.Response(200, json={"value": [_perm(["read"], group="G2")]})
        if "i-shared" in url:
            return httpx.Response(
                200,
                json={
                    "value": [
                        _perm(["read"], group="G1"),
                        _perm(["read"], group="G2"),
                    ]
                },
            )
        return httpx.Response(404, json={})

    mapping = {
        "doc-nord": {"site_id": "s", "drive_id": "d", "item_id": "i-nord"},
        "doc-sud": {"site_id": "s", "drive_id": "d", "item_id": "i-sud"},
        "doc-shared": {"site_id": "s", "drive_id": "d", "item_id": "i-shared"},
    }

    async def go():
        graph = _graph_session(handler, settings)
        try:
            return await build_graph_acl(graph, mapping)
        finally:
            await graph.client.aclose()

    acl = run(go())
    candidates = ["doc-nord", "doc-sud", "doc-shared"]
    user_a = _principal(user_id="a", upn="a@x", groups=["G1"])
    user_b = _principal(user_id="b", upn="b@x", groups=["G2"])
    set_a = acl.authorized_ids(candidates, user_a)
    set_b = acl.authorized_ids(candidates, user_b)
    assert set_a == {"doc-nord", "doc-shared"}
    assert set_b == {"doc-sud", "doc-shared"}
    assert set_a != set_b


# --------------------------------------------------------------------------- #
# 7. load_mapping / entries_to_acl_obj.                                        #
# --------------------------------------------------------------------------- #
def test_load_mapping_missing_raises():
    with pytest.raises(GraphError):
        load_mapping("/no/such/mapping.json")


def test_load_mapping_non_object_raises(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(GraphError):
        load_mapping(str(p))


def test_entries_to_acl_obj_is_sorted_and_roundtrips():
    acl = GraphDocACL(
        {
            "docB": _Entry(groups=frozenset({"g2", "g1"}), users=frozenset({"u@x"})),
            "docA": _Entry(groups=frozenset({"g1"}), users=frozenset()),
        }
    )
    obj = entries_to_acl_obj(acl)
    assert list(obj.keys()) == ["docA", "docB"]  # trié
    assert obj["docB"]["groups"] == ["g1", "g2"]  # listes triées
    # Roundtrip : StaticDocACL relit ce qu'on a sérialisé.
    static = StaticDocACL.from_obj(obj)
    assert static.is_authorized("docB", _principal(groups=["g2"]))
    assert static.is_authorized("docB", _principal(upn="u@x", groups=[]))


# --------------------------------------------------------------------------- #
# 8. CLI sync-doc-acl.py — écrit un doc_acl.json valide depuis un mapping +    #
#    Graph moqué, relisible par StaticDocACL.                                  #
# --------------------------------------------------------------------------- #
def _load_sync_cli():
    """Importe scripts/sync-doc-acl.py par chemin (nom non importable directement
    à cause du tiret)."""
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.abspath(os.path.join(here, "..", "..", "scripts", "sync-doc-acl.py"))
    spec = importlib.util.spec_from_file_location("sync_doc_acl_cli", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sync_cli_writes_valid_doc_acl(monkeypatch, tmp_path):
    _settings(monkeypatch)  # creds Graph présents
    cli = _load_sync_cli()

    # Mapping d'entrée.
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(
        json.dumps(
            {
                "_version": 1,
                "doc-nord": {"site_id": "s", "drive_id": "d", "item_id": "i-nord"},
                "doc-sud": {"site_id": "s", "drive_id": "d", "item_id": "i-sud"},
                "doc-ko": {"site_id": "s", "drive_id": "d", "item_id": "i-ko"},
            }
        ),
        encoding="utf-8",
    )
    out_path = tmp_path / "doc_acl.json"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "i-nord" in url:
            return httpx.Response(200, json={"value": [_perm(["read"], group="G-NORD")]})
        if "i-sud" in url:
            return httpx.Response(
                200,
                json={"value": [_perm(["read"], group="G-SUD", user="dir@contoso.fr")]},
            )
        return httpx.Response(500, json={"error": "boom"})  # i-ko échoue → omis

    # Patcher httpx.AsyncClient utilisé par le CLI pour injecter le transport moqué
    # ET court-circuiter l'acquisition de jeton (token_provider via GraphSession).
    real_async_client = httpx.AsyncClient

    def _fake_async_client(*args, **kwargs):  # noqa: ANN001
        kwargs.pop("timeout", None)
        return real_async_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(cli.httpx, "AsyncClient", _fake_async_client)
    # Éviter tout appel réseau de jeton : forcer un token_provider sur la session.
    real_session_cls = cli.GraphSession

    def _session_with_token(*args, **kwargs):  # noqa: ANN001
        kwargs.setdefault("token_provider", _token())
        return real_session_cls(*args, **kwargs)

    monkeypatch.setattr(cli, "GraphSession", _session_with_token)

    rc = cli.main(["--mapping", str(mapping_path), "--out", str(out_path)])
    assert rc == 0
    assert out_path.exists()

    # Le fichier écrit est un doc_acl.json VALIDE, relisible par StaticDocACL.
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["_doc_count"] == 2  # i-ko omis
    acl = StaticDocACL.from_file(str(out_path))
    assert acl.is_authorized("doc-nord", _principal(groups=["g-nord"]))
    assert acl.is_authorized("doc-sud", _principal(groups=["g-sud"]))
    # Override user nominatif (dir) sérialisé puis relu.
    assert acl.is_authorized("doc-sud", _principal(upn="dir@contoso.fr", groups=[]))
    # i-ko absent → refusé (default deny).
    assert not acl.is_authorized("doc-ko", _principal(groups=["g-nord", "g-sud"]))


def test_sync_cli_without_graph_config_errors(monkeypatch, tmp_path):
    # Pas de creds Graph → code retour 2 (erreur de configuration), aucun fichier.
    for var in ("GATEWAY_GRAPH_TENANT_ID", "GATEWAY_GRAPH_CLIENT_ID", "GATEWAY_GRAPH_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    config.reset_settings_cache()
    cli = _load_sync_cli()
    mapping_path = tmp_path / "m.json"
    mapping_path.write_text(json.dumps({"d": {"site_id": "s", "drive_id": "d", "item_id": "i"}}))
    out_path = tmp_path / "out.json"
    rc = cli.main(["--mapping", str(mapping_path), "--out", str(out_path)])
    assert rc == 2
    assert not out_path.exists()
