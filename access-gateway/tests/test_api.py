"""Tests d'API bout-en-bout (TestClient FastAPI, amont Onyx moqué).

Couvre : santé, refus sans identité (401), deny-by-default (403), forçage du
filtre Document Set, introspection, et non-élargissement de périmètre.
"""
from __future__ import annotations

from conftest import GROUP_NORD, GROUP_SUD, claims


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "onix-access-gateway"
    assert body["group_source"] == "claims"
    assert body["groups_mapped"] == 2


def test_no_identity_returns_401(client):
    r = client.post("/v1/chat/send-message", json={"message": "bonjour"})
    assert r.status_code == 401


def test_introspection_lists_authorized_sets(client):
    r = client.get(
        "/v1/authorized-document-sets",
        headers={"X-OIDC-Claims": claims(oid="u1", groups=[GROUP_NORD])},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["authorized_document_sets"] == ["clients-nord"]
    assert body["group_source"] == "claims"
    assert body["upn"] == "nord@contoso.fr"


def test_user_without_mapped_group_is_denied(client):
    r = client.post(
        "/v1/chat/send-message",
        json={"message": "x"},
        headers={"X-OIDC-Claims": claims(oid="u2", groups=["groupe-inconnu"])},
    )
    assert r.status_code == 403


def test_send_message_forces_document_set_filter(client):
    r = client.post(
        "/v1/chat/send-message",
        json={"message": "Résume le client ABC"},
        headers={"X-OIDC-Claims": claims(oid="nord", groups=[GROUP_NORD])},
    )
    assert r.status_code == 200
    # Le payload RÉELLEMENT relayé à Onyx porte le filtre forcé.
    relayed = client.last_upstream["payload"]
    assert relayed["retrieval_options"]["filters"]["document_set"] == ["clients-nord"]
    # La cible amont est bien Onyx.
    assert client.last_upstream["url"] == "http://onyx.test:8080/chat/send-message"


def test_user_cannot_widen_scope(client):
    # L'utilisateur Nord tente d'interroger aussi 'clients-sud' -> filtré.
    r = client.post(
        "/v1/chat/send-message",
        json={
            "message": "x",
            "retrieval_options": {"filters": {"document_set": ["clients-nord", "clients-sud"]}},
        },
        headers={"X-OIDC-Claims": claims(oid="nord", groups=[GROUP_NORD])},
    )
    assert r.status_code == 200
    relayed = client.last_upstream["payload"]
    assert relayed["retrieval_options"]["filters"]["document_set"] == ["clients-nord"]


def test_user_requesting_only_foreign_set_is_denied(client):
    r = client.post(
        "/v1/chat/send-message",
        json={"message": "x", "retrieval_options": {"filters": {"document_set": ["clients-sud"]}}},
        headers={"X-OIDC-Claims": claims(oid="nord", groups=[GROUP_NORD])},
    )
    assert r.status_code == 403


def test_two_commercials_are_isolated(client):
    """Cœur du cas réel : Nord et Sud ne voient pas le périmètre l'un de l'autre."""
    r_sud = client.get(
        "/v1/authorized-document-sets",
        headers={"X-OIDC-Claims": claims(oid="sud", upn="sud@contoso.fr", groups=[GROUP_SUD])},
    )
    assert r_sud.json()["authorized_document_sets"] == ["clients-sud"]
    r_nord = client.get(
        "/v1/authorized-document-sets",
        headers={"X-OIDC-Claims": claims(oid="nord", groups=[GROUP_NORD])},
    )
    assert r_nord.json()["authorized_document_sets"] == ["clients-nord"]


def test_invalid_json_body_returns_400(client):
    r = client.post(
        "/v1/chat/send-message",
        content=b"{not-json",
        headers={
            "X-OIDC-Claims": claims(oid="nord", groups=[GROUP_NORD]),
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# Durcissement : fail-closed & cas de groupes (sans groupe / multi-groupes).    #
# --------------------------------------------------------------------------- #
def test_user_with_empty_groups_is_denied(client):
    """Utilisateur authentifié mais SANS aucun groupe (liste vide) -> deny (403).

    C'est le fail-closed : pas de groupe => pas de Document Set => aucune recherche.
    """
    r = client.post(
        "/v1/chat/send-message",
        json={"message": "x"},
        headers={"X-OIDC-Claims": claims(oid="ghost", groups=[])},
    )
    assert r.status_code == 403
    # Et l'introspection confirme un périmètre vide (pas de fuite par défaut).
    intro = client.get(
        "/v1/authorized-document-sets",
        headers={"X-OIDC-Claims": claims(oid="ghost", groups=[])},
    )
    assert intro.status_code == 200
    assert intro.json()["authorized_document_sets"] == []


def test_user_without_groups_claim_at_all_is_denied(client):
    """Aucun claim 'groups' du tout (None) -> en mode 'claims', liste vide -> deny."""
    r = client.post(
        "/v1/chat/send-message",
        json={"message": "x"},
        headers={"X-OIDC-Claims": claims(oid="nogroupsclaim", groups=None)},
    )
    assert r.status_code == 403


def test_multi_group_user_gets_union_only(client):
    """Multi-groupes : l'utilisateur appartient à Nord ET Sud.

    Le périmètre autorisé est EXACTEMENT l'union {clients-nord, clients-sud} —
    ni plus (pas de set tiers), ni moins.
    """
    hdr = {"X-OIDC-Claims": claims(oid="dir", upn="dir@contoso.fr", groups=[GROUP_NORD, GROUP_SUD])}
    intro = client.get("/v1/authorized-document-sets", headers=hdr)
    assert intro.status_code == 200
    assert intro.json()["authorized_document_sets"] == ["clients-nord", "clients-sud"]
    assert intro.json()["group_count"] == 2

    # Sans filtre demandé : on force EXACTEMENT l'union autorisée.
    r = client.post("/v1/chat/send-message", json={"message": "x"}, headers=hdr)
    assert r.status_code == 200
    relayed = client.last_upstream["payload"]
    assert relayed["retrieval_options"]["filters"]["document_set"] == [
        "clients-nord",
        "clients-sud",
    ]


def test_multi_group_user_cannot_reach_unmapped_set(client):
    """Multi-groupes Nord+Sud : un set NON cartographié reste hors de portée."""
    hdr = {"X-OIDC-Claims": claims(oid="dir", groups=[GROUP_NORD, GROUP_SUD])}
    r = client.post(
        "/v1/chat/send-message",
        json={
            "message": "x",
            "retrieval_options": {
                "filters": {"document_set": ["clients-nord", "clients-sud", "secret-rh"]}
            },
        },
        headers=hdr,
    )
    assert r.status_code == 200
    relayed = client.last_upstream["payload"]
    # 'secret-rh' (non mappé) est retiré ; seule l'union autorisée subsiste.
    assert relayed["retrieval_options"]["filters"]["document_set"] == [
        "clients-nord",
        "clients-sud",
    ]


def test_partial_overlap_multi_group_intersects(client):
    """Multi-groupes mais requête ne ciblant qu'un sous-ensemble autorisé : OK,
    bornée à l'intersection (Nord+Sud autorisés, requête = Sud uniquement)."""
    hdr = {"X-OIDC-Claims": claims(oid="dir", groups=[GROUP_NORD, GROUP_SUD])}
    r = client.post(
        "/v1/chat/send-message",
        json={"message": "x", "retrieval_options": {"filters": {"document_set": ["clients-sud"]}}},
        headers=hdr,
    )
    assert r.status_code == 200
    relayed = client.last_upstream["payload"]
    assert relayed["retrieval_options"]["filters"]["document_set"] == ["clients-sud"]
