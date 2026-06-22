"""Tests de durcissement FAIL-CLOSED de la passerelle (API bout-en-bout).

Principe : si la passerelle ne PEUT PAS établir l'appartenance aux groupes de
l'appelant (claims tronqués par overage + repli Graph indisponible), elle REFUSE
(502) — jamais de passage « ouvert » sans périmètre. On vérifie aussi qu'aucune
identité en clair ne fuit dans le journal d'audit.
"""
from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from conftest import GROUP_NORD, claims


def _build_client(
    monkeypatch, tmp_path, *, group_source, with_graph, proxy_secret=None
):
    """Construit un TestClient avec un env choisi (mode/Graph), amont Onyx moqué.

    Par défaut (``proxy_secret=None``) l'override dev anti-spoof est ACTIVÉ pour ne
    pas exiger d'en-tête de preuve proxy : les tests historiques de fail-closed
    (overage/identité) restent inchangés. Si ``proxy_secret`` est fourni, on exerce
    le chemin PROD (secret partagé requis) pour prouver le rejet d'un X-OIDC-Claims
    forgé sans preuve de transit proxy (M7)."""
    mapping = tmp_path / "group_map.json"
    mapping.write_text(
        json.dumps({"version": 1, "groups": {GROUP_NORD: {"document_sets": ["clients-nord"]}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GATEWAY_ONYX_BASE_URL", "http://onyx.test:8080")
    monkeypatch.setenv("GATEWAY_ONYX_API_KEY", "k")
    monkeypatch.setenv("GATEWAY_GROUP_SOURCE", group_source)
    monkeypatch.setenv("GATEWAY_MAPPING_PATH", str(mapping))
    monkeypatch.setenv("GATEWAY_DENY_IF_NO_MATCH", "true")
    monkeypatch.setenv("GATEWAY_GROUP_CACHE_TTL", "0")
    monkeypatch.setenv("GATEWAY_AUDIT_SALT", "failclosed-salt")
    if proxy_secret is None:
        # Override dev : on tolère l'en-tête sans preuve proxy (cf. anti-spoof M7).
        monkeypatch.setenv("GATEWAY_ALLOW_UNAUTHENTICATED_HEADER", "true")
        monkeypatch.delenv("GATEWAY_PROXY_SHARED_SECRET", raising=False)
    else:
        # Chemin PROD : secret partagé requis, override dev désactivé.
        monkeypatch.setenv("GATEWAY_PROXY_SHARED_SECRET", proxy_secret)
        monkeypatch.setenv("GATEWAY_ALLOW_UNAUTHENTICATED_HEADER", "false")
    if with_graph:
        monkeypatch.setenv("GATEWAY_GRAPH_TENANT_ID", "tid")
        monkeypatch.setenv("GATEWAY_GRAPH_CLIENT_ID", "cid")
        monkeypatch.setenv("GATEWAY_GRAPH_CLIENT_SECRET", "sek")
    else:
        for k in ("GATEWAY_GRAPH_TENANT_ID", "GATEWAY_GRAPH_CLIENT_ID", "GATEWAY_GRAPH_CLIENT_SECRET"):
            monkeypatch.delenv(k, raising=False)

    import app.config as config
    import app.main as main

    importlib.reload(config)
    importlib.reload(main)
    return main


def test_overage_without_graph_fails_closed_502(monkeypatch, tmp_path):
    """Claims en overage + Graph NON configuré (mode auto) => 502 (fail-closed),
    et surtout PAS 200 : on ne laisse jamais passer sans périmètre résolu."""
    main = _build_client(monkeypatch, tmp_path, group_source="auto", with_graph=False)
    with TestClient(main.app) as c:
        r = c.post(
            "/v1/chat/send-message",
            json={"message": "x"},
            headers={"X-OIDC-Claims": claims(oid="big", overage=True)},
        )
    assert r.status_code == 502  # dépendance d'autorisation indisponible -> refus


def test_overage_with_graph_error_fails_closed(monkeypatch, tmp_path):
    """Overage + Graph configuré mais en erreur (token KO) => 502, jamais 200."""
    main = _build_client(monkeypatch, tmp_path, group_source="auto", with_graph=True)

    async def _boom_post(url, data=None, json=None, headers=None, **kwargs):  # noqa: A002
        # Échec d'acquisition du jeton Graph (login.microsoftonline.com).
        class _R:
            status_code = 401

            def json(self):
                return {"error": "invalid_client"}

        return _R()

    with TestClient(main.app) as c:
        # Le client httpx partagé sert AUSSI à appeler Graph -> on force l'échec token.
        monkeypatch.setattr(main.app.state.http, "post", _boom_post)
        r = c.post(
            "/v1/chat/send-message",
            json={"message": "x"},
            headers={"X-OIDC-Claims": claims(oid="big", overage=True)},
        )
    assert r.status_code == 502


def test_no_identity_header_denies_and_no_plaintext_in_audit(monkeypatch, tmp_path, caplog):
    """Pas d'en-tête d'identité => 401, et le journal d'audit ne contient pas d'UPN."""
    import logging

    main = _build_client(monkeypatch, tmp_path, group_source="claims", with_graph=False)
    with TestClient(main.app) as c:
        with caplog.at_level(logging.WARNING, logger="onix.gateway.audit"):
            r = c.post("/v1/chat/send-message", json={"message": "secret question"})
    assert r.status_code == 401
    # Aucune donnée métier ni UPN dans les logs d'audit.
    for rec in caplog.records:
        msg = rec.getMessage()
        assert "secret question" not in msg
        assert "contoso.fr" not in msg


# --------------------------------------------------------------------------- #
# Anti-spoof X-OIDC-Claims bout-en-bout (M7) : preuve de transit proxy requise #
# --------------------------------------------------------------------------- #
def test_forged_claims_without_proxy_secret_denied_401(monkeypatch, tmp_path):
    """VULN M7 : un client forgeant X-OIDC-Claims (groupes admin) SANS transiter par
    le proxy de confiance (donc sans X-OIDC-Proxy-Secret) doit être REFUSÉ (401),
    jamais identifié/autorisé. C'est l'anti-usurpation RBAC."""
    main = _build_client(
        monkeypatch, tmp_path, group_source="claims", with_graph=False,
        proxy_secret="secret-proxy-attendu",
    )
    with TestClient(main.app) as c:
        r = c.post(
            "/v1/chat/send-message",
            json={"message": "x"},
            headers={"X-OIDC-Claims": claims(oid="attacker", groups=[GROUP_NORD])},
        )
    assert r.status_code == 401  # preuve proxy absente -> refus dur


def test_forged_claims_with_wrong_proxy_secret_denied_401(monkeypatch, tmp_path):
    """Même attaque mais avec un X-OIDC-Proxy-Secret ERRONÉ => 401."""
    main = _build_client(
        monkeypatch, tmp_path, group_source="claims", with_graph=False,
        proxy_secret="secret-proxy-attendu",
    )
    with TestClient(main.app) as c:
        r = c.post(
            "/v1/chat/send-message",
            json={"message": "x"},
            headers={
                "X-OIDC-Claims": claims(oid="attacker", groups=[GROUP_NORD]),
                "X-OIDC-Proxy-Secret": "mauvais-secret",
            },
        )
    assert r.status_code == 401


def test_claims_with_valid_proxy_secret_pass(monkeypatch, tmp_path):
    """Avec le BON X-OIDC-Proxy-Secret (transit proxy prouvé), la requête passe :
    on n'a pas cassé le chemin légitime. L'amont Onyx est moqué (aucun réseau)."""
    main = _build_client(
        monkeypatch, tmp_path, group_source="claims", with_graph=False,
        proxy_secret="secret-proxy-attendu",
    )

    class _R:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = "{}"

        def json(self):
            return {"answer": "ok"}

    async def _fake_post(url, json=None, headers=None, **kwargs):  # noqa: A002
        return _R()

    with TestClient(main.app) as c:
        monkeypatch.setattr(main.app.state.http, "post", _fake_post)
        r = c.post(
            "/v1/chat/send-message",
            json={"message": "x"},
            headers={
                "X-OIDC-Claims": claims(oid="legit", groups=[GROUP_NORD]),
                "X-OIDC-Proxy-Secret": "secret-proxy-attendu",
            },
        )
    assert r.status_code == 200
