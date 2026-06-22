"""Tests WS2 — Sécurité applicative & conformité RGPD (onix-actions).

Couvre les nouveautés livrées par WS2 :
  * redaction PII (JWT/IBAN/NIR/email + anti-CRLF) — unité + filtre de log ;
  * identité d'appelant vérifiée (HMAC par appel, JWT HS256) + fail-closed ;
  * clé admin distincte OBLIGATOIRE (fail-closed) ;
  * rate-limiting par appelant (429) ;
  * DLP egress (allowlist, fail-closed, anti-SSRF, https-only) ;
  * journal d'audit chaîné tamper-evident (détection d'altération) ;
  * journalisation d'accès (UPN hashés) ;
  * rétention TTL + effacement ciblé par sujet (art. 17) ;
  * fail-closed sur flag inconnu.

Les tests repartent — sauf mention — des DÉFAUTS fail-closed pour PROUVER le
durcissement (le profil permissif de conftest est surchargé localement)."""
from __future__ import annotations

import importlib
import logging
import time

import pytest


# ===========================================================================
# 1. Redaction PII (safe_logger)
# ===========================================================================
def test_redact_text_couvre_jwt_iban_nir_email():
    from app.safe_logger import redact_text

    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.abc-DEF_123"
    assert "[REDACTED_JWT]" in redact_text(f"token={jwt}")
    assert "[REDACTED_IBAN]" in redact_text("IBAN FR7630006000011234567890189 ok")
    assert "[REDACTED_NIR]" in redact_text("NIR 1 85 12 75 116 001 25 fin")
    assert "[REDACTED_EMAIL]" in redact_text("contact jean.dupont@corp.fr please")
    # La donnée brute ne doit plus apparaître.
    assert "jean.dupont@corp.fr" not in redact_text("jean.dupont@corp.fr")


def test_redact_anti_crlf_log_forging():
    from app.safe_logger import redact_text

    payload = "ligne1\nINFO:fake:injecté\r\n"
    out = redact_text(payload)
    assert "\n" not in out and "\r" not in out
    assert "\\n" in out  # le retour à la ligne est échappé en littéral


def test_redact_structures_imbriquees():
    from app.safe_logger import redact

    data = {"user": "a@b.fr", "items": ["IBAN FR7630006000011234567890189", 42], "n": 1}
    out = redact(data)
    assert out["n"] == 1
    assert "[REDACTED_EMAIL]" in out["user"]
    assert "[REDACTED_IBAN]" in out["items"][0]
    assert out["items"][1] == 42  # les scalaires non-PII passent tels quels


def test_redacting_filter_sur_logger(caplog):
    from app import safe_logger

    logger = logging.getLogger("onix.actions.test_redact")
    safe_logger.install("onix.actions.test_redact")
    with caplog.at_level(logging.INFO, logger="onix.actions.test_redact"):
        logger.info("email=%s", "victime@corp.fr")
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "victime@corp.fr" not in joined
    assert "[REDACTED_EMAIL]" in joined


# ===========================================================================
# 2. Identité d'appelant vérifiée (HMAC / JWT)
# ===========================================================================
def test_hmac_identity_resolue(monkeypatch):
    monkeypatch.setenv("ONIX_ACTIONS_CALLER_HMAC_SECRET", "s3cr3t-hmac")
    import app.caller_identity as ci

    importlib.reload(ci)
    ts = str(int(time.time()))
    sig = ci.compute_hmac("s3cr3t-hmac", "alice@corp.fr", ts, "POST", "/audit")
    ctx = ci.resolve_caller(
        http_method="POST", path="/audit", authorization=None,
        x_caller="alice@corp.fr", x_timestamp=ts, x_signature=sig,
    )
    assert ctx.method == "hmac" and ctx.caller_id == "alice@corp.fr"
    assert ctx.is_service is False


def test_hmac_signature_invalide_repli_service(monkeypatch):
    monkeypatch.setenv("ONIX_ACTIONS_CALLER_HMAC_SECRET", "s3cr3t-hmac")
    import app.caller_identity as ci

    importlib.reload(ci)
    ts = str(int(time.time()))
    ctx = ci.resolve_caller(
        http_method="POST", path="/audit", authorization=None,
        x_caller="alice@corp.fr", x_timestamp=ts, x_signature="deadbeef",
    )
    assert ctx.is_service is True  # signature fausse -> pas d'identité HMAC


def test_hmac_anti_rejeu_horodatage_perime(monkeypatch):
    monkeypatch.setenv("ONIX_ACTIONS_CALLER_HMAC_SECRET", "s3cr3t-hmac")
    monkeypatch.setenv("ONIX_HMAC_MAX_SKEW", "60")
    import app.caller_identity as ci

    importlib.reload(ci)
    old_ts = str(int(time.time()) - 3600)  # 1 h dans le passé
    sig = ci.compute_hmac("s3cr3t-hmac", "alice@corp.fr", old_ts, "POST", "/audit")
    ctx = ci.resolve_caller(
        http_method="POST", path="/audit", authorization=None,
        x_caller="alice@corp.fr", x_timestamp=old_ts, x_signature=sig,
    )
    assert ctx.is_service is True  # horodatage périmé -> rejeté


def _make_hs256_jwt(secret: str, payload: dict) -> str:
    import base64
    import hashlib
    import hmac as _hmac
    import json

    def _seg(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    header = _seg({"alg": "HS256", "typ": "JWT"})
    body = _seg(payload)
    signing_input = f"{header}.{body}".encode()
    sig = base64.urlsafe_b64encode(
        _hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"{header}.{body}.{sig}"


def test_jwt_hs256_identity(monkeypatch):
    monkeypatch.setenv("ONIX_OIDC_HS256_SECRET", "jwt-secret")
    monkeypatch.setenv("ONIX_OIDC_ISSUER", "https://idp.corp")
    monkeypatch.setenv("ONIX_OIDC_AUDIENCE", "onix-actions")
    import app.caller_identity as ci

    importlib.reload(ci)
    token = _make_hs256_jwt("jwt-secret", {
        "preferred_username": "bob@corp.fr", "exp": int(time.time()) + 600,
        "iss": "https://idp.corp", "aud": "onix-actions",
    })
    ctx = ci.resolve_caller(
        http_method="GET", path="/x", authorization=f"Bearer {token}",
        x_caller=None, x_timestamp=None, x_signature=None,
    )
    assert ctx.method == "jwt" and ctx.caller_id == "bob@corp.fr"


def test_jwt_hs256_fail_closed_sans_exp_ou_iss_aud(monkeypatch):
    """Fail-closed : JWT sans exp -> refusé ; iss/aud non configurés -> JWT ignoré."""
    monkeypatch.setenv("ONIX_OIDC_HS256_SECRET", "jwt-secret")
    monkeypatch.setenv("ONIX_OIDC_ISSUER", "https://idp.corp")
    monkeypatch.setenv("ONIX_OIDC_AUDIENCE", "onix-actions")
    import app.caller_identity as ci

    importlib.reload(ci)
    # Token SANS exp -> refusé (repli service).
    tok_no_exp = _make_hs256_jwt("jwt-secret", {
        "preferred_username": "bob@corp.fr", "iss": "https://idp.corp", "aud": "onix-actions",
    })
    ctx = ci.resolve_caller(http_method="GET", path="/x",
                            authorization=f"Bearer {tok_no_exp}",
                            x_caller=None, x_timestamp=None, x_signature=None)
    assert ctx.is_service is True
    # Token pour un AUTRE audience -> refusé.
    tok_bad_aud = _make_hs256_jwt("jwt-secret", {
        "preferred_username": "bob@corp.fr", "exp": int(time.time()) + 600,
        "iss": "https://idp.corp", "aud": "autre-rp",
    })
    ctx2 = ci.resolve_caller(http_method="GET", path="/x",
                             authorization=f"Bearer {tok_bad_aud}",
                             x_caller=None, x_timestamp=None, x_signature=None)
    assert ctx2.is_service is True
    # iss/aud non configurés -> JWT non accepté (fail-closed).
    monkeypatch.delenv("ONIX_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("ONIX_OIDC_AUDIENCE", raising=False)
    importlib.reload(ci)
    tok_ok = _make_hs256_jwt("jwt-secret", {
        "preferred_username": "bob@corp.fr", "exp": int(time.time()) + 600,
        "iss": "x", "aud": "y",
    })
    ctx3 = ci.resolve_caller(http_method="GET", path="/x",
                             authorization=f"Bearer {tok_ok}",
                             x_caller=None, x_timestamp=None, x_signature=None)
    assert ctx3.is_service is True


def test_require_caller_fail_closed_si_identite_exigee(monkeypatch, tmp_path):
    """ONIX_ACTIONS_REQUIRE_CALLER_IDENTITY=true : un appel clé de service SEULE
    (sans HMAC/JWT) est refusé (401)."""
    monkeypatch.setenv("ONIX_ACTIONS_API_KEY", "test-key-0123456789")
    monkeypatch.setenv("ONIX_ACTIONS_DB", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("ONIX_ACTIONS_REQUIRE_CALLER_IDENTITY", "true")
    monkeypatch.setenv("ONIX_ACTIONS_ADMIN_KEY_OPTIONAL", "true")
    monkeypatch.setenv("ONIX_EGRESS_DEFAULT_DENY", "false")
    from fastapi.testclient import TestClient

    import app.admin_state as admin_state
    import app.security as security
    import app.main as main

    importlib.reload(admin_state)
    importlib.reload(security)
    importlib.reload(main)
    with TestClient(main.app) as c:
        r = c.post("/usage", json={"event_type": "message_sent"},
                   headers={"X-API-Key": "test-key-0123456789"})
        assert r.status_code == 401


# ===========================================================================
# 3. Clé admin distincte OBLIGATOIRE (fail-closed)
# ===========================================================================
def _client_with(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("ONIX_ACTIONS_API_KEY", "test-key-0123456789")
    monkeypatch.setenv("ONIX_ACTIONS_DB", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("ONIX_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("ONIX_ACTIONS_RATE_LIMIT", "10000/minute")
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    from fastapi.testclient import TestClient

    import app.admin_state as admin_state
    import app.security as security
    import app.main as main

    importlib.reload(admin_state)
    importlib.reload(security)
    importlib.reload(main)
    security.reset_rate_limits()
    c = TestClient(main.app)
    c.headers.update({"X-API-Key": "test-key-0123456789"})
    return c


def test_admin_fail_closed_sans_cle_admin(monkeypatch, tmp_path):
    """Par défaut (clé admin non configurée, non optionnelle) -> /admin/* = 403."""
    c = _client_with(
        monkeypatch, tmp_path,
        ONIX_ACTIONS_ADMIN_KEY=None, ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="false",
    )
    with c:
        r = c.get("/admin/state")
        assert r.status_code == 403


def test_admin_avec_cle_admin_distincte(monkeypatch, tmp_path):
    c = _client_with(
        monkeypatch, tmp_path,
        ONIX_ACTIONS_ADMIN_KEY="admin-secret-key", ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="false",
    )
    with c:
        # Sans X-Admin-Key -> 403.
        assert c.get("/admin/state").status_code == 403
        # Avec la bonne clé admin -> 200.
        r = c.get("/admin/state", headers={"X-Admin-Key": "admin-secret-key"})
        assert r.status_code == 200
        # Mauvaise clé admin -> 403.
        assert c.get("/admin/state", headers={"X-Admin-Key": "wrong"}).status_code == 403


# ===========================================================================
# 4. Rate-limiting par appelant
# ===========================================================================
def test_rate_limit_429_par_appelant(monkeypatch, tmp_path):
    c = _client_with(
        monkeypatch, tmp_path,
        ONIX_ACTIONS_RATE_LIMIT="3/minute", ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true",
    )
    with c:
        codes = [c.get("/health").status_code for _ in range(2)]  # /health non gardé
        # /usage est gardé (require_caller) -> compte dans le quota.
        ok = [c.post("/usage", json={"event_type": "message_sent"}).status_code for _ in range(3)]
        blocked = c.post("/usage", json={"event_type": "message_sent"})
        assert ok == [200, 200, 200]
        assert blocked.status_code == 429
        assert "Retry-After" in blocked.headers


def test_rate_limit_admin_non_soumis_au_quota(monkeypatch, tmp_path):
    """L'administration n'est PAS limitée : un kill-switch reste joignable même
    quand le seau métier est plein."""
    c = _client_with(
        monkeypatch, tmp_path,
        ONIX_ACTIONS_RATE_LIMIT="1/minute", ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true",
    )
    with c:
        # Sature le quota métier.
        assert c.post("/usage", json={"event_type": "message_sent"}).status_code == 200
        assert c.post("/usage", json={"event_type": "message_sent"}).status_code == 429
        # L'admin passe quand même (plusieurs fois).
        for _ in range(5):
            assert c.get("/admin/state").status_code == 200


def test_rate_limit_disable_avec_zero_par_heure(monkeypatch, tmp_path):
    """ONIX_ACTIONS_RATE_LIMIT=0/hour DÉSACTIVE le quota (et ne devient pas 1/h)."""
    c = _client_with(
        monkeypatch, tmp_path,
        ONIX_ACTIONS_RATE_LIMIT="0/hour", ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true",
    )
    with c:
        codes = [c.post("/usage", json={"event_type": "message_sent"}).status_code
                 for _ in range(5)]
        assert codes == [200] * 5


# ===========================================================================
# 5. DLP egress
# ===========================================================================
def test_dlp_check_egress_allowlist_et_https(monkeypatch):
    from app import dlp

    # Isolation propre via monkeypatch (restauré en fin de test).
    for k in ("ONIX_EGRESS_ALLOWLIST", "ONIX_EGRESS_ALLOW_HTTP",
              "ONIX_EGRESS_ALLOW_PRIVATE_IP", "ONIX_EGRESS_DEFAULT_DENY"):
        monkeypatch.delenv(k, raising=False)
    # Sans allowlist + fail-closed (défaut) -> refus.
    with pytest.raises(dlp.EgressDenied):
        dlp.check_egress("https://hooks.slack.com/abc")
    # Avec allowlist : domaine autorisé OK, autre refusé.
    monkeypatch.setenv("ONIX_EGRESS_ALLOWLIST", "hooks.slack.com,*.corp.local")
    assert dlp.check_egress("https://hooks.slack.com/abc").startswith("https://")
    assert dlp.check_egress("https://team.corp.local/hook")
    with pytest.raises(dlp.EgressDenied):
        dlp.check_egress("https://evil.example.com/x")
    # http refusé par défaut (https-only).
    with pytest.raises(dlp.EgressDenied):
        dlp.check_egress("http://hooks.slack.com/abc")


def test_dlp_anti_ssrf_ip_privee(monkeypatch):
    from app import dlp

    monkeypatch.setenv("ONIX_EGRESS_ALLOWLIST", "evil.test")
    monkeypatch.setenv("ONIX_EGRESS_ALLOW_HTTP", "true")
    monkeypatch.delenv("ONIX_EGRESS_ALLOW_PRIVATE_IP", raising=False)
    # Une IP privée/link-local littérale non allowlistée -> refus anti-SSRF.
    with pytest.raises(dlp.EgressDenied):
        dlp.check_egress("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(dlp.EgressDenied):
        dlp.check_egress("http://10.0.0.5/x")
    # CGNAT (100.64.0.0/10) : non couvert par is_private en 3.11 -> doit être bloqué.
    with pytest.raises(dlp.EgressDenied):
        dlp.check_egress("http://100.64.0.1/x")


def test_dlp_notify_refuse_destination_hors_allowlist(monkeypatch, tmp_path):
    c = _client_with(
        monkeypatch, tmp_path,
        ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true",
        ONIX_EGRESS_ALLOWLIST="hooks.slack.com",
        ONIX_EGRESS_ALLOW_HTTP="false", ONIX_EGRESS_ALLOW_PRIVATE_IP="false",
    )
    with c:
        r = c.post("/notify", json={"provider": "webhook", "message": "x",
                                    "url": "https://evil.example.com/hook"})
        assert r.status_code == 403
        # Une destination allowlistée passe le contrôle DLP (l'envoi échouera
        # réseau mais sans 403 DLP).
        r2 = c.post("/notify", json={"provider": "webhook", "message": "x",
                                     "url": "https://hooks.slack.com/services/T/B/X"})
        assert r2.status_code == 200


def test_dlp_tasks_webhook_refuse(monkeypatch, tmp_path):
    c = _client_with(
        monkeypatch, tmp_path,
        ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true",
        ONIX_EGRESS_ALLOWLIST="hooks.slack.com", ONIX_EGRESS_ALLOW_PRIVATE_IP="false",
    )
    with c:
        r = c.post("/tasks", json={"title": "t", "webhook_url": "https://evil.example.com/x"})
        assert r.status_code == 403


# ===========================================================================
# 6. Journal d'audit chaîné tamper-evident
# ===========================================================================
def test_audit_chain_verify_ok_puis_alteration(monkeypatch, tmp_path):
    c = _client_with(
        monkeypatch, tmp_path,
        ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true",
        ONIX_ACTIONS_AUDIT_HMAC_KEY="audit-chain-key",
    )
    with c:
        # Génère quelques entrées d'audit via /admin/control.
        c.post("/admin/control", json={"action": "disable_feature", "scope": "audit"})
        c.post("/admin/control", json={"action": "enable_feature", "scope": "audit"})
        c.post("/admin/control", json={"action": "disable_global", "scope": "global"})
        v = c.get("/admin/audit/verify").json()
        assert v["ok"] is True and v["count"] >= 3

        # Altère une ligne directement en base -> la chaîne doit casser.
        import sqlite3

        import app.admin_state as admin_state

        with sqlite3.connect(admin_state.db_path()) as conn:
            conn.execute("UPDATE admin_audit SET reason='falsifié' WHERE seq=2")
            conn.commit()
    # Re-vérifier (nouveau client, même DB).
    c2 = _client_with(
        monkeypatch, tmp_path,
        ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true",
        ONIX_ACTIONS_AUDIT_HMAC_KEY="audit-chain-key",
    )
    with c2:
        v2 = c2.get("/admin/audit/verify").json()
        assert v2["ok"] is False
        assert v2["broken_at"] == 2


def test_audit_chain_sans_cle_puis_avec_cle_signale_downgrade(monkeypatch, tmp_path):
    """Fail-closed M1 : entrées écrites SANS clé (SHA-256) PUIS clé ajoutée.

    L'ANCIEN comportement (marqueur d'algo par ligne) acceptait la chaîne mixte
    sha256+hmac. C'était une FAILLE : l'algo stocké par ligne pilotait la vérif,
    donc un attaquant pouvait réécrire une ligne en sha256 keyless (recalculable
    SANS la clé) et la chaîne « vérifiait » → downgrade HMAC→keyless silencieux.

    Politique corrigée : dès qu'une clé est configurée, toute ligne keyless
    (sha256) est un downgrade indétectable d'une vraie altération → rupture. La
    migration légitime keyless→HMAC doit donc se faire sur une base d'audit
    vierge (ou via re-scellement explicite), jamais en mêlant les deux."""
    # Phase 1 : pas de clé d'audit (lignes écrites en sha256 keyless).
    c = _client_with(
        monkeypatch, tmp_path,
        ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true", ONIX_ACTIONS_AUDIT_HMAC_KEY=None,
    )
    with c:
        c.post("/admin/control", json={"action": "disable_feature", "scope": "audit"})
        c.post("/admin/control", json={"action": "enable_feature", "scope": "audit"})
        assert c.get("/admin/audit/verify").json()["ok"] is True
    # Phase 2 : clé ajoutée. Les lignes keyless préexistantes deviennent suspectes
    # (recalculables sans clé) → la chaîne DOIT signaler un downgrade (fail-closed).
    c2 = _client_with(
        monkeypatch, tmp_path,
        ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true", ONIX_ACTIONS_AUDIT_HMAC_KEY="cle-ajoutee",
    )
    with c2:
        c2.post("/admin/control", json={"action": "disable_global", "scope": "global"})
        v = c2.get("/admin/audit/verify").json()
        assert v["ok"] is False
        assert "downgrade" in (v.get("reason") or "").lower()
        assert v["broken_at"] == 1  # la première ligne keyless est rejetée


def test_audit_reason_redacted_avant_persistance(monkeypatch, tmp_path):
    c = _client_with(monkeypatch, tmp_path, ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true")
    with c:
        c.post("/admin/control", json={
            "action": "disable_feature", "scope": "audit",
            "reason": "demandé par admin@corp.fr",
        })
        state = c.get("/admin/state").json()
        recent = state["recent_actions"]
        # Le motif persisté ne doit pas contenir l'e-mail en clair.
        # (recent_actions ne renvoie pas 'reason' ; on vérifie directement en base)
    import sqlite3

    import app.admin_state as admin_state

    with sqlite3.connect(admin_state.db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT reason FROM admin_audit WHERE reason IS NOT NULL ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert "admin@corp.fr" not in (row["reason"] or "")
    assert "[REDACTED_EMAIL]" in (row["reason"] or "")


# ===========================================================================
# 7. Journalisation d'accès (UPN hashés)
# ===========================================================================
def test_access_log_document_et_rag(monkeypatch, tmp_path):
    c = _client_with(monkeypatch, tmp_path, ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true")
    with c:
        r1 = c.post("/access/log", json={
            "event": "document_accessed", "user_id": "u@corp.fr", "document_id": "doc-42",
        })
        assert r1.status_code == 200 and r1.json()["event_type"] == "document_accessed"
        r2 = c.post("/access/log", json={
            "event": "rag_search_executed", "user_id": "u@corp.fr", "query": "secret nom client",
        })
        assert r2.status_code == 200 and r2.json()["event_type"] == "rag_search_executed"
        r3 = c.post("/access/log", json={"event": "nope"})
        assert r3.status_code == 400
        # Aucune donnée en clair en base (UPN hashé, requête non stockée).
        summary = c.get("/usage/summary").json()
        assert summary["by_type"].get("document_accessed", 0) >= 1
        assert summary["by_type"].get("rag_search_executed", 0) >= 1
    import sqlite3

    import app.admin_state as admin_state

    with sqlite3.connect(admin_state.db_path()) as conn:
        rows = conn.execute("SELECT user_id_hash FROM usage_events").fetchall()
    assert all("@" not in (r[0] or "") for r in rows)  # jamais d'UPN en clair


# ===========================================================================
# 8. Rétention TTL + effacement ciblé (art. 17)
# ===========================================================================
def test_retention_purge_par_age(monkeypatch, tmp_path):
    c = _client_with(monkeypatch, tmp_path, ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true")
    with c:
        # Crée un événement RÉCENT et insère un événement ANCIEN directement.
        c.post("/usage", json={"event_type": "message_sent", "user_id": "u@corp.fr"})
        import sqlite3

        import app.admin_state as admin_state

        with sqlite3.connect(admin_state.db_path()) as conn:
            conn.execute(
                "INSERT INTO usage_events(event_id, timestamp_utc, event_type, status)"
                " VALUES('old-1','2000-01-01T00:00:00Z','message_sent','ok')"
            )
            conn.commit()
        # Purge TTL 365 j -> supprime l'ancien, garde le récent.
        res = c.post("/admin/retention/purge", json={"days": 365}).json()
        assert res["deleted_usage_events"] >= 1
        with sqlite3.connect(admin_state.db_path()) as conn:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM usage_events WHERE event_id='old-1'"
            ).fetchone()[0]
        assert remaining == 0


def test_effacement_cible_sujet_art17(monkeypatch, tmp_path):
    c = _client_with(monkeypatch, tmp_path, ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true")
    with c:
        # Deux sujets : on efface l'un, l'autre survit.
        c.post("/usage", json={"event_type": "message_sent", "user_id": "alice@corp.fr"})
        c.post("/usage", json={"event_type": "message_sent", "user_id": "bob@corp.fr"})
        c.post("/tasks", json={"title": "t-alice", "client_id": "alice@corp.fr"})
        res = c.post("/admin/retention/erase", json={"subject_id": "alice@corp.fr"}).json()
        assert res["deleted_usage_events"] >= 1
        assert res["deleted_tasks"] >= 1

        import sqlite3

        import app.admin_state as admin_state

        h_alice = admin_state.hash_id("alice@corp.fr")
        h_bob = admin_state.hash_id("bob@corp.fr")
        with sqlite3.connect(admin_state.db_path()) as conn:
            n_alice = conn.execute(
                "SELECT COUNT(*) FROM usage_events WHERE user_id_hash=?", (h_alice,)
            ).fetchone()[0]
            n_bob = conn.execute(
                "SELECT COUNT(*) FROM usage_events WHERE user_id_hash=?", (h_bob,)
            ).fetchone()[0]
        assert n_alice == 0 and n_bob >= 1


def test_erase_par_hash_seul(monkeypatch, tmp_path):
    c = _client_with(monkeypatch, tmp_path, ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true")
    with c:
        c.post("/usage", json={"event_type": "message_sent", "user_id": "carol@corp.fr"})
        import app.admin_state as admin_state

        h = admin_state.hash_id("carol@corp.fr")
        res = c.post("/admin/retention/erase", json={"subject_hash": h}).json()
        assert res["subject_hash"] == h and res["deleted_usage_events"] >= 1


# ===========================================================================
# 9. Fail-closed sur flag inconnu
# ===========================================================================
def test_flag_inconnu_fail_closed(monkeypatch, tmp_path):
    """Un flag à valeur inconnue (typo) DÉSACTIVE la fonction (fail-closed)."""
    c = _client_with(
        monkeypatch, tmp_path,
        ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true", ONIX_AUDIT_ENABLED="enabledd",  # typo
    )
    with c:
        r = c.post("/audit", json={
            "document": {"nom_client": "ACME"}, "reference": {"nom_client": "ACME"},
        })
        assert r.status_code == 403  # flag inconnu -> coupé


def test_flag_valide_reste_ouvert(monkeypatch, tmp_path):
    c = _client_with(
        monkeypatch, tmp_path,
        ONIX_ACTIONS_ADMIN_KEY_OPTIONAL="true", ONIX_AUDIT_ENABLED="true",
    )
    with c:
        r = c.post("/audit", json={
            "document": {"nom_client": "ACME"}, "reference": {"nom_client": "ACME"},
        })
        assert r.status_code == 200


# ===========================================================================
# 10. Effacement / purge RGPD en mode S3 (art. 17 exhaustif en HA)
# ===========================================================================
class _FakeS3Client:
    """Client S3 minimal en mémoire (clé -> (corps, LastModified)). Implémente le
    sous-ensemble boto3 utilisé par objstore (list_objects_v2 paginé simplifié +
    delete_objects). Permet de PROUVER la suppression S3 sans MinIO réel."""

    def __init__(self, objects):
        # objects : dict {key: datetime|None}
        self.store = dict(objects)

    def list_objects_v2(self, Bucket, Prefix="", ContinuationToken=None):
        contents = [
            {"Key": k, "LastModified": lm}
            for k, lm in self.store.items()
            if k.startswith(Prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}

    def delete_objects(self, Bucket, Delete):
        for obj in Delete.get("Objects", []):
            self.store.pop(obj["Key"], None)
        return {"Deleted": Delete.get("Objects", [])}


def _import_retention(monkeypatch, tmp_path, fake_client):
    """Recharge objstore+retention en mode S3 et injecte le faux client S3."""
    monkeypatch.setenv("ONIX_OBJECT_STORE", "s3")
    monkeypatch.setenv("ONIX_S3_BUCKET", "test-bucket")
    monkeypatch.setenv("ONIX_ACTIONS_DB", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("ONIX_JOBS_DIR", str(tmp_path / "jobs"))

    import app.objstore as objstore
    import app.retention as retention

    importlib.reload(objstore)
    importlib.reload(retention)
    # Court-circuite la fabrique de client boto3 par notre faux client en mémoire.
    monkeypatch.setattr(objstore, "_client", lambda: fake_client)
    return objstore, retention


def test_objstore_delete_subject_docx_supprime_les_bons_objets(monkeypatch, tmp_path):
    """delete_subject_docx supprime UNIQUEMENT les .docx du sujet visé."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    fake = _FakeS3Client({
        "jobs/job-a/Fiche_RDV_Alice_Martin.docx": now,
        "jobs/job-b/Fiche_RDV_Bob_Durand.docx": now,
        "jobs/job-c/Fiche_RDV_Alice_Martin.docx": now,  # 2e fiche du même sujet
    })
    objstore, _ = _import_retention(monkeypatch, tmp_path, fake)

    deleted = objstore.delete_subject_docx("alice_martin")
    assert deleted == 2
    # Les fiches d'Alice ont disparu ; celle de Bob est intacte.
    assert "jobs/job-b/Fiche_RDV_Bob_Durand.docx" in fake.store
    assert not any("Alice_Martin" in k for k in fake.store)


def test_erase_subject_efface_les_objets_s3(monkeypatch, tmp_path):
    """erase_subject (art. 17) BRANCHE bien la suppression S3 quand le store est
    actif : l'objet S3 du sujet est supprimé et compté dans le résultat."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    fake = _FakeS3Client({
        "jobs/job-1/Fiche_RDV_Alice_Martin.docx": now,
        "jobs/job-2/Fiche_RDV_Bob_Durand.docx": now,
    })
    _, retention = _import_retention(monkeypatch, tmp_path, fake)

    res = retention.erase_subject(subject_id="Alice Martin")
    assert res["erased_s3_objects"] == 1
    assert "jobs/job-1/Fiche_RDV_Alice_Martin.docx" not in fake.store
    assert "jobs/job-2/Fiche_RDV_Bob_Durand.docx" in fake.store  # autre sujet préservé


def test_purge_by_age_supprime_les_objets_s3_perimes(monkeypatch, tmp_path):
    """purge_by_age (TTL) supprime les objets S3 plus vieux que la rétention et
    conserve les récents."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=400)
    fake = _FakeS3Client({
        "jobs/job-old/Fiche_RDV_X.docx": old,
        "jobs/job-new/Fiche_RDV_Y.docx": now,
    })
    _, retention = _import_retention(monkeypatch, tmp_path, fake)

    res = retention.purge_by_age(days=365)
    assert res["deleted_s3_objects"] == 1
    assert "jobs/job-old/Fiche_RDV_X.docx" not in fake.store
    assert "jobs/job-new/Fiche_RDV_Y.docx" in fake.store


def test_erase_subject_local_ne_touche_pas_s3(monkeypatch, tmp_path):
    """En mode local (défaut), erase_subject ne tente AUCUNE opération S3
    (fail-safe : le compteur S3 reste à 0)."""
    monkeypatch.delenv("ONIX_OBJECT_STORE", raising=False)
    monkeypatch.setenv("ONIX_ACTIONS_DB", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("ONIX_JOBS_DIR", str(tmp_path / "jobs"))

    import app.objstore as objstore
    import app.retention as retention

    importlib.reload(objstore)
    importlib.reload(retention)
    assert objstore.is_s3() is False

    res = retention.erase_subject(subject_id="alice@corp.fr")
    assert res["erased_s3_objects"] == 0
