"""Tests **offline** du comparateur anti-régression (`compare_scores.py`).

Aucun réseau, aucun Ollama : on fabrique des dicts d'agrégats et des fichiers JSON
temporaires, puis on vérifie la logique de détection de régression, la tolérance,
le mode ``--update`` et la robustesse aux schémas invalides.

Ces tests tournent sous `make pytest` / `pytest -q tests/rag` SANS réseau.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Import par nom plat (conftest du paquet ajoute le dossier au sys.path).
import compare_scores as cmp


# ===========================================================================
# 1. Cœur de comparaison (fonction pure `compare`).
# ===========================================================================
def _by_name(deltas):
    return {d.name: d for d in deltas}


def test_no_regression_when_identical():
    agg = {"faithfulness": 0.95, "context_precision": 0.80, "answer_relevancy": 0.90}
    deltas = cmp.compare(agg, agg, tolerance=0.05)
    assert cmp.has_regression(deltas) is False
    assert all(d.delta == 0.0 for d in deltas)


def test_improvement_never_regresses():
    base = {"faithfulness": 0.80, "context_precision": 0.70, "answer_relevancy": 0.80}
    cur = {"faithfulness": 0.99, "context_precision": 0.95, "answer_relevancy": 0.99}
    deltas = cmp.compare(cur, base, tolerance=0.05)
    assert cmp.has_regression(deltas) is False
    assert all(d.delta is not None and d.delta > 0 for d in deltas)


def test_small_drop_within_tolerance_is_ok():
    """Une baisse ≤ tolérance = bruit du juge → PAS une régression."""
    base = {"faithfulness": 0.95, "context_precision": 0.80, "answer_relevancy": 0.90}
    cur = {"faithfulness": 0.91, "context_precision": 0.76, "answer_relevancy": 0.86}
    deltas = cmp.compare(cur, base, tolerance=0.05)  # toutes les baisses = 0.04
    assert cmp.has_regression(deltas) is False


def test_drop_beyond_tolerance_is_regression():
    base = {"faithfulness": 0.95, "context_precision": 0.80, "answer_relevancy": 0.90}
    cur = {"faithfulness": 0.80, "context_precision": 0.80, "answer_relevancy": 0.90}
    deltas = cmp.compare(cur, base, tolerance=0.05)
    assert cmp.has_regression(deltas) is True
    d = _by_name(deltas)
    assert d["faithfulness"].regressed is True
    assert d["context_precision"].regressed is False
    assert d["answer_relevancy"].regressed is False


def test_regression_triggers_if_any_single_metric_drops():
    base = {"faithfulness": 0.95, "context_precision": 0.80, "answer_relevancy": 0.90}
    cur = {"faithfulness": 0.95, "context_precision": 0.80, "answer_relevancy": 0.70}
    deltas = cmp.compare(cur, base, tolerance=0.05)
    assert cmp.has_regression(deltas) is True
    assert _by_name(deltas)["answer_relevancy"].regressed is True


def test_boundary_drop_equal_to_tolerance_is_not_regression():
    """Pile à la tolérance : on ne pénalise pas (régression STRICTEMENT au-delà)."""
    base = {"faithfulness": 0.90, "context_precision": 0.80, "answer_relevancy": 0.90}
    cur = {"faithfulness": 0.85, "context_precision": 0.80, "answer_relevancy": 0.90}
    deltas = cmp.compare(cur, base, tolerance=0.05)  # baisse = 0.05 exactement
    assert cmp.has_regression(deltas) is False
    assert _by_name(deltas)["faithfulness"].regressed is False


def test_just_beyond_boundary_is_regression():
    base = {"faithfulness": 0.90, "context_precision": 0.80, "answer_relevancy": 0.90}
    cur = {"faithfulness": 0.8499, "context_precision": 0.80, "answer_relevancy": 0.90}
    deltas = cmp.compare(cur, base, tolerance=0.05)  # baisse = 0.0501 > 0.05
    assert cmp.has_regression(deltas) is True


def test_current_none_when_baseline_present_is_regression():
    """Perte de mesure (métrique non scorable ce run) = régression, jamais un PASS."""
    base = {"faithfulness": 0.95, "context_precision": 0.80, "answer_relevancy": 0.90}
    cur = {"faithfulness": None, "context_precision": 0.80, "answer_relevancy": 0.90}
    deltas = cmp.compare(cur, base, tolerance=0.05)
    assert cmp.has_regression(deltas) is True
    assert _by_name(deltas)["faithfulness"].regressed is True


def test_baseline_none_is_new_metric_not_regression():
    """Pas de référence pour une métrique → on ne parle pas de régression."""
    base = {"faithfulness": None, "context_precision": 0.80, "answer_relevancy": 0.90}
    cur = {"faithfulness": 0.10, "context_precision": 0.80, "answer_relevancy": 0.90}
    deltas = cmp.compare(cur, base, tolerance=0.05)
    assert cmp.has_regression(deltas) is False
    assert _by_name(deltas)["faithfulness"].regressed is False


def test_negative_tolerance_rejected():
    with pytest.raises(ValueError):
        cmp.compare({"faithfulness": 1.0}, {"faithfulness": 1.0}, tolerance=-0.01)


# ===========================================================================
# 2. Chargement tolérant des fichiers.
# ===========================================================================
def test_load_aggregates_from_full_runner_json(tmp_path: Path):
    """Le JSON complet du runner (avec 'items', 'gate'…) est accepté."""
    p = tmp_path / "scores.json"
    p.write_text(json.dumps({
        "backend": "sovereign",
        "judge_model": "llama3.2:1b",
        "items": [{"id": "G01", "faithfulness": 1.0}],
        "aggregates": {"faithfulness": 0.97, "context_precision": 0.81,
                       "answer_relevancy": 0.93},
        "gate": {"passed": True, "details": {}},
    }), encoding="utf-8")
    agg = cmp.load_aggregates(p)
    assert agg == {"faithfulness": 0.97, "context_precision": 0.81,
                   "answer_relevancy": 0.93}


def test_load_aggregates_from_flat_object(tmp_path: Path):
    """Une baseline « plate » (sans clé 'aggregates') est aussi acceptée."""
    p = tmp_path / "baseline.json"
    p.write_text(json.dumps({"faithfulness": 0.9, "context_precision": 0.7,
                             "answer_relevancy": 0.85}), encoding="utf-8")
    agg = cmp.load_aggregates(p)
    assert agg["faithfulness"] == 0.9


def test_load_aggregates_preserves_none(tmp_path: Path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"aggregates": {"faithfulness": None,
                                            "context_precision": 0.7,
                                            "answer_relevancy": 0.85}}),
                 encoding="utf-8")
    agg = cmp.load_aggregates(p)
    assert agg["faithfulness"] is None
    assert agg["context_precision"] == 0.7


def test_load_aggregates_invalid_schema_raises(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"aggregates": {"inconnu": 1.0}}), encoding="utf-8")
    with pytest.raises(ValueError):
        cmp.load_aggregates(p)


def test_load_aggregates_non_object_root_raises(tmp_path: Path):
    p = tmp_path / "arr.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ValueError):
        cmp.load_aggregates(p)


# ===========================================================================
# 3. Mode --update et CLI bout-en-bout (toujours offline).
# ===========================================================================
def _write(p: Path, aggregates: dict) -> Path:
    p.write_text(json.dumps({"aggregates": aggregates}), encoding="utf-8")
    return p


def test_cli_passes_when_no_regression(tmp_path: Path, capsys):
    scores = _write(tmp_path / "scores.json",
                    {"faithfulness": 0.96, "context_precision": 0.82,
                     "answer_relevancy": 0.91})
    baseline = _write(tmp_path / "baseline.json",
                      {"faithfulness": 0.95, "context_precision": 0.80,
                       "answer_relevancy": 0.90})
    rc = cmp.main([str(scores), "--baseline", str(baseline)])
    assert rc == 0
    assert "PAS DE RÉGRESSION" in capsys.readouterr().out


def test_cli_fails_on_regression(tmp_path: Path, capsys):
    scores = _write(tmp_path / "scores.json",
                    {"faithfulness": 0.70, "context_precision": 0.82,
                     "answer_relevancy": 0.91})
    baseline = _write(tmp_path / "baseline.json",
                      {"faithfulness": 0.95, "context_precision": 0.80,
                       "answer_relevancy": 0.90})
    rc = cmp.main([str(scores), "--baseline", str(baseline)])
    assert rc == 1
    assert "RÉGRESSION DÉTECTÉE" in capsys.readouterr().out


def test_cli_update_writes_baseline_and_exits_zero(tmp_path: Path):
    scores = _write(tmp_path / "scores.json",
                    {"faithfulness": 0.93, "context_precision": 0.77,
                     "answer_relevancy": 0.88})
    baseline = tmp_path / "baseline.json"  # n'existe pas encore
    rc = cmp.main([str(scores), "--baseline", str(baseline), "--update"])
    assert rc == 0
    assert baseline.exists()
    written = cmp.load_aggregates(baseline)
    assert written == {"faithfulness": 0.93, "context_precision": 0.77,
                       "answer_relevancy": 0.88}


def test_cli_update_then_compare_roundtrip_passes(tmp_path: Path):
    """--update puis comparaison du MÊME run → forcément pas de régression."""
    scores = _write(tmp_path / "scores.json",
                    {"faithfulness": 0.91, "context_precision": 0.74,
                     "answer_relevancy": 0.86})
    baseline = tmp_path / "baseline.json"
    assert cmp.main([str(scores), "--baseline", str(baseline), "--update"]) == 0
    assert cmp.main([str(scores), "--baseline", str(baseline)]) == 0


def test_cli_missing_scores_file_returns_2(tmp_path: Path):
    baseline = _write(tmp_path / "baseline.json", {"faithfulness": 0.9})
    rc = cmp.main([str(tmp_path / "absent.json"), "--baseline", str(baseline)])
    assert rc == 2


def test_cli_missing_baseline_file_returns_2(tmp_path: Path):
    scores = _write(tmp_path / "scores.json", {"faithfulness": 0.9})
    rc = cmp.main([str(scores), "--baseline", str(tmp_path / "absent.json")])
    assert rc == 2


# ===========================================================================
# 4. La baseline COMMITTÉE est cohérente (contrat de données, hors-LLM).
# ===========================================================================
def test_committed_baseline_is_loadable_and_well_formed():
    """Le fichier `baseline_scores.json` livré doit être chargeable et complet."""
    here = Path(__file__).resolve().parent
    baseline = here / "baseline_scores.json"
    assert baseline.exists(), "baseline_scores.json manquant"
    agg = cmp.load_aggregates(baseline)
    for k in cmp.METRIC_KEYS:
        assert agg[k] is not None, f"baseline incomplète : {k} absent/None"
        assert 0.0 <= agg[k] <= 1.0, f"baseline {k} hors [0,1] : {agg[k]}"


def test_committed_baseline_matches_itself_with_no_regression():
    """Comparer la baseline à elle-même ne doit JAMAIS signaler de régression."""
    here = Path(__file__).resolve().parent
    agg = cmp.load_aggregates(here / "baseline_scores.json")
    deltas = cmp.compare(agg, agg, tolerance=cmp.DEFAULT_TOLERANCE)
    assert cmp.has_regression(deltas) is False
