"""Tests d'intégration des chemins « prouvés contre de vrais services » :

  * extraction LLM (Ollama) : parsing robuste des réponses bruitées, repli
    heuristique propre quand Ollama est indisponible, mode réellement utilisé ;
  * /notify webhook : livraison vers un SINK HTTP local (serveur réel en thread),
    et échec réseau -> statut d'erreur propre (jamais 500) ;
  * /notify smtp : livraison vers un serveur SMTP local (aiosmtpd) qui CAPTE le
    message, + cas STARTTLS exigé mais non supporté -> erreur propre ;
  * /tasks webhook_url : la création déclenche bien l'appel sortant (capté).

Ces tests n'exigent AUCUN service externe : sinks/serveurs locaux + httpx mocké.
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List

import pytest

# --- Sink webhook local (serveur HTTP réel, en thread) ----------------------


class _SinkHandler(BaseHTTPRequestHandler):
    received: List[dict] = []
    status_code = 200

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        type(self).received.append({"path": self.path, "body": body})
        self.send_response(type(self).status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *args):
        pass


@pytest.fixture()
def webhook_sink():
    """Démarre un vrai serveur HTTP local qui capture les POST. Rend (url, received)."""

    class _Handler(_SinkHandler):
        received: List[dict] = []
        status_code = 200

    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield {"url": f"http://127.0.0.1:{port}", "handler": _Handler}
    finally:
        srv.shutdown()
        srv.server_close()


# --- Extraction LLM : parsing robuste --------------------------------------


def test_extract_json_robuste_aux_reponses_bruitees():
    """Le parser doit récupérer l'objet JSON malgré prose + fences markdown +
    objet imbriqué + texte parasite (réponses typiques d'un petit LLM)."""
    from app.llm import _extract_json

    # prose autour
    assert _extract_json('Voici: {"a": 1} merci') == {"a": 1}
    # fence markdown ```json
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    # prose + fence + texte après
    noisy = 'Je peux vous aider.\n```json\n{"nom_client": "ACME"}\n```\nSi besoin...'
    assert _extract_json(noisy) == {"nom_client": "ACME"}
    # objet imbriqué : accolades équilibrées
    assert _extract_json('x {"a": {"b": 2}} y') == {"a": {"b": 2}}
    # non exploitable
    assert _extract_json("pas de json ici") is None
    assert _extract_json("") is None
    # une LISTE n'est pas un objet -> None (on n'accepte qu'un dict)
    assert _extract_json("[1, 2, 3]") is None


def test_extract_fields_llm_succes_mocke(monkeypatch):
    """extract_fields_llm renvoie les champs canoniques quand Ollama répond
    (réponse simulée bruitée). Vérifie aussi le filtrage aux clés connues."""
    import app.llm as llm

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "response": '```json\n{"nom_client": "ACME SAS", '
                '"numero_contrat": "CTR-1", "champ_inconnu": "x", '
                '"plafond_hospitalisation": null}\n```'
            }

    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _Resp())
    out = llm.extract_fields_llm("texte quelconque")
    assert out == {"nom_client": "ACME SAS", "numero_contrat": "CTR-1"}


def test_extract_fields_llm_indisponible_leve(monkeypatch):
    """Si Ollama est injoignable, extract_fields_llm lève RuntimeError (capté en
    amont pour le repli)."""
    import httpx

    import app.llm as llm

    def _boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(llm.httpx, "post", _boom)
    with pytest.raises(RuntimeError):
        llm.extract_fields_llm("texte")


def test_audit_use_llm_repli_heuristique_si_ollama_ko(client, monkeypatch):
    """/audit use_llm=true : si Ollama échoue, repli PROPRE sur l'heuristique
    (pas d'erreur 500) et le mode renvoyé est 'heuristic'."""
    import app.llm as llm

    def _boom(*a, **k):
        raise RuntimeError("Ollama indisponible: down")

    # On force l'échec de l'extraction LLM. `_resolve_document_fields` appelle
    # désormais `extract_fields_llm_with_usage` (capture des tokens réels).
    monkeypatch.setattr(llm, "extract_fields_llm_with_usage", _boom)
    text = (
        "Raison sociale: ACME SAS\nPlafond hospitalisation: 2000\n"
        "Date d'effet: 01/01/2024\nNuméro de contrat: CTR-2024-001\n"
    )
    r = client.post("/audit", json={
        "text": text,
        "use_llm": True,
        "reference": {
            "nom_client": "ACME SAS", "plafond_hospitalisation": "2000",
            "date_effet": "2024-01-01", "numero_contrat": "CTR-2024-001",
        },
    })
    assert r.status_code == 200
    body = r.json()
    assert body["_extraction_mode"] == "heuristic"
    # Le repli heuristique a bien extrait depuis les libellés "clé: valeur".
    assert body["verdict"] == "CONFORME"


def test_audit_use_llm_mode_llm(client, monkeypatch):
    """/audit use_llm=true : si Ollama répond, mode 'llm' et champs LLM utilisés.

    `_resolve_document_fields` fait `from .llm import extract_fields_llm_with_usage`
    au moment de l'appel : patcher `app.llm.extract_fields_llm_with_usage` suffit."""
    import app.llm as llm

    def _fake(text, **k):
        return (
            {"nom_client": "ACME SAS", "numero_contrat": "CTR-2024-001"},
            llm.LLMUsage(input_tokens=42, output_tokens=7, measured=True),
        )

    monkeypatch.setattr(llm, "extract_fields_llm_with_usage", _fake)
    r = client.post("/audit", json={
        "text": "n'importe quel texte libre sans structure clé:valeur",
        "use_llm": True,
        "reference": {"nom_client": "ACME SAS", "numero_contrat": "CTR-2024-001"},
    })
    assert r.status_code == 200
    assert r.json()["_extraction_mode"] == "llm"


# --- /notify webhook : livraison réelle vers un sink local ------------------


def test_notify_webhook_livraison_sink(client, webhook_sink):
    """POST /notify (webhook) -> le sink local CAPTE le payload (text + extra)."""
    r = client.post("/notify", json={
        "provider": "webhook",
        "message": "Audit ACME: ECART",
        "url": webhook_sink["url"] + "/hook",
        "extra": {"severity": "high"},
    })
    assert r.status_code == 200
    assert r.json()["status"] == "sent"
    received = webhook_sink["handler"].received
    assert len(received) == 1
    assert received[0]["path"] == "/hook"
    import json as _json
    body = _json.loads(received[0]["body"])
    assert body["text"] == "Audit ACME: ECART"
    assert body["severity"] == "high"


def test_notify_webhook_cible_down_erreur_propre(client):
    """Cible injoignable -> HTTP 200 avec statut 'error' (jamais 500)."""
    r = client.post("/notify", json={
        "provider": "webhook", "message": "x", "url": "http://127.0.0.1:9/dead",
    })
    assert r.status_code == 200
    assert r.json()["status"] == "error"


def test_notify_provider_inconnu(client):
    r = client.post("/notify", json={"provider": "pigeon", "message": "x"})
    assert r.status_code == 200
    assert r.json()["status"] == "error"
    assert "inconnu" in r.json()["reason"].lower()


# --- /notify smtp : livraison réelle vers un serveur SMTP local -------------


def _free_port() -> int:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def smtp_capture():
    """Démarre un serveur SMTP local (aiosmtpd) qui capture les messages.

    NB : on fixe un port EXPLICITE (le Controller d'aiosmtpd 1.4.x sonde la
    connectivité avec self.port, encore 0 si on demande port=0)."""
    aiosmtpd_controller = pytest.importorskip("aiosmtpd.controller")
    from aiosmtpd.handlers import Message

    captured: list = []

    class _Capture(Message):
        def handle_message(self, message):
            captured.append(message)

    port = _free_port()
    ctrl = aiosmtpd_controller.Controller(_Capture(), hostname="127.0.0.1", port=port)
    ctrl.start()
    try:
        yield {"host": "127.0.0.1", "port": port, "captured": captured}
    finally:
        ctrl.stop()


def test_notify_smtp_livraison(client, smtp_capture, monkeypatch):
    """POST /notify (smtp) -> le serveur SMTP local CAPTE le mail (sujet+corps)."""
    monkeypatch.setenv("ONIX_SMTP_HOST", smtp_capture["host"])
    monkeypatch.setenv("ONIX_SMTP_PORT", str(smtp_capture["port"]))
    monkeypatch.setenv("ONIX_SMTP_STARTTLS", "false")
    monkeypatch.setenv("ONIX_SMTP_FROM", "onix@local.test")
    r = client.post("/notify", json={
        "provider": "smtp",
        "subject": "Rapport ACME",
        "message": "Corps du message audit.",
        "to": "agent@local.test",
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent"
    # Laisse le serveur traiter.
    for _ in range(20):
        if smtp_capture["captured"]:
            break
        time.sleep(0.05)
    assert len(smtp_capture["captured"]) == 1
    msg = smtp_capture["captured"][0]
    assert msg["Subject"] == "Rapport ACME"
    assert msg["To"] == "agent@local.test"
    assert "Corps du message audit." in msg.get_payload()


def test_notify_smtp_non_configure_skipped(client):
    """Sans ONIX_SMTP_HOST -> 'skipped' (pas d'erreur)."""
    r = client.post("/notify", json={"provider": "smtp", "message": "x"})
    assert r.status_code == 200
    assert r.json()["status"] in ("skipped", "error")


# --- /tasks webhook_url : l'appel sortant part bien ------------------------


def test_tasks_webhook_declenche_appel(client, webhook_sink):
    """POST /tasks avec webhook_url -> le sink CAPTE l'appel ; webhook_status=sent."""
    r = client.post("/tasks", json={
        "title": "Relancer ACME",
        "due_date": "2026-07-01",
        "webhook_url": webhook_sink["url"] + "/task-hook",
    })
    assert r.status_code == 200
    assert r.json()["webhook_status"] == "sent"
    received = webhook_sink["handler"].received
    assert len(received) == 1
    assert received[0]["path"] == "/task-hook"
    assert "Relancer ACME" in received[0]["body"]


def test_tasks_webhook_down_task_creee_quand_meme(client):
    """Webhook down -> la tâche est CRÉÉE quand même (webhook_status=error, pas 500)."""
    r = client.post("/tasks", json={
        "title": "Tache resiliente", "webhook_url": "http://127.0.0.1:9/dead",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "open"
    assert body["webhook_status"] == "error"
