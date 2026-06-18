"""Tests de la décision d'autorisation Fabric (OFFLINE) — fail-closed.

AUCUN appel réseau réel : transport httpx MOQUÉ + jeton injecté. Couvre :
  * principal_has_read_role (pur) : rôle de lecture direct vs via groupe ; rôle
    non-lecture ignoré ; assignment malformé ignoré ; casse-insensible.
  * can_principal_read : autorisé via roleAssignments (direct / groupe) ;
    élargissement via principalAccess OneLake ; FAIL-CLOSED si Fabric non
    configuré, ids manquants, erreur HTTP, format inattendu.
  * Preview OneLake indisponible (404) → ignorée, ne fait pas pencher vers True ;
    mais accorde si (a) accorde.
  * authorized_items : roleAssignments lus une fois ; sous-ensemble correct ;
    isolation deux principals.
  * _onelake_access_grants_read : formes connues (hasAccess / actions / roles) ;
    forme inconnue → False.
"""
from __future__ import annotations

import httpx
import pytest

import app.config as config
from app.fabric_acl import (
    _onelake_access_grants_read,
    authorized_items,
    can_principal_read,
    principal_has_read_role,
)
from app.fabric_client import FabricClient
from conftest import run


def _settings(monkeypatch, *, configured=True):
    if configured:
        monkeypatch.setenv("GATEWAY_GRAPH_TENANT_ID", "tid")
        monkeypatch.setenv("GATEWAY_GRAPH_CLIENT_ID", "cid")
        monkeypatch.setenv("GATEWAY_GRAPH_CLIENT_SECRET", "sek")
    else:
        for var in (
            "GATEWAY_GRAPH_TENANT_ID",
            "GATEWAY_GRAPH_CLIENT_ID",
            "GATEWAY_GRAPH_CLIENT_SECRET",
            "GATEWAY_FABRIC_TENANT_ID",
            "GATEWAY_FABRIC_CLIENT_ID",
            "GATEWAY_FABRIC_CLIENT_SECRET",
        ):
            monkeypatch.delenv(var, raising=False)
    config.reset_settings_cache()
    return config.get_settings()


def _token_provider(value: str = "tok"):
    async def _provider(audience: str) -> str:  # noqa: ARG001
        return value

    return _provider


def _client(handler, settings) -> FabricClient:
    transport = httpx.MockTransport(handler)
    httpx_client = httpx.AsyncClient(transport=transport)
    return FabricClient(settings, client=httpx_client, token_provider=_token_provider())


def _assignment(principal_id, *, role="Viewer", ptype="User"):
    return {"principal": {"id": principal_id, "type": ptype}, "role": role}


# --------------------------------------------------------------------------- #
# 1. principal_has_read_role — logique PURE.                                   #
# --------------------------------------------------------------------------- #
def test_read_role_direct():
    assignments = [_assignment("p1", role="Viewer")]
    assert principal_has_read_role(assignments, "p1", [])
    assert not principal_has_read_role(assignments, "p2", [])


def test_read_role_via_group():
    assignments = [_assignment("grp-nord", role="Member", ptype="Group")]
    assert principal_has_read_role(assignments, "p1", ["grp-nord"])
    assert not principal_has_read_role(assignments, "p1", ["grp-sud"])


def test_read_role_case_insensitive():
    assignments = [_assignment("P-UP", role="VIEWER")]
    assert principal_has_read_role(assignments, "p-up", [])
    # Groupe casse-insensible aussi.
    assignments2 = [_assignment("GRP", role="Contributor", ptype="Group")]
    assert principal_has_read_role(assignments2, "x", ["grp"])


def test_non_read_role_ignored():
    # Un rôle hors liste lecture (hypothétique) n'accorde rien.
    assignments = [_assignment("p1", role="NoSuchRole")]
    assert not principal_has_read_role(assignments, "p1", [])


def test_all_four_fabric_roles_grant_read():
    for role in ("Admin", "Member", "Contributor", "Viewer"):
        assert principal_has_read_role([_assignment("p", role=role)], "p", [])


def test_malformed_assignment_ignored():
    assignments = [
        "not-a-dict",
        {"role": "Viewer"},  # principal absent
        {"principal": {"type": "User"}, "role": "Viewer"},  # id absent
        {"principal": {"id": "p1"}},  # role absent
        _assignment("p1", role="Viewer"),  # le seul valide
    ]
    assert principal_has_read_role(assignments, "p1", [])
    assert not principal_has_read_role(assignments, "other", [])


def test_role_name_alias_supported():
    # Certaines réponses utilisent roleName plutôt que role.
    assignments = [{"principal": {"id": "p1", "type": "User"}, "roleName": "Viewer"}]
    assert principal_has_read_role(assignments, "p1", [])


# --------------------------------------------------------------------------- #
# 2. can_principal_read — autorisé via (a) roleAssignments.                    #
# --------------------------------------------------------------------------- #
def test_can_read_via_role_assignment_direct(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if "roleAssignments" in str(request.url):
            return httpx.Response(200, json={"value": [_assignment("p1", role="Viewer")]})
        return httpx.Response(404, json={})

    async def go():
        fab = _client(handler, settings)
        try:
            return await can_principal_read("p1", "ws1", "item1", fabric=fab)
        finally:
            await fab.aclose()

    assert run(go()) is True


def test_can_read_via_group_membership(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if "roleAssignments" in str(request.url):
            return httpx.Response(
                200, json={"value": [_assignment("grp1", role="Contributor", ptype="Group")]}
            )
        return httpx.Response(404, json={})

    async def go():
        fab = _client(handler, settings)
        try:
            return await can_principal_read(
                "p1", "ws1", "item1", fabric=fab, principal_group_ids=["grp1"]
            )
        finally:
            await fab.aclose()

    assert run(go()) is True


def test_can_read_denied_when_no_assignment(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if "roleAssignments" in str(request.url):
            return httpx.Response(200, json={"value": [_assignment("someone-else")]})
        # principalAccess OneLake : pas d'accès non plus.
        return httpx.Response(200, json={"hasAccess": False})

    async def go():
        fab = _client(handler, settings)
        try:
            return await can_principal_read("p1", "ws1", "item1", fabric=fab)
        finally:
            await fab.aclose()

    assert run(go()) is False


# --------------------------------------------------------------------------- #
# 3. Élargissement OneLake principalAccess (source b).                         #
# --------------------------------------------------------------------------- #
def test_can_read_via_onelake_effective_access(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if "roleAssignments" in str(request.url):
            # (a) n'accorde PAS.
            return httpx.Response(200, json={"value": [_assignment("autre")]})
        if "principalAccess" in str(request.url):
            # (b) accorde via accès effectif.
            return httpx.Response(200, json={"hasAccess": True})
        return httpx.Response(404, json={})

    async def go():
        fab = _client(handler, settings)
        try:
            return await can_principal_read("p1", "ws1", "item1", fabric=fab)
        finally:
            await fab.aclose()

    assert run(go()) is True


def test_onelake_preview_unavailable_does_not_grant(monkeypatch):
    """Preview OneLake 404 → ignorée ; si (a) ne donne rien → refus (fail-closed)."""
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if "roleAssignments" in str(request.url):
            return httpx.Response(200, json={"value": [_assignment("autre")]})
        return httpx.Response(404, json={})  # principalAccess indisponible

    async def go():
        fab = _client(handler, settings)
        try:
            return await can_principal_read("p1", "ws1", "item1", fabric=fab)
        finally:
            await fab.aclose()

    assert run(go()) is False


def test_onelake_disabled_falls_back_to_role_only(monkeypatch):
    settings = _settings(monkeypatch)
    seen = {"onelake": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if "principalAccess" in str(request.url):
            seen["onelake"] = True
        if "roleAssignments" in str(request.url):
            return httpx.Response(200, json={"value": [_assignment("p1", role="Viewer")]})
        return httpx.Response(404, json={})

    async def go():
        fab = _client(handler, settings)
        try:
            return await can_principal_read(
                "p1", "ws1", "item1", fabric=fab, use_onelake_effective_access=False
            )
        finally:
            await fab.aclose()

    assert run(go()) is True
    assert seen["onelake"] is False  # OneLake jamais appelé


# --------------------------------------------------------------------------- #
# 4. FAIL-CLOSED — non configuré, ids manquants, erreur HTTP.                  #
# --------------------------------------------------------------------------- #
def test_fail_closed_not_configured(monkeypatch):
    settings = _settings(monkeypatch, configured=False)
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={"value": [_assignment("p1", role="Viewer")]})

    async def go():
        fab = _client(handler, settings)
        try:
            return await can_principal_read("p1", "ws1", "item1", fabric=fab)
        finally:
            await fab.aclose()

    assert run(go()) is False
    assert called["n"] == 0  # aucun appel réseau quand non configuré


def test_fail_closed_missing_ids(monkeypatch):
    settings = _settings(monkeypatch)

    async def go(pid, ws, item):
        fab = _client(lambda r: httpx.Response(200, json={"value": []}), settings)
        try:
            return await can_principal_read(pid, ws, item, fabric=fab)
        finally:
            await fab.aclose()

    assert run(go("", "ws", "it")) is False
    assert run(go("p", "", "it")) is False
    assert run(go("p", "ws", "")) is False


def test_fail_closed_on_http_error(monkeypatch):
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        # roleAssignments en erreur ET principalAccess en erreur → refus total.
        return httpx.Response(500, json={"error": "boom"})

    async def go():
        fab = _client(handler, settings)
        try:
            return await can_principal_read("p1", "ws1", "item1", fabric=fab)
        finally:
            await fab.aclose()

    assert run(go()) is False


def test_role_error_but_onelake_grants(monkeypatch):
    """(a) en erreur HTTP mais (b) OneLake accorde → autorisé (b est sûre)."""
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if "roleAssignments" in str(request.url):
            return httpx.Response(403, json={"error": "forbidden"})
        if "principalAccess" in str(request.url):
            return httpx.Response(200, json={"hasAccess": True})
        return httpx.Response(404, json={})

    async def go():
        fab = _client(handler, settings)
        try:
            return await can_principal_read("p1", "ws1", "item1", fabric=fab)
        finally:
            await fab.aclose()

    assert run(go()) is True


# --------------------------------------------------------------------------- #
# 5. authorized_items — sous-ensemble + isolation.                            #
# --------------------------------------------------------------------------- #
def test_authorized_items_workspace_level_grant(monkeypatch):
    settings = _settings(monkeypatch)
    role_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "roleAssignments" in str(request.url):
            role_calls["n"] += 1
            return httpx.Response(200, json={"value": [_assignment("p1", role="Member")]})
        return httpx.Response(404, json={})

    async def go():
        fab = _client(handler, settings)
        try:
            return await authorized_items(
                "p1", "ws1", ["i1", "i2", "i3"], fabric=fab
            )
        finally:
            await fab.aclose()

    allowed = run(go())
    assert allowed == {"i1", "i2", "i3"}
    assert role_calls["n"] == 1  # roleAssignments lus UNE seule fois


def test_authorized_items_isolation_two_principals(monkeypatch):
    settings = _settings(monkeypatch)

    def make_handler(grant_principal):
        def handler(request: httpx.Request) -> httpx.Response:
            if "roleAssignments" in str(request.url):
                return httpx.Response(
                    200, json={"value": [_assignment(grant_principal, role="Viewer")]}
                )
            # principalAccess refuse pour tout le monde ici.
            return httpx.Response(200, json={"hasAccess": False})
        return handler

    async def go(grant_principal, asker):
        fab = _client(make_handler(grant_principal), settings)
        try:
            return await authorized_items(asker, "ws1", ["i1", "i2"], fabric=fab)
        finally:
            await fab.aclose()

    # Le workspace n'accorde qu'à p1.
    set_a = run(go("p1", "p1"))
    set_b = run(go("p1", "p2"))
    assert set_a == {"i1", "i2"}
    assert set_b == set()
    assert set_a != set_b


def test_authorized_items_onelake_per_item(monkeypatch):
    """(a) n'accorde pas, mais (b) OneLake accorde pour i2 seulement."""
    settings = _settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "roleAssignments" in url:
            return httpx.Response(200, json={"value": [_assignment("autre")]})
        if "principalAccess" in url:
            if "/artifacts/i2/" in url:
                return httpx.Response(200, json={"hasAccess": True})
            return httpx.Response(200, json={"hasAccess": False})
        return httpx.Response(404, json={})

    async def go():
        fab = _client(handler, settings)
        try:
            return await authorized_items("p1", "ws1", ["i1", "i2"], fabric=fab)
        finally:
            await fab.aclose()

    assert run(go()) == {"i2"}


def test_authorized_items_not_configured_empty(monkeypatch):
    settings = _settings(monkeypatch, configured=False)

    async def go():
        fab = _client(lambda r: httpx.Response(200, json={"value": []}), settings)
        try:
            return await authorized_items("p1", "ws1", ["i1"], fabric=fab)
        finally:
            await fab.aclose()

    assert run(go()) == set()


# --------------------------------------------------------------------------- #
# 6. _onelake_access_grants_read — interprétation défensive.                   #
# --------------------------------------------------------------------------- #
def test_onelake_grants_read_forms():
    assert _onelake_access_grants_read({"hasAccess": True})
    assert _onelake_access_grants_read({"actions": ["Read"]})
    assert _onelake_access_grants_read({"accessActions": ["*"]})
    assert _onelake_access_grants_read({"effectivePermissions": ["Viewer"]})
    assert _onelake_access_grants_read({"roles": ["Contributor"]})


def test_onelake_grants_read_denies_unknown_or_empty():
    assert not _onelake_access_grants_read({})
    assert not _onelake_access_grants_read({"hasAccess": False})
    assert not _onelake_access_grants_read({"actions": ["write"]})
    assert not _onelake_access_grants_read({"effectivePermissions": ["NoRead"]})
    assert not _onelake_access_grants_read("not-a-dict")  # type: ignore[arg-type]
