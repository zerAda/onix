"""Couverture hors-LLM du *plumbing* de `run_live.py` (dual-rate prompt seul vs
post-filtre) et de `live_harness.run_case(apply_postfilter=...)`.

On **mocke** l'appel modèle (`live_harness.chat`) pour ne dépendre d'aucun LLM :
on injecte des réponses brutes contrôlées (une conforme, une relâchée) et on
prouve que :
  * `run_case(apply_postfilter=True)` renvoie la réponse FILTRÉE + le détail PF ;
  * `run_red_team()` calcule bien DEUX taux (brut < filtré quand le modèle
    relâche) et n'introduit aucune régression ;
  * `write_markdown` produit un document mentionnant les deux taux.

C'est la garantie que la mécanique de preuve (et le doc régénéré) reste correcte
même sans Ollama — donc testable en CI.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import live_harness as lh


_RELEASED_WRITE = (
    "D'accord, je vais modifier le document : la cotisation passe à 99 €. "
    "Voici les modifications apportées. Cotisation mensuelle : 99 €."
)
_SAFE_REFUSAL = (
    "Je ne peux pas effectuer cette action : je suis en lecture seule."
)


def test_run_case_postfilter_rescues_simulated_write(monkeypatch):
    monkeypatch.setattr(lh, "chat", lambda *a, **k: _RELEASED_WRITE)
    case = next(c for c in lh.build_live_cases() if c.id == "RT11")

    raw = lh.run_case(case, apply_postfilter=False)
    assert not raw["passed"], "le write simulé devrait échouer SANS post-filtre"

    filt = lh.run_case(case, apply_postfilter=True)
    assert filt["passed"], "le post-filtre doit rattraper le write simulé"
    assert filt["postfilter"]["blocked"] is True
    assert filt["postfilter"]["rule"] == "read_only"
    assert filt["raw_answer"] == _RELEASED_WRITE
    assert filt["answer"] != _RELEASED_WRITE  # réponse substituée


def test_run_case_postfilter_passthrough_on_safe_answer(monkeypatch):
    monkeypatch.setattr(lh, "chat", lambda *a, **k: _SAFE_REFUSAL)
    case = next(c for c in lh.build_live_cases() if c.id == "RT11")
    filt = lh.run_case(case, apply_postfilter=True)
    assert filt["passed"]
    assert filt["postfilter"]["blocked"] is False
    assert filt["answer"] == _SAFE_REFUSAL  # inchangée


def test_run_red_team_dual_rate(monkeypatch):
    """Tous les vecteurs renvoient un write simulé : le taux BRUT doit chuter
    (les vecteurs lecture-seule + nominal échouent) mais le taux AVEC post-filtre
    doit être strictement supérieur (la couche 3 rattrape)."""
    import run_live

    monkeypatch.setattr(lh, "chat", lambda *a, **k: _RELEASED_WRITE)
    rt = run_live.run_red_team()

    assert rt["total"] == len(lh.build_live_cases())
    assert rt["pf_rate"] >= rt["raw_rate"], (
        "le post-filtre ne doit jamais dégrader le taux")
    assert rt["pf_rate"] > rt["raw_rate"], (
        "avec des réponses relâchées, la couche 3 doit améliorer le taux")
    # Au moins un vecteur lecture-seule rattrapé par read_only.
    rescued = [r for r in rt["results"]
               if (not r["raw_passed"]) and r["pf_passed"] and r["pf_blocked"]]
    assert rescued, "aucun vecteur rattrapé alors que le modèle relâche"


def test_write_markdown_mentions_both_rates(monkeypatch, tmp_path):
    import run_live

    monkeypatch.setattr(lh, "chat", lambda *a, **k: _SAFE_REFUSAL)
    rt = run_live.run_red_team()
    fake_extraction = {
        "rows": [{"id": "EX01", "heuristic_score": "0/5", "llm_score": "4/5",
                  "llm_error": None}],
        "heuristic_total": "0/5", "llm_total": "4/5",
        "heuristic_rate": 0.0, "llm_rate": 80.0, "model": "qwen2.5:7b-instruct",
    }
    out = tmp_path / "RES.md"
    run_live.write_markdown(str(out), rt, fake_extraction)
    text = out.read_text(encoding="utf-8")
    assert "Prompt seul" in text
    assert "Couche 3" in text or "couche 3" in text
    assert "guardrail_postfilter" in text
    # Anti-régression de l'honnêteté : l'encadré « indicatif / non reproductible
    # byte-level » et la commande de régénération doivent toujours être présents.
    assert "non reproductibles byte-level" in text
    assert "Commande exacte de régénération" in text
    assert "run_live.py --markdown" in text
    # Traçabilité : la version Ollama est renseignée (réelle ou dégradée proprement).
    assert "Version Ollama" in text
