"""test_finops_tokens — FinOps : comptes de tokens MESURÉS vs ESTIMÉS.

Vérifie que l'on capture les VRAIS comptes de tokens renvoyés par Ollama
(`prompt_eval_count` / `eval_count`) au lieu d'une heuristique chars/4, et que le
flag `measured` permet au FinOps de distinguer le ground truth de l'estimation.

Mocks : on simule la réponse `/api/generate` d'Ollama via monkeypatch de
`httpx.post` (même style que test_integration_paths.py), sans aucun réseau.
"""
from __future__ import annotations

import pytest


class _OllamaResp:
    """Fausse réponse httpx d'Ollama (`/api/generate`, stream=false)."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


# --- Unitaire : parsing des compteurs Ollama -------------------------------


def test_usage_from_ollama_capture_les_vrais_comptes():
    """Les champs ground truth d'Ollama sont captés tels quels + tokens/s dérivé."""
    from app.llm import usage_from_ollama

    # Exemple calqué sur la doc Ollama (docs/api.md) : durées en NANOSECONDES.
    usage = usage_from_ollama({
        "response": "ok",
        "done": True,
        "prompt_eval_count": 26,
        "eval_count": 259,
        "prompt_eval_duration": 130079000,
        "eval_duration": 4232710000,
        "total_duration": 10706818083,
    })
    assert usage is not None
    assert usage.measured is True
    assert usage.input_tokens == 26      # prompt_eval_count
    assert usage.output_tokens == 259    # eval_count
    assert usage.eval_duration_ns == 4232710000
    assert usage.total_duration_ns == 10706818083
    # 259 tokens / 4.23271 s ~= 61.2 tok/s (signal de perf RÉEL).
    assert usage.eval_tokens_per_second == pytest.approx(61.19, abs=0.5)


def test_usage_from_ollama_none_si_compteurs_absents():
    """Réponse sans compteurs (vieille version / partielle) -> None (=> estimation)."""
    from app.llm import usage_from_ollama

    assert usage_from_ollama({"response": "ok", "done": True}) is None
    assert usage_from_ollama({"prompt_eval_count": 5}) is None  # eval_count manquant
    assert usage_from_ollama("pas un dict") is None


def test_estimate_tokens_heuristique():
    from app.llm import estimate_tokens

    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1          # 4 chars / 4
    assert estimate_tokens("a" * 400) == 100


# --- extract_fields_llm_with_usage : chemin MESURÉ -------------------------


def test_extract_with_usage_mesure_les_tokens_reels(monkeypatch):
    """Quand Ollama renvoie les compteurs, on RÉCUPÈRE les vrais tokens
    (pas chars/4) et measured=True."""
    import app.llm as llm

    payload = {
        "response": '{"nom_client": "ACME SAS", "numero_contrat": "CTR-1"}',
        "done": True,
        "prompt_eval_count": 123,
        "eval_count": 45,
        "eval_duration": 1_000_000_000,  # 1 s
    }
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _OllamaResp(payload))

    fields, usage = llm.extract_fields_llm_with_usage("un texte d'entrée quelconque")
    assert fields == {"nom_client": "ACME SAS", "numero_contrat": "CTR-1"}
    assert usage.measured is True
    assert usage.input_tokens == 123
    assert usage.output_tokens == 45
    # 45 tokens en 1 s -> 45 tok/s.
    assert usage.eval_tokens_per_second == pytest.approx(45.0, abs=0.1)
    # GROUND TRUTH : différent de l'estimation chars/4 du texte d'entrée.
    assert usage.input_tokens != llm.estimate_tokens("un texte d'entrée quelconque")


def test_extract_with_usage_repli_estimation_si_compteurs_absents(monkeypatch):
    """Ollama répond SANS compteurs -> extraction OK mais tokens ESTIMÉS
    (measured=False), pour ne pas mentir sur le coût."""
    import app.llm as llm

    payload = {"response": '{"nom_client": "ACME SAS"}', "done": True}
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _OllamaResp(payload))

    fields, usage = llm.extract_fields_llm_with_usage("texte")
    assert fields == {"nom_client": "ACME SAS"}
    assert usage.measured is False
    assert usage.output_tokens == llm.estimate_tokens('{"nom_client": "ACME SAS"}')


def test_extract_fields_llm_compat_ascendante(monkeypatch):
    """L'API historique `extract_fields_llm` renvoie TOUJOURS juste les champs."""
    import app.llm as llm

    payload = {
        "response": '{"nom_client": "ACME SAS"}',
        "prompt_eval_count": 10, "eval_count": 3,
    }
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _OllamaResp(payload))
    out = llm.extract_fields_llm("texte")
    assert out == {"nom_client": "ACME SAS"}  # dict, pas un tuple


# --- cost_tracker : flag measured -------------------------------------------


def test_estimate_cost_porte_le_flag_measured():
    from app.cost_tracker import estimate_cost

    measured = estimate_cost("llm_token_input", 100, unit="token", measured=True)
    assert measured["measured"] is True
    estimated = estimate_cost("llm_token_output", 50, unit="token")
    assert estimated["measured"] is False  # défaut


# --- Bout-en-bout via /audit : usage_tracker reçoit les vrais comptes -------


def _ref():
    return {"nom_client": "ACME SAS", "numero_contrat": "CTR-2024-001"}


def test_audit_llm_enregistre_tokens_mesures(client, monkeypatch):
    """/audit use_llm=true (Ollama OK) -> /usage/summary et /cost exposent des
    tokens MESURÉS (issus de eval_count) et measured_events>=1."""
    import app.llm as llm

    def _fake(text, **k):
        return (
            {"nom_client": "ACME SAS", "numero_contrat": "CTR-2024-001"},
            llm.LLMUsage(input_tokens=128, output_tokens=64, measured=True),
        )

    monkeypatch.setattr(llm, "extract_fields_llm_with_usage", _fake)
    r = client.post("/audit", json={
        "text": "texte libre sans structure", "use_llm": True, "reference": _ref(),
    })
    assert r.status_code == 200
    assert r.json()["_extraction_mode"] == "llm"

    summary = client.get("/usage/summary").json()
    # Les comptes RÉELS sont enregistrés (et NON une estimation chars/4).
    assert summary["estimated_tokens_input"] == 128
    assert summary["estimated_tokens_output"] == 64
    tok = summary["tokens"]
    assert tok["measured_input"] == 128
    assert tok["measured_output"] == 64
    assert tok["estimated_input"] == 0
    assert tok["measured_events"] >= 1

    cost = client.get("/cost").json()
    assert cost["tokens"]["measured_output"] == 64


def test_audit_llm_valorise_le_cout_avec_rate_card(client, monkeypatch):
    """Le coût LLM est valorisé via les centres llm_token_input/output (€/token)
    à partir des VRAIS comptes."""
    import app.llm as llm

    monkeypatch.setenv("ONIX_RATE_CARD", '{"llm_token_input": 0.01, "llm_token_output": 0.02}')

    def _fake(text, **k):
        return ({"nom_client": "ACME SAS", "numero_contrat": "CTR-2024-001"},
                llm.LLMUsage(input_tokens=100, output_tokens=10, measured=True))

    monkeypatch.setattr(llm, "extract_fields_llm_with_usage", _fake)
    r = client.post("/audit", json={
        "text": "texte libre", "use_llm": True, "reference": _ref(),
    })
    assert r.status_code == 200
    # 100*0.01 + 10*0.02 = 1.20 €
    spent = client.get("/cost").json()["spent_eur"]
    assert spent == pytest.approx(1.20, abs=1e-6)


def test_audit_repli_heuristique_enregistre_tokens_estimes(client, monkeypatch):
    """/audit use_llm=true mais Ollama KO -> repli heuristique : tokens ESTIMÉS
    (measured=False), measured_events reste à 0 (aucun ground truth)."""
    import app.llm as llm

    def _boom(*a, **k):
        raise RuntimeError("Ollama indisponible: down")

    monkeypatch.setattr(llm, "extract_fields_llm_with_usage", _boom)
    text = ("Raison sociale: ACME SAS\nNuméro de contrat: CTR-2024-001\n")
    r = client.post("/audit", json={"text": text, "use_llm": True, "reference": _ref()})
    assert r.status_code == 200
    assert r.json()["_extraction_mode"] == "heuristic"

    tok = client.get("/usage/summary").json()["tokens"]
    # Estimation chars/4 du texte d'entrée -> compté côté ESTIMÉ, pas MESURÉ.
    assert tok["measured_events"] == 0
    assert tok["estimated_input"] >= 1
    assert tok["measured_input"] == 0


def test_usage_endpoint_accepte_le_flag_measured(client):
    """POST /usage avec measured=true -> /usage/summary le ventile en MESURÉ."""
    r = client.post("/usage", json={
        "event_type": "message_received", "estimated_tokens_output": 30, "measured": True,
    })
    assert r.status_code == 200
    tok = client.get("/usage/summary").json()["tokens"]
    assert tok["measured_output"] == 30
    assert tok["measured_events"] >= 1
