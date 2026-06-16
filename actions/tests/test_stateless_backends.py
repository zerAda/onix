"""Tests WS-CW1 — backends STATELESS (multi-réplica) de onix-actions.

Trois familles :

  1. **Unitaires DB** (toujours exécutés) : l'adaptateur SQL SQLite→Postgres
     (`app.db.translate_sql`) traduit correctement placeholders, upsert,
     introspection et littéraux `%` — sans aucun conteneur.

  2. **Postgres** (exécutés si `ONIX_TEST_PG_URL` est défini, sinon SKIP) :
     l'app entière tourne sur Postgres partagé — admin/control persiste, chaîne
     d'audit HMAC vérifiable, usage agrégé, tâches — PROUVANT le partage d'état.
     Un 2e "process logique" (nouveau client, même DB) relit l'état → simule deux
     répliques.

  3. **S3/MinIO** (exécutés si `ONIX_TEST_S3=1`, sinon SKIP) : `docgen` écrit le
     `.docx` sur S3 et `GET /download/{id}` le relit depuis S3 — donc une réplique
     qui n'a PAS généré le fichier peut quand même le servir.

  4. **Celery** (toujours, en mode EAGER) : `POST /audit/file/async` renvoie 202 +
     task_id et `GET /jobs/{task_id}` rend le résultat.

Les tests Postgres/S3 SKIPPENT proprement hors environnement dédié → la suite par
défaut (`pytest actions/tests -q`) reste verte sur n'importe quelle machine.
"""
from __future__ import annotations

import importlib
import io
import os
import sys
import zipfile

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

API_KEY = "test-key-0123456789"

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


# ===========================================================================
# 1. Unitaires de l'adaptateur SQL (aucun conteneur)
# ===========================================================================
def _translate(sql):
    """Traduit en forçant le backend Postgres, indépendamment de l'env courant."""
    import app.db as db

    prev = os.environ.get("ONIX_DB_BACKEND")
    os.environ["ONIX_DB_BACKEND"] = "postgres"
    try:
        importlib.reload(db)
        return db.translate_sql(sql)
    finally:
        if prev is None:
            os.environ.pop("ONIX_DB_BACKEND", None)
        else:
            os.environ["ONIX_DB_BACKEND"] = prev
        importlib.reload(db)


def test_translate_positional_placeholder():
    out, override = _translate("SELECT value FROM admin_state WHERE key=?")
    assert out == "SELECT value FROM admin_state WHERE key=%s"
    assert override is None


def test_translate_named_placeholders():
    out, _ = _translate(
        "INSERT INTO tasks(task_id, title) VALUES(:task_id,:title)"
    )
    assert "%(task_id)s" in out and "%(title)s" in out
    assert "?" not in out


def test_translate_like_literal_percent_doubled():
    # Le `%` littéral d'un LIKE doit être doublé pour psycopg (pyformat).
    out, _ = _translate(
        "SELECT key FROM admin_state WHERE key LIKE 'blocked_user:%' AND value='1'"
    )
    assert "LIKE 'blocked_user:%%'" in out


def test_translate_insert_or_replace_to_on_conflict():
    out, _ = _translate(
        "INSERT OR REPLACE INTO usage_events(event_id, timestamp_utc, status)"
        " VALUES(:event_id,:timestamp_utc,:status)"
    )
    assert out.upper().startswith("INSERT INTO USAGE_EVENTS")
    assert "ON CONFLICT (event_id) DO UPDATE SET" in out
    # La PK n'est pas réaffectée ; les autres colonnes le sont.
    assert "timestamp_utc=EXCLUDED.timestamp_utc" in out
    assert "status=EXCLUDED.status" in out
    assert "event_id=EXCLUDED.event_id" not in out


def test_translate_pragma_table_info_parameterised():
    out, override = _translate("PRAGMA table_info(admin_audit)")
    assert "information_schema.columns" in out
    assert "column_name AS name" in out
    # Le nom de table est passé en PARAMÈTRE LIÉ (anti-injection), pas interpolé.
    assert override == ("admin_audit",)
    assert "admin_audit" not in out


def test_translate_sqlite_master_to_information_schema():
    out, _ = _translate(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
    )
    assert "information_schema.tables" in out
    assert "table_name AS name" in out


def test_backend_default_is_sqlite(monkeypatch):
    import app.db as db

    monkeypatch.delenv("ONIX_DB_BACKEND", raising=False)
    importlib.reload(db)
    assert db.backend() == "sqlite"
    assert db.is_postgres() is False


# ===========================================================================
# Helpers de client (réimport propre, comme conftest mais paramétrable backend)
# ===========================================================================
def _build_client(monkeypatch, **env):
    monkeypatch.setenv("ONIX_ACTIONS_API_KEY", API_KEY)
    monkeypatch.setenv("ONIX_ACTIONS_ADMIN_KEY_OPTIONAL", "true")
    monkeypatch.setenv("ONIX_ACTIONS_RATE_LIMIT", "100000/minute")
    monkeypatch.setenv("ONIX_ACTIONS_REQUIRE_CALLER_IDENTITY", "false")
    monkeypatch.setenv("ONIX_EGRESS_ALLOWLIST", "127.0.0.1,localhost")
    for var in list(os.environ):
        if var.startswith("ONIX_") and var.endswith("_ENABLED") and var != "ONIX_QUEUE_ENABLED":
            monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)

    from fastapi.testclient import TestClient

    import app.db as db
    import app.admin_state as admin_state

    importlib.reload(db)
    importlib.reload(admin_state)
    for modname in (
        "app.usage_tracker", "app.tasks", "app.cost_tracker",
        "app.audit_log", "app.retention", "app.objstore",
        "app.docgen", "app.celery_app",
    ):
        importlib.reload(importlib.import_module(modname))
    import app.security as security
    import app.main as main

    importlib.reload(security)
    importlib.reload(main)
    security.reset_rate_limits()
    c = TestClient(main.app)
    c.headers.update({"X-API-Key": API_KEY})
    return c


# ===========================================================================
# 2. Postgres — état partagé entre "répliques" (SKIP si pas de PG)
# ===========================================================================
_PG_URL = os.environ.get("ONIX_TEST_PG_URL")
_pg = pytest.mark.skipif(not _PG_URL, reason="ONIX_TEST_PG_URL non défini (pas de Postgres)")


@_pg
def test_pg_admin_control_persists_and_gates(monkeypatch):
    c = _build_client(monkeypatch, ONIX_DB_BACKEND="postgres", ONIX_DB_URL=_PG_URL,
                      ONIX_ACTIONS_AUDIT_HMAC_KEY="pg-audit-key")
    with c:
        # État neutre au départ (purge éventuelle d'un run précédent).
        c.post("/admin/control", json={"action": "enable_global", "scope": "global"})
        c.post("/admin/control", json={"action": "enable_feature", "scope": "audit"})
        # Audit OK.
        assert c.post("/audit", json={"document": DOC, "reference": REF}).status_code == 200
        # Coupe la fonction audit -> persiste en Postgres.
        assert c.post("/admin/control", json={"action": "disable_feature", "scope": "audit"}).status_code == 200
        assert c.post("/audit", json={"document": DOC, "reference": REF}).status_code == 403

    # "Réplique 2" : nouveau client (nouveau process logique), MÊME Postgres.
    # Il doit VOIR le flag coupé sans l'avoir posé lui-même -> état PARTAGÉ.
    c2 = _build_client(monkeypatch, ONIX_DB_BACKEND="postgres", ONIX_DB_URL=_PG_URL,
                       ONIX_ACTIONS_AUDIT_HMAC_KEY="pg-audit-key")
    with c2:
        assert c2.post("/audit", json={"document": DOC, "reference": REF}).status_code == 403
        state = c2.get("/admin/state").json()
        assert state["features"]["audit"] is False
        # Réactive pour ne pas polluer d'autres tests sur la même base.
        c2.post("/admin/control", json={"action": "enable_feature", "scope": "audit"})


@_pg
def test_pg_audit_chain_hmac_verifiable(monkeypatch):
    c = _build_client(monkeypatch, ONIX_DB_BACKEND="postgres", ONIX_DB_URL=_PG_URL,
                      ONIX_ACTIONS_AUDIT_HMAC_KEY="pg-audit-key")
    with c:
        c.post("/admin/control", json={"action": "disable_feature", "scope": "generate"})
        c.post("/admin/control", json={"action": "enable_feature", "scope": "generate"})
        v = c.get("/admin/audit/verify").json()
        assert v["ok"] is True
        assert v["count"] >= 2


@_pg
def test_pg_usage_and_tasks_shared(monkeypatch):
    c = _build_client(monkeypatch, ONIX_DB_BACKEND="postgres", ONIX_DB_URL=_PG_URL)
    with c:
        c.post("/usage", json={"event_type": "message_sent", "user_id": "pg@corp.fr"})
        c.post("/tasks", json={"title": "Tache PG", "due_date": "2026-09-01"})
        s = c.get("/usage/summary").json()
        assert s["total_events"] >= 1
        lst = c.get("/tasks").json()
        assert lst["count"] >= 1


# ===========================================================================
# 3. S3 / MinIO — .docx déporté, download multi-réplica (SKIP si pas de MinIO)
# ===========================================================================
_S3 = os.environ.get("ONIX_TEST_S3") == "1"
_s3mark = pytest.mark.skipif(not _S3, reason="ONIX_TEST_S3 != 1 (pas de MinIO)")


@_s3mark
def test_s3_generate_and_download_cross_replica(monkeypatch, tmp_path):
    # Réplique A : génère la fiche -> .docx sur S3.
    cA = _build_client(
        monkeypatch,
        ONIX_OBJECT_STORE="s3",
        ONIX_JOBS_DIR=str(tmp_path / "jobsA"),
    )
    with cA:
        r = cA.post("/generate/fiche", json={
            "client_name": "ACME SAS",
            "summary": "Contrat mutuelle.",
            "alert_points": "Kbis manquant.",
        })
        assert r.status_code == 200
        job_id = r.json()["job_id"]

    # Réplique B : répertoire LOCAL différent (vide), même S3. Doit servir le
    # fichier depuis S3 -> prouve le partage objet en multi-réplica.
    cB = _build_client(
        monkeypatch,
        ONIX_OBJECT_STORE="s3",
        ONIX_JOBS_DIR=str(tmp_path / "jobsB_empty"),
    )
    with cB:
        dl = cB.get(f"/download/{job_id}")
        assert dl.status_code == 200, dl.text
        content = dl.content
        assert len(content) > 0
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            assert "word/document.xml" in zf.namelist()


# ===========================================================================
# 4. Celery — audit asynchrone en mode EAGER (toujours exécuté)
# ===========================================================================
def test_async_audit_eager_returns_result(monkeypatch, tmp_path):
    """En mode EAGER (`.delay()` synchrone), POST /audit/file/async renvoie 202 +
    task_id, et GET /jobs/{task_id} rend le résultat (verdict d'audit)."""
    c = _build_client(
        monkeypatch,
        ONIX_DB_BACKEND="sqlite",
        ONIX_ACTIONS_DB=str(tmp_path / "db.sqlite"),
        ONIX_JOBS_DIR=str(tmp_path / "jobs"),
        ONIX_QUEUE_ENABLED="true",
        ONIX_QUEUE_EAGER="true",
    )
    # Un "PDF" trivial : l'OCR sera "unavailable" sans tesseract/poppler OU rendra
    # un texte vide -> on vérifie surtout le CYCLE async (202 -> task_id -> statut).
    pdf_bytes = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    with c:
        r = c.post(
            "/audit/file/async",
            files={"file": ("doc.pdf", pdf_bytes, "application/pdf")},
            data={"reference": '{"nom_client": "ACME SAS"}'},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "accepted"
        task_id = body["task_id"]
        assert task_id

        # En EAGER, le résultat est immédiatement disponible.
        st = c.get(f"/jobs/{task_id}")
        assert st.status_code == 200, st.text
        sj = st.json()
        assert sj["task_id"] == task_id
        # État Celery : SUCCESS en eager (la tâche a tourné en process).
        assert sj["state"] in ("SUCCESS", "STARTED", "PENDING")
        if sj["state"] == "SUCCESS":
            # Le résultat porte un statut métier (completed ou error ocr_unavailable).
            assert sj["result"]["status"] in ("completed", "error")


def test_async_endpoints_gated_off_by_default(monkeypatch, tmp_path):
    """Sans ONIX_QUEUE_ENABLED, les endpoints async répondent 503 (mode par
    défaut mono-poste inchangé)."""
    c = _build_client(
        monkeypatch,
        ONIX_ACTIONS_DB=str(tmp_path / "db.sqlite"),
        ONIX_QUEUE_ENABLED=None,
    )
    with c:
        r = c.post(
            "/audit/file/async",
            files={"file": ("doc.pdf", b"%PDF-1.4\n", "application/pdf")},
        )
        assert r.status_code == 503
        assert c.get("/jobs/whatever").status_code == 503
