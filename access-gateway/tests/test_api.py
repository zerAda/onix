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
