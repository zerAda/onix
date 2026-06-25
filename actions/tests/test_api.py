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


# --- Réconciliation contrat ↔ SI Fabric : POST /audit/reconcile/file ----------
# OCR et lecture du SI Fabric mockés (déterministe, hors-ligne) ; on prouve le
# flux HTTP complet OCR → champs → réf Fabric → audit → verdict + fiche de revue.
_CONTRAT_BETA = {
    "Client": "CLIENT BETA", "Numero dossier": "BETA-201",
    "Date d effet": "01/01/2026", "Cotisation": "12 500 EUR / an",
    "Garantie": "Prevoyance collective",
}


def _patch_reconcile(monkeypatch, reference):
    """Mocke l'OCR (contrat BETA) et la lecture du SI Fabric (`reference`)."""
    import app.fabric_reference as fabric_reference
    import app.ocr as ocr_mod
    monkeypatch.setattr(ocr_mod, "extract", lambda data, name: {
        "metadata": {"extraction_mode": "pdf_text"}, "tables": [], "fields": _CONTRAT_BETA,
    })
    monkeypatch.setattr(fabric_reference, "fetch_client_reference", lambda ck: reference)
    monkeypatch.setattr(fabric_reference, "fabric_reference_configured", lambda: True)


def _post_reconcile(client, client_key="CLIENT BETA"):
    return client.post(
        "/audit/reconcile/file",
        files={"file": ("c.pdf", b"%PDF-1.4 contrat", "application/pdf")},
        data={"client_key": client_key},
    )


def test_reconcile_file_ecart_cotisation(client, monkeypatch):
    # SI Fabric : cotisation divergente (13000) -> ECART détecté.
    _patch_reconcile(monkeypatch, {
        "nom_client": "CLIENT BETA", "numero_contrat": "BETA-201",
        "date_effet": "01/01/2026", "cotisation_annuelle": "13000",
        "garantie": "Prevoyance collective",
    })
    r = _post_reconcile(client)
    assert r.status_code == 200
    b = r.json()
    assert b["verdict"] == "ECART"
    assert b["_reference_source"] == "fabric_si"
    cot = [f for f in b["fields"] if f["champ"] == "cotisation_annuelle"][0]
    assert cot["statut"] == "MISMATCH"
    assert b["fiche_revue"]["a_revoir"] is True
    assert b["fiche_revue"]["nb_ecarts"] >= 1


def test_reconcile_file_conforme(client, monkeypatch):
    # SI Fabric aligné -> CONFORME, fiche sans revue requise.
    _patch_reconcile(monkeypatch, {
        "nom_client": "CLIENT BETA", "numero_contrat": "BETA-201",
        "date_effet": "01/01/2026", "cotisation_annuelle": "12500",
        "garantie": "Prevoyance collective",
    })
    r = _post_reconcile(client)
    assert r.status_code == 200
    b = r.json()
    assert b["verdict"] == "CONFORME"
    assert b["fiche_revue"]["a_revoir"] is False


def test_reconcile_file_client_non_trouve(client, monkeypatch):
    # Client absent du SI Fabric (référence None) -> CLIENT_NON_TROUVE (fail-closed).
    _patch_reconcile(monkeypatch, None)
    r = _post_reconcile(client, client_key="INCONNU")
    assert r.status_code == 200
    b = r.json()
    assert b["verdict"] == "CLIENT_NON_TROUVE"
    assert b["fiche_revue"]["a_revoir"] is True


# --- Réconciliation de PORTEFEUILLE (lot) : POST /audit/reconcile/batch --------
def test_reconcile_batch_endpoint(client, monkeypatch):
    """Flux HTTP complet du lot : portefeuille mixte -> synthèse par verdict
    (lecture SI mockée -> AUCUNE I/O réelle vers Fabric)."""
    import app.fabric_reference as fabric_reference
    si = {
        "acme": {"nom_client": "ACME", "cotisation_annuelle": "1000"},
        "beta": {"nom_client": "BETA", "cotisation_annuelle": "2000"},
    }
    # reconcile_batch appelle fetch_client_reference(ck, reader=...) : le mock doit
    # absorber le kwarg `reader`.
    monkeypatch.setattr(fabric_reference, "fetch_client_reference",
                        lambda ck, **kw: si.get((ck or "").strip().lower()))
    monkeypatch.setattr(fabric_reference, "fabric_reference_configured", lambda: True)
    items = [
        {"client_key": "acme", "document": {"nom_client": "ACME", "cotisation_annuelle": "1000"}},  # CONFORME
        {"client_key": "beta", "document": {"nom_client": "BETA", "cotisation_annuelle": "9999"}},   # ECART
        {"client_key": "zz", "document": {"nom_client": "ZZ"}},                                      # CLIENT_NON_TROUVE
        {"client_key": "x"},                                                                         # INVALIDE (pas de document)
    ]
    r = client.post("/audit/reconcile/batch", json={"items": items})
    assert r.status_code == 200
    b = r.json()
    s = b["synthese"]
    assert s["total"] == 4
    assert s["CONFORME"] == 1 and s["ECART"] == 1 and s["CLIENT_NON_TROUVE"] == 1 and s["invalides"] == 1
    assert len(b["fiches"]) == 4
    assert b["_reference_source"] == "fabric_si"


def test_reconcile_batch_endpoint_borne_fail_closed(client, monkeypatch):
    """Lot trop volumineux -> 400 fail-closed, AVANT toute lecture SI."""
    import app.fabric_reference as fabric_reference
    monkeypatch.setattr(fabric_reference, "fetch_client_reference", lambda ck, **kw: None)
    items = [{"client_key": str(i), "document": {"nom_client": "X"}} for i in range(201)]
    r = client.post("/audit/reconcile/batch", json={"items": items})
    assert r.status_code == 400
    assert "trop volumineux" in str(r.json()).lower()


def test_reconcile_batch_endpoint_borne_configurable(client, monkeypatch):
    """AUDIT : la borne est tunable par env `ONIX_RECONCILE_BATCH_MAX` et FAIL-SAFE
    (valeur illisible → repli sur le défaut 200, jamais d'illimité)."""
    import app.fabric_reference as fabric_reference
    monkeypatch.setattr(fabric_reference, "fetch_client_reference", lambda ck, **kw: None)
    items3 = [{"client_key": str(i), "document": {"nom_client": "X"}} for i in range(3)]
    # Borne abaissée à 2 -> 3 items refusés (400).
    monkeypatch.setenv("ONIX_RECONCILE_BATCH_MAX", "2")
    assert client.post("/audit/reconcile/batch", json={"items": items3}).status_code == 400
    # Valeur d'env illisible -> repli sur 200 -> 3 items acceptés (200).
    monkeypatch.setenv("ONIX_RECONCILE_BATCH_MAX", "pas-un-entier")
    assert client.post("/audit/reconcile/batch", json={"items": items3}).status_code == 200


def test_reconcile_batch_endpoint_si_non_configure(client, monkeypatch):
    """SI non configuré : reader → None partout, `_reference_source`='non_configuree'
    et TOUS les verdicts = CLIENT_NON_TROUVE (fail-closed honnête, pas d'invention)."""
    import app.fabric_reference as fabric_reference
    monkeypatch.setattr(fabric_reference, "fetch_client_reference", lambda ck, **kw: None)
    monkeypatch.setattr(fabric_reference, "fabric_reference_configured", lambda: False)
    items = [
        {"client_key": "a", "document": {"nom_client": "A"}},
        {"client_key": "b", "document": {"nom_client": "B"}},
    ]
    b = client.post("/audit/reconcile/batch", json={"items": items}).json()
    assert b["_reference_source"] == "non_configuree"
    assert b["synthese"]["CLIENT_NON_TROUVE"] == 2
    assert all(f["verdict"] == "CLIENT_NON_TROUVE" for f in b["fiches"])


def test_reconcile_batch_endpoint_lot_vide(client):
    """Lot vide → 200 avec synthèse à zéro (no-op valide, pas une erreur)."""
    r = client.post("/audit/reconcile/batch", json={"items": []})
    assert r.status_code == 200
    b = r.json()
    assert b["synthese"]["total"] == 0 and b["fiches"] == []


def test_reconcile_batch_endpoint_kill_switch(client):
    """Kill-switch 'audit' coupé → l'endpoint batch répond 403 (gate respecté)."""
    client.post("/admin/control",
                json={"action": "disable_feature", "scope": "audit", "reason": "test"})
    try:
        r = client.post("/audit/reconcile/batch",
                        json={"items": [{"client_key": "a", "document": {"nom_client": "A"}}]})
        assert r.status_code == 403
    finally:
        client.post("/admin/control", json={"action": "enable_feature", "scope": "audit"})


def test_reconcile_batch_endpoint_format_csv(client, monkeypatch):
    """`?format=csv` → 200 text/csv (attachment) avec en-tête ; défaut = JSON inchangé."""
    import app.fabric_reference as fabric_reference
    si = {"acme": {"nom_client": "ACME", "cotisation_annuelle": "1000"}}
    monkeypatch.setattr(fabric_reference, "fetch_client_reference",
                        lambda ck, **kw: si.get((ck or "").strip().lower()))
    items = [{"client_key": "acme", "document": {"nom_client": "ACME", "cotisation_annuelle": "1000"}}]
    r = client.post("/audit/reconcile/batch?format=csv", json={"items": items})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers.get("content-disposition", "")
    assert r.text.splitlines()[0] == "client,verdict,a_revoir,nb_ecarts,recommandation"
    # Sans format -> JSON (comportement inchangé).
    r2 = client.post("/audit/reconcile/batch", json={"items": items})
    assert r2.headers["content-type"].startswith("application/json")
    assert "synthese" in r2.json()


# --- RAG non-agentique souverain : POST /rag/ask ------------------------------
def test_rag_ask_grounded(client, monkeypatch):
    """Récupère le bon document + génère une réponse grounded (générateur mocké)."""
    import app.rag_local as rag_local
    monkeypatch.setattr(rag_local, "ollama_generator",
                        lambda prompt: "La cotisation annuelle est de 12 500 EUR (dossier BETA-201).")
    r = client.post("/rag/ask", json={
        "question": "Quelle est la cotisation du dossier BETA ?",
        "documents": [
            {"id": "fiche_beta", "content": "Dossier BETA-201, cotisation annuelle 12 500 EUR, prevoyance collective."},
            {"id": "fiche_gamma", "content": "Dossier GAMMA-301, sante collective."},
        ],
    })
    assert r.status_code == 200
    b = r.json()
    assert b["grounded"] is True
    assert b["sources"] == ["fiche_beta"]      # le doc le plus pertinent
    assert "12 500" in b["answer"]


def test_rag_ask_aucune_source_failclosed(client, monkeypatch):
    """Aucun document pertinent -> refus explicite (grounded=False), pas d'invention."""
    import app.rag_local as rag_local
    monkeypatch.setattr(rag_local, "ollama_generator",
                        lambda prompt: "NE DOIT PAS ETRE APPELE")
    r = client.post("/rag/ask", json={
        "question": "Comment reparer mon velo aujourd'hui ?",
        "documents": [{"id": "d1", "content": "Dossier client mutuelle sante entreprise."}],
    })
    assert r.status_code == 200
    b = r.json()
    assert b["grounded"] is False
    assert b["sources"] == []


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


def test_summary_cost_is_cumulative_not_windowed(client):
    """M19 (FinOps) : le total budget `estimated_cost_eur` doit couvrir TOUS les
    événements, pas seulement la fenêtre des `limit` plus récents.

    Avant le correctif, `summary()` sommait le coût sur `ORDER BY ... DESC LIMIT
    limit`, donc au-delà de `limit` événements le budget devenait une fenêtre
    glissante (sous-comptage sans borne). On insère 5 événements à 1.0 € et on
    appelle `summary(limit=2)` : la somme doit valoir 5.0 (cumul), pas 2.0.
    """
    import app.usage_tracker as ut  # module rechargé par la fixture `client`

    for i in range(5):
        ut.track(
            "cost_estimated",
            estimated_cost_eur=1.0,
            timestamp_utc=f"2026-01-01T00:00:0{i}Z",
        )

    s = ut.summary(limit=2)  # fenêtre de ventilation < nombre d'événements
    assert s["estimated_cost_eur"] == 5.0  # cumul réel (valait 2.0 avant le fix)
    assert s["total_events"] >= 5
    # La ventilation by_type reste bornée à la fenêtre récente (limit=2).
    assert sum(s["by_type"].values()) == 2


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
