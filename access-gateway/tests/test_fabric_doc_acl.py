"""Tests M3 — câblage de l'ACL **Fabric** par-document au filtre de citations.

Vuln corrigée : avant M3, `fabric_acl.py` (décision de lecture Fabric, fail-closed)
existait et était testé en isolation, MAIS n'était JAMAIS branché comme source
`DocACL` du filtre de citations (`doc_acl.filter_citations`). Conséquence : un
document Fabric HORS du périmètre de l'appelant (aucun rôle de lecture sur son
workspace, ou doc dont l'ACL est indéterminable) **fuitait en citation**.

Ce fichier prouve, sur le chemin RÉEL (`filter_citations` + l'adaptateur
`FabricDocACL`), que :
  * un doc Fabric dont le workspace n'accorde PAS de rôle de lecture à l'appelant
    est RETIRÉ des citations (deny-by-default) ;
  * un doc Fabric NON mappé (item/ACL indéterminable) est RETIRÉ (fail-closed) ;
  * un doc Fabric dont le workspace accorde un rôle de lecture (direct ou via
    groupe) à l'appelant est CONSERVÉ ;
  * un item hors périmètre GOLD est RETIRÉ même si un rôle existe.

AUCUN appel réseau réel : transport httpx MOQUÉ + jeton injecté (comme
`test_fabric_acl.py`).
"""
from __future__ import annotations

import httpx

import app.config as config
from app.doc_acl import filter_citations
from app.fabric_client import FabricClient
from app.fabric_doc_acl import FabricDocACL, build_fabric_acl
from conftest import run


GOLD_WS = "goldws"


class _Principal:
    """Surface minimale d'un `identity.Principal` pour le filtre."""

    def __init__(self, user_id, upn=None, group_ids=None):
        self.user_id = user_id
        self.upn = upn
        self.group_ids = group_ids or []


def _settings(monkeypatch):
    """Fabric + gold RÉELS configurés. Le lakehouse gold est ('lh1'/'goldlake')."""
    monkeypatch.setenv("GATEWAY_GRAPH_TENANT_ID", "tid")
    monkeypatch.setenv("GATEWAY_GRAPH_CLIENT_ID", "cid")
    monkeypatch.setenv("GATEWAY_GRAPH_CLIENT_SECRET", "sek")
    monkeypatch.setenv("GATEWAY_FABRIC_GOLD_WORKSPACE_ID", GOLD_WS)
    monkeypatch.setenv("GATEWAY_FABRIC_GOLD_LAKEHOUSE_ID", "lh1")
    monkeypatch.setenv("GATEWAY_FABRIC_GOLD_LAKEHOUSE_NAME", "goldlake")
    config.reset_settings_cache()
    return config.get_settings()


def _token_provider(value="tok"):
    async def _provider(audience):  # noqa: ARG001
        return value

    return _provider


def _client(handler, settings):
    transport = httpx.MockTransport(handler)
    httpx_client = httpx.AsyncClient(transport=transport)
    return FabricClient(settings, client=httpx_client, token_provider=_token_provider())


def _assignment(principal_id, *, role="Viewer", ptype="User"):
    return {"principal": {"id": principal_id, "type": ptype}, "role": role}


# Mapping doc → item Fabric. 'doc-autorise' et 'doc-interdit' pointent le lakehouse
# gold (lh1) ; 'doc-hors-gold' pointe un item hors gold ; 'doc-non-mappe' n'est PAS
# dans le mapping (ACL indéterminable).
_MAPPING = {
    "_comment": "mapping de test",
    "doc-autorise": {"workspace_id": GOLD_WS, "item_id": "lh1", "item_type": "Lakehouse"},
    "doc-interdit": {"workspace_id": GOLD_WS, "item_id": "lh1", "item_type": "Lakehouse"},
    "doc-hors-gold": {"workspace_id": GOLD_WS, "item_id": "autre", "item_type": "Lakehouse"},
}


def _build_acl(monkeypatch, *, grant_to):
    """Construit un `FabricDocACL` réel : roleAssignments accordent un rôle de
    lecture à `grant_to` (id direct OU groupe). Tout le reste est refusé."""
    settings = _settings(monkeypatch)

    def handler(request):
        if "roleAssignments" in str(request.url):
            return httpx.Response(200, json={"value": [_assignment(grant_to, role="Viewer")]})
        # principalAccess OneLake : aucun accès supplémentaire.
        return httpx.Response(404, json={})

    async def go():
        fab = _client(handler, settings)
        try:
            return await build_fabric_acl(fab, _MAPPING)
        finally:
            await fab.aclose()

    return run(go())


def _body_with_citations(*doc_ids):
    return {
        "answer": "Réponse citant des documents.",
        "citations": [{"document_id": d, "snippet": f"extrait {d}"} for d in doc_ids],
        "top_documents": [{"document_id": d} for d in doc_ids],
    }


# --------------------------------------------------------------------------- #
# 1. FUITE reproduite AVANT le câblage : sans ACL Fabric, la citation interdite #
#    passe ; AVEC l'ACL Fabric câblée au filtre, elle est retirée.             #
# --------------------------------------------------------------------------- #
def test_citation_fabric_hors_perimetre_fuit_sans_acl(monkeypatch):
    """Sans aucune ACL (acl=None), le filtre est un no-op : la citation interdite
    FUIT (état pré-M3, où aucune source Fabric n'était câblée)."""
    _settings(monkeypatch)
    body = _body_with_citations("doc-autorise", "doc-interdit")
    # Reproduction du chemin pré-fix : filtre désactivé/inerte → fuite.
    out, dropped = filter_citations(body, _Principal("p1"), acl=None, enabled=False)  # type: ignore[arg-type]
    cited = {c["document_id"] for c in out["citations"]}
    assert "doc-interdit" in cited  # ← LA FUITE : doc hors-périmètre cité.
    assert dropped == []


def test_citation_fabric_hors_perimetre_retiree_avec_acl(monkeypatch):
    """AVEC l'ACL Fabric câblée au filtre : le workspace n'accorde la lecture qu'à
    'p1'. L'appelant 'p2' ne doit PAS voir 'doc-interdit' (ni en citation, ni en
    document)."""
    # Le rôle de lecture est accordé à p1 uniquement.
    acl = _build_acl(monkeypatch, grant_to="p1")
    body = _body_with_citations("doc-autorise", "doc-interdit")

    # p2 n'a aucun rôle → AUCUN doc autorisé.
    out, dropped = filter_citations(body, _Principal("p2"), acl, enabled=True)
    cited = {c["document_id"] for c in out["citations"]}
    assert "doc-interdit" not in cited
    assert "doc-autorise" not in cited  # p2 n'a rien
    dropped_ids = {d["doc_id"] for d in dropped}
    assert "doc-interdit" in dropped_ids


def test_citation_fabric_autorisee_conservee(monkeypatch):
    """p1 a un rôle de lecture sur le workspace gold → 'doc-autorise' (item gold)
    est CONSERVÉ ; 'doc-hors-gold' est retiré (gold-only) ; 'doc-non-mappe' est
    retiré (ACL indéterminable, deny-by-default)."""
    acl = _build_acl(monkeypatch, grant_to="p1")
    body = _body_with_citations("doc-autorise", "doc-hors-gold", "doc-non-mappe")

    out, _dropped = filter_citations(body, _Principal("p1"), acl, enabled=True)
    cited = {c["document_id"] for c in out["citations"]}
    assert "doc-autorise" in cited           # gold + rôle → visible
    assert "doc-hors-gold" not in cited       # hors gold → refus
    assert "doc-non-mappe" not in cited       # non mappé → indéterminable → refus


def test_citation_fabric_via_groupe_conservee(monkeypatch):
    """Le rôle de lecture est accordé à un GROUPE Entra ; un membre de ce groupe
    voit la citation, un non-membre ne la voit pas (isolation)."""
    acl = _build_acl(monkeypatch, grant_to="grp-nord")
    body = _body_with_citations("doc-autorise")

    # Membre du groupe → visible.
    out_ok, _ = filter_citations(
        body, _Principal("alice", group_ids=["grp-nord"]), acl, enabled=True
    )
    assert {c["document_id"] for c in out_ok["citations"]} == {"doc-autorise"}

    # Non-membre → retiré.
    out_ko, _ = filter_citations(
        body, _Principal("bob", group_ids=["grp-sud"]), acl, enabled=True
    )
    assert out_ko["citations"] == []


def test_fabric_doc_acl_deny_quand_non_mappe(monkeypatch):
    """`is_authorized` direct : un doc absent du mapping est refusé (deny-by-default),
    pour tout principal."""
    acl = _build_acl(monkeypatch, grant_to="p1")
    assert acl.is_authorized("doc-autorise", _Principal("p1")) is True
    assert acl.is_authorized("doc-non-mappe", _Principal("p1")) is False
    assert acl.is_authorized("", _Principal("p1")) is False


def test_build_fabric_acl_vide_si_non_configure(monkeypatch):
    """Fabric non configuré → ACL construite VIDE (donc deny-by-default total :
    aucune citation Fabric autorisée). Aucun crash."""
    for var in (
        "GATEWAY_GRAPH_TENANT_ID",
        "GATEWAY_GRAPH_CLIENT_ID",
        "GATEWAY_GRAPH_CLIENT_SECRET",
        "GATEWAY_FABRIC_TENANT_ID",
        "GATEWAY_FABRIC_CLIENT_ID",
        "GATEWAY_FABRIC_CLIENT_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GATEWAY_FABRIC_GOLD_WORKSPACE_ID", GOLD_WS)
    monkeypatch.setenv("GATEWAY_FABRIC_GOLD_LAKEHOUSE_ID", "lh1")
    config.reset_settings_cache()
    settings = config.get_settings()

    called = {"n": 0}

    def handler(request):
        called["n"] += 1
        return httpx.Response(200, json={"value": [_assignment("p1", role="Admin")]})

    async def go():
        fab = _client(handler, settings)
        try:
            return await build_fabric_acl(fab, _MAPPING)
        finally:
            await fab.aclose()

    acl = run(go())
    assert isinstance(acl, FabricDocACL)
    assert len(acl) == 0           # rien de résolu
    assert called["n"] == 0        # aucun appel réseau quand non configuré
    assert acl.is_authorized("doc-autorise", _Principal("p1")) is False
