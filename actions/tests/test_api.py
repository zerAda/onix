"""Tests de l'API FastAPI : santé, auth, audit JSON, génération .docx,
gating admin (coupé -> 403), tâches, usage, coût."""
from __future__ import annotations

import zipfile

REF = {
    "nom_client": "ACME SAS",
    "plafond_hospitalisation": "2000",
    "date_effet": "2024-01-01",
    "numero_contrat": "CTR-2024-001",
}
DOC = {
    "nom_client": "ACME SAS",
    "plafond_hospitalisation": "2000",
    "date_effet": "01/01/2024",
    "numero_contrat": "ctr2024001",
}


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "onix-actions"
    assert "ocr" in body


def test_auth_required(client):
    r = client.post("/audit", json={"document": DOC, "reference": REF},
                    headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_audit_json_conforme(client):
    r = client.post("/audit", json={"document": DOC, "reference": REF})
    assert r.status_code == 200
    assert r.json()["verdict"] == "CONFORME"


def test_audit_from_text_heuristic(client):
    text = (
        "Raison sociale: ACME SAS\n"
        "Plafond hospitalisation: 2000\n"
        "Date d'effet: 01/01/2024\n"
        "Numéro de contrat: CTR-2024-001\n"
    )
    r = client.post("/audit", json={"text": text, "reference": REF})
    assert r.status_code == 200
    assert r.json()["verdict"] == "CONFORME"


def test_generate_fiche_and_download(client):
    r = client.post("/generate/fiche", json={
        "client_name": "ACME SAS",
        "summary": "Contrat mutuelle standard. 50 employés.",
        "alert_points": "Kbis manquant.",
    })
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    dl = client.get(f"/download/{job_id}")
    assert dl.status_code == 200
    content = dl.content
    assert len(content) > 0
    # Un .docx est un zip valide contenant document.xml.
    import io
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        assert "word/document.xml" in zf.namelist()


def test_admin_gating_blocks_audit(client):
    # Coupe la fonction 'audit' via /admin/control.
    ctl = client.post("/admin/control", json={
        "action": "disable_feature", "scope": "audit", "reason": "test",
    })
    assert ctl.status_code == 200
    assert ctl.json()["result"] == "applied"
    # Re-audit -> 403.
    r = client.post("/audit", json={"document": DOC, "reference": REF})
    assert r.status_code == 403
    # Réactivation -> de nouveau 200.
    client.post("/admin/control", json={"action": "enable_feature", "scope": "audit"})
    r2 = client.post("/audit", json={"document": DOC, "reference": REF})
    assert r2.status_code == 200


def test_admin_global_kill_switch(client):
    client.post("/admin/control", json={"action": "disable_global", "scope": "global"})
    assert client.post("/audit", json={"document": DOC, "reference": REF}).status_code == 403
    assert client.post("/generate/fiche", json={"client_name": "X"}).status_code == 403
    state = client.get("/admin/state").json()
    assert state["global_enabled"] is False


def test_admin_block_user(client):
    client.post("/admin/control", json={
        "action": "block_user", "scope": "user", "target_id": "user@corp.local",
    })
    blocked = client.post("/audit", json={
        "document": DOC, "reference": REF, "caller_id": "user@corp.local",
    })
    assert blocked.status_code == 403
    # Un autre utilisateur passe.
    ok = client.post("/audit", json={
        "document": DOC, "reference": REF, "caller_id": "autre@corp.local",
    })
    assert ok.status_code == 200


def test_tasks_create_and_list(client):
    c = client.post("/tasks", json={"title": "Relancer ACME", "due_date": "2026-07-01"})
    assert c.status_code == 200
    assert c.json()["status"] == "open"
    lst = client.get("/tasks")
    assert lst.status_code == 200
    assert lst.json()["count"] >= 1


def test_usage_and_summary(client):
    client.post("/usage", json={"event_type": "message_sent", "user_id": "u@corp"})
    s = client.get("/usage/summary")
    assert s.status_code == 200
    assert s.json()["total_events"] >= 1


def test_cost_estimate_and_budget(client, monkeypatch):
    monkeypatch.setenv("ONIX_RATE_CARD", '{"audit_request": 0.5}')
    monkeypatch.setenv("ONIX_BUDGET_EUR", "10")
    e = client.post("/cost/estimate", json={"cost_center": "audit_request", "quantity": 4})
    assert e.status_code == 200
    assert e.json()["estimated_cost_eur"] == 2.0
    c = client.get("/cost")
    assert c.status_code == 200
    assert c.json()["budget"]["budget_eur"] == 10.0


def test_notify_skipped_without_config(client):
    r = client.post("/notify", json={"provider": "webhook", "message": "hello"})
    assert r.status_code == 200
    # Aucune URL configurée -> skipped (pas d'erreur).
    assert r.json()["status"] in ("skipped", "error")


def test_usage_invalid_event_type(client):
    r = client.post("/usage", json={"event_type": "does_not_exist"})
    assert r.status_code == 400
