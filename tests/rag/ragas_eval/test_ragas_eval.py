"""Tests **offline** du harnais d'évaluation RAGAS souveraine.

Aucun réseau, aucun Ollama : on **injecte un juge scripté** (``ScriptedJudge``)
qui imite un LLM-juge en se basant sur le contenu réel des prompts (présence des
chiffres dans le contexte, mention du sujet dans les chunks…). La discrimination
des métriques **émerge donc des données**, comme avec un vrai juge — elle n'est
pas codée en dur item par item.

Couverture exigée par la recette :
  * la mathématique des métriques est correcte sur des cas fabriqués à la main ;
  * l'item halluciné (G07) obtient une **faithfulness basse** ;
  * l'item à contexte hors-sujet (G08) obtient une **context_precision basse** ;
  * la logique de gate renvoie le bon PASS/FAIL ;
  * l'extraction JSON tolère les sorties *fenced* / bruitées / mal formées.

Ces tests doivent passer sous `make pytest` (et `pytest -q tests/rag`) SANS réseau.
"""
from __future__ import annotations

import json
import re
from typing import List

import pytest

# Imports par nom plat (paquet `ragas_eval` + dossier ajouté au path par runner).
import judge as judge_mod
import metrics as metrics_mod
import runner as runner_mod
from judge import (
    ChunkVerdict,
    ClaimVerdict,
    ItemJudgement,
    extract_json,
)


# ===========================================================================
# Juge scripté (faux LLM) — déterministe, basé sur le CONTENU des prompts.
# ===========================================================================
_NUM_RE = re.compile(r"\d[\d\s.,]*")


def _numbers(text: str) -> List[str]:
    """Suites de chiffres normalisées (espaces/insécables retirés) — pour comparer
    « 142 € » du contexte avec « 142 » d'une affirmation."""
    out = []
    for m in _NUM_RE.findall(text or ""):
        norm = re.sub(r"[\s .,]", "", m)
        if norm:
            out.append(norm)
    return out


class ScriptedJudge:
    """Faux juge : route selon la métrique détectée dans le prompt `user`.

    * faithfulness : découpe la réponse en phrases-claims ; un claim est « étayé »
      si tous ses nombres apparaissent dans le contexte (sinon non étayé) ;
    * context_precision : un chunk est pertinent s'il mentionne le sujet de la
      question (heuristique de recouvrement de mots-clés) ;
    * answer_relevancy : note 4 si la réponse n'est pas vide et reprend un mot-clé
      de la question, sinon 1.
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, system: str, user: str) -> str:
        self.calls += 1
        if user.startswith("Décompose la RÉPONSE") or "affirmations atomiques" in user:
            return self._faithfulness(user)
        if "CHUNKS DE CONTEXTE" in user:
            return self._context_precision(user)
        if "à quel point la RÉPONSE adresse" in user or "Barème" in user:
            return self._answer_relevancy(user)
        return "{}"  # inconnu → objet vide (le runner comptera l'anomalie)

    # -- parsing utilitaire des sections du prompt --
    @staticmethod
    def _section(user: str, start: str, end: str) -> str:
        i = user.find(start)
        if i == -1:
            return ""
        i += len(start)
        j = user.find(end, i)
        return user[i:j] if j != -1 else user[i:]

    def _faithfulness(self, user: str) -> str:
        context = self._section(user, "CONTEXTE :", "RÉPONSE À ÉVALUER :")
        answer = self._section(user, "RÉPONSE À ÉVALUER :", "Réponds par cet objet")
        ctx_nums = set(_numbers(context))
        claims = [c.strip() for c in re.split(r"[.\n]", answer) if c.strip()]
        # Refus honnête → aucune affirmation vérifiable.
        if any(k in answer.lower() for k in ("non disponible", "n'est pas mentionné",
                                             "pas mentionné")):
            return json.dumps({"claims": []}, ensure_ascii=False)
        out = []
        for cl in claims:
            nums = _numbers(cl)
            supported = all(n in ctx_nums for n in nums) if nums else True
            out.append({"claim": cl, "supported": supported, "reason": "auto"})
        return json.dumps({"claims": out}, ensure_ascii=False)

    def _context_precision(self, user: str) -> str:
        question = self._section(user, "QUESTION :", "CHUNKS DE CONTEXTE")
        chunks_blob = self._section(user, "(numérotés) :", "Réponds par cet objet")
        subject_words = self._subject_words(question)
        chunks = re.findall(r"\[(\d+)\]\s*(.*)", chunks_blob)
        out = []
        for idx, body in chunks:
            low = body.lower()
            relevant = any(w in low for w in subject_words)
            out.append({"index": int(idx), "relevant": relevant, "reason": "auto"})
        return json.dumps({"chunks": out}, ensure_ascii=False)

    def _answer_relevancy(self, user: str) -> str:
        question = self._section(user, "QUESTION :", "RÉPONSE :")
        answer = self._section(user, "RÉPONSE :", "Réponds par cet objet")
        words = self._subject_words(question)
        score = 4 if (answer.strip() and any(w in answer.lower() for w in words)) else 1
        return json.dumps({"score": score, "reason": "auto"}, ensure_ascii=False)

    @staticmethod
    def _subject_words(question: str) -> List[str]:
        # Mots-clés du domaine présents dans la question (heuristique simple).
        candidates = ["alpha", "cotisation", "plafond", "hospitalisation",
                      "échéance", "renouvellement", "dossier", "synthèse",
                      "téléphone", "dirigeant", "point d'attention"]
        low = question.lower()
        return [w for w in candidates if w in low] or ["alpha"]


# ===========================================================================
# 1. Extraction JSON robuste.
# ===========================================================================
def test_extract_json_plain():
    assert extract_json('{"score": 3}') == {"score": 3}


def test_extract_json_code_fence():
    raw = "Voici mon évaluation :\n```json\n{\"score\": 4, \"reason\": \"ok\"}\n```\nMerci."
    assert extract_json(raw) == {"score": 4, "reason": "ok"}


def test_extract_json_fence_without_lang():
    raw = "```\n{\"claims\": []}\n```"
    assert extract_json(raw) == {"claims": []}


def test_extract_json_with_leading_and_trailing_prose():
    raw = ('Bien sûr ! Mon analyse est la suivante. {"chunks": '
           '[{"index": 0, "relevant": true}]} Voilà, j\'espère que cela aide.')
    assert extract_json(raw) == {"chunks": [{"index": 0, "relevant": True}]}


def test_extract_json_nested_braces_and_strings():
    raw = 'bla {"a": {"b": 1}, "s": "texte avec } accolade"} fin'
    assert extract_json(raw) == {"a": {"b": 1}, "s": "texte avec } accolade"}


def test_extract_json_returns_none_on_garbage():
    assert extract_json("aucun json ici, juste du texte") is None
    assert extract_json("") is None
    assert extract_json("[1, 2, 3]") is None  # tableau, pas un objet


def test_as_bool_tolerant():
    assert judge_mod._as_bool(True) is True
    assert judge_mod._as_bool("oui") is True
    assert judge_mod._as_bool("non") is False
    assert judge_mod._as_bool("pertinent") is True
    assert judge_mod._as_bool(1) is True
    assert judge_mod._as_bool("peut-être") is None


# ===========================================================================
# 2. Mathématique des métriques (déterministe, sur cas fabriqués).
# ===========================================================================
def test_faithfulness_math():
    j = ItemJudgement(claims=[
        ClaimVerdict("a", True), ClaimVerdict("b", True),
        ClaimVerdict("c", False), ClaimVerdict("d", True),
    ])
    assert metrics_mod.score_faithfulness(j) == 0.75


def test_faithfulness_no_claims_is_one():
    """Refus honnête (aucune affirmation) → rien d'hallucinable → 1.0."""
    assert metrics_mod.score_faithfulness(ItemJudgement(claims=[])) == 1.0


def test_context_precision_math():
    j = ItemJudgement(chunks=[
        ChunkVerdict(0, True), ChunkVerdict(1, False), ChunkVerdict(2, False),
    ])
    assert metrics_mod.score_context_precision(j) == pytest.approx(0.3333, abs=1e-4)


def test_context_precision_no_chunk_is_zero():
    assert metrics_mod.score_context_precision(ItemJudgement(chunks=[])) == 0.0


def test_answer_relevancy_math():
    assert metrics_mod.score_answer_relevancy(ItemJudgement(relevancy_score_0_4=3)) == 0.75
    assert metrics_mod.score_answer_relevancy(ItemJudgement(relevancy_score_0_4=4)) == 1.0
    assert metrics_mod.score_answer_relevancy(ItemJudgement(relevancy_score_0_4=0)) == 0.0


def test_answer_relevancy_none_when_unparsed():
    assert metrics_mod.score_answer_relevancy(ItemJudgement(relevancy_score_0_4=None)) is None


def test_aggregate_macro_mean_ignores_none():
    scores = [
        metrics_mod.ItemScores("a", 1.0, 1.0, 1.0),
        metrics_mod.ItemScores("b", 0.0, 0.0, None),
    ]
    agg = metrics_mod.aggregate(scores)
    assert agg["faithfulness"] == 0.5
    assert agg["context_precision"] == 0.5
    assert agg["answer_relevancy"] == 1.0  # le None de 'b' est ignoré


def test_aggregate_all_none_metric_is_none():
    scores = [metrics_mod.ItemScores("a", None, None, None)]
    assert metrics_mod.aggregate(scores)["faithfulness"] is None


# ===========================================================================
# 3. Logique de gate.
# ===========================================================================
def test_gate_pass_when_all_meet_thresholds():
    agg = {"faithfulness": 0.95, "context_precision": 0.80, "answer_relevancy": 0.90}
    res = metrics_mod.evaluate_gate(agg, metrics_mod.GateThresholds())
    assert res.passed is True
    assert all(d["passed"] for d in res.details.values())


def test_gate_fail_on_single_metric():
    agg = {"faithfulness": 0.50, "context_precision": 0.80, "answer_relevancy": 0.90}
    res = metrics_mod.evaluate_gate(agg, metrics_mod.GateThresholds())
    assert res.passed is False
    assert res.details["faithfulness"]["passed"] is False
    assert res.details["context_precision"]["passed"] is True


def test_gate_fail_on_none_metric():
    """Une métrique non scorable (None) ne doit jamais laisser passer le gate."""
    agg = {"faithfulness": None, "context_precision": 0.80, "answer_relevancy": 0.90}
    res = metrics_mod.evaluate_gate(agg, metrics_mod.GateThresholds())
    assert res.passed is False
    assert res.details["faithfulness"]["passed"] is False


def test_gate_boundary_is_inclusive():
    agg = {"faithfulness": 0.90, "context_precision": 0.70, "answer_relevancy": 0.85}
    res = metrics_mod.evaluate_gate(agg, metrics_mod.GateThresholds())
    assert res.passed is True  # >= seuil


# ===========================================================================
# 4. Judge end-to-end avec juge scripté (sans réseau) + robustesse.
# ===========================================================================
def test_judge_item_faithful_answer_scores_high():
    judge = ScriptedJudge()
    j = judge_mod.judge_item(
        question="Quelle est la cotisation mensuelle d'ALPHA ?",
        answer="La cotisation mensuelle d'ALPHA est de 142 € par salarié.",
        contexts=["Cotisation mensuelle : 142 € par salarié. Date d'effet : 01/01/2025."],
        llm=judge,
    )
    s = metrics_mod.score_item("G", j)
    assert s.faithfulness == 1.0
    assert s.errors == []


def test_judge_item_hallucinated_answer_scores_low_faithfulness():
    judge = ScriptedJudge()
    j = judge_mod.judge_item(
        question="Quelle est la cotisation mensuelle d'ALPHA ?",
        answer=("La cotisation d'ALPHA est de 210 € par salarié, en hausse de 12 %, "
                "avec 3000 opticiens partenaires."),
        contexts=["Cotisation mensuelle : 142 € par salarié."],
        llm=judge,
    )
    s = metrics_mod.score_item("G07", j)
    assert s.faithfulness is not None and s.faithfulness < 0.5, (
        f"faithfulness attendue basse, obtenue {s.faithfulness}")


def test_judge_item_irrelevant_context_scores_low_precision():
    judge = ScriptedJudge()
    j = judge_mod.judge_item(
        question="Quel est le plafond hospitalisation du client ALPHA ?",
        answer="Le plafond hospitalisation du contrat ALPHA est de 1 200 €.",
        contexts=[
            "Compte rendu RDV BETA : renégociation prévoyance au prochain trimestre.",
            "Procédure de congés payés : délai de prévenance de deux semaines.",
            "Tendances du marché santé 2025 : téléconsultation.",
        ],
        llm=judge,
    )
    s = metrics_mod.score_item("G08", j)
    assert s.context_precision is not None and s.context_precision < 0.5, (
        f"context_precision attendue basse, obtenue {s.context_precision}")


def test_judge_item_never_crashes_on_bad_llm_output():
    """Un juge qui renvoie n'importe quoi ne doit pas crasher : erreurs comptées."""
    def broken_llm(system: str, user: str) -> str:
        return "Désolé, je n'ai pas compris la demande."  # aucun JSON

    j = judge_mod.judge_item(
        question="Q ?", answer="Une réponse.", contexts=["Un chunk."], llm=broken_llm)
    s = metrics_mod.score_item("BAD", j)
    assert len(s.errors) >= 1
    # faithfulness : pas de claim lisible → 1.0 (refus prudent du juge) ; on
    # vérifie surtout l'absence de crash et le comptage des anomalies.
    assert s.faithfulness == 1.0
    assert s.answer_relevancy is None  # score non lisible


def test_judge_item_on_llm_exception_is_counted_not_raised():
    """Un juge qui LÈVE une exception est rattrapé et compté, pas propagé."""
    def exploding_llm(system: str, user: str) -> str:
        raise RuntimeError("réseau coupé")

    j = judge_mod.judge_item(
        question="Q ?", answer="R.", contexts=["c"], llm=exploding_llm)
    assert any("appel LLM échoué" in e for e in j.errors)


def test_context_precision_fills_missing_chunk_verdicts():
    """Si le juge oublie un chunk, il est compté non pertinent + anomalie remontée."""
    def partial_llm(system: str, user: str) -> str:
        # Ne juge que le chunk 0 alors qu'il y en a 2.
        return '{"chunks": [{"index": 0, "relevant": true}]}'

    errors: List[str] = []
    chunks = judge_mod.judge_context_precision(
        "Q ?", ["chunk A", "chunk B"], partial_llm, errors)
    assert len(chunks) == 2
    assert chunks[1].relevant is False
    assert any("non jugé" in e for e in errors)


# ===========================================================================
# 5. Runner end-to-end sur le VRAI golden set, juge scripté → gate discrimine.
# ===========================================================================
def test_runner_on_golden_set_with_scripted_judge():
    items = runner_mod.load_golden()
    assert len(items) >= 8, "le golden set doit contenir au moins 8 items"

    judge = ScriptedJudge()
    scores, aggregates, gate = runner_mod.evaluate(
        items, backend="sovereign", llm=judge)

    by_id = {s.item_id: s for s in scores}

    # L'item halluciné (G07) : faithfulness basse.
    assert by_id["G07"].faithfulness < 0.5

    # L'item à contexte hors-sujet (G08) : context_precision basse.
    assert by_id["G08"].context_precision < 0.5

    # Les items fidèles et bien contextualisés tiennent.
    assert by_id["G01"].faithfulness == 1.0
    assert by_id["G01"].context_precision >= 0.5

    # Le rapport se construit sans erreur et mentionne le verdict.
    report = runner_mod.build_report(
        scores, aggregates, gate, model="fake", backend="sovereign")
    assert "VERDICT GATE" in report
    assert "faithfulness" in report


def test_runner_json_serialization_roundtrip(tmp_path):
    items = runner_mod.load_golden()
    judge = ScriptedJudge()
    scores, aggregates, gate = runner_mod.evaluate(items, llm=judge)
    payload = runner_mod.to_json(scores, aggregates, gate, model="fake",
                                 backend="sovereign")
    # Sérialisable et relisible.
    text = json.dumps(payload, ensure_ascii=False)
    back = json.loads(text)
    assert back["backend"] == "sovereign"
    assert len(back["items"]) == len(items)
    assert "faithfulness" in back["aggregates"]
    assert "passed" in back["gate"]


def test_runner_gate_fails_when_golden_has_degraded_items():
    """Avec G07 (halluciné) et G08 (hors-sujet) dans le set, les agrégats
    faithfulness/context_precision tombent sous les seuils → gate FAIL.
    C'est la preuve que le gate DISCRIMINE réellement."""
    items = runner_mod.load_golden()
    judge = ScriptedJudge()
    _, aggregates, gate = runner_mod.evaluate(items, llm=judge)
    # Au moins une des deux métriques dégradées passe sous son seuil.
    assert gate.passed is False, (
        f"gate aurait dû échouer avec items dégradés ; agrégats={aggregates}")


def test_runner_gate_passes_on_clean_subset():
    """Sur un sous-ensemble SANS items dégradés, le gate doit PASSER."""
    items = [it for it in runner_mod.load_golden()
             if it["id"] not in {"G07", "G08", "G06"}]
    judge = ScriptedJudge()
    _, aggregates, gate = runner_mod.evaluate(items, llm=judge)
    assert gate.passed is True, f"gate aurait dû passer ; agrégats={aggregates}"


def test_thresholds_from_env(monkeypatch):
    monkeypatch.setenv("ONIX_RAGAS_MIN_FAITHFULNESS", "0.5")
    monkeypatch.setenv("ONIX_RAGAS_MIN_CONTEXT_PRECISION", "0.4")
    monkeypatch.delenv("ONIX_RAGAS_MIN_ANSWER_RELEVANCY", raising=False)
    thr = runner_mod.thresholds_from_env()
    assert thr.faithfulness == 0.5
    assert thr.context_precision == 0.4
    assert thr.answer_relevancy == 0.85  # défaut conservé


def test_thresholds_from_env_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("ONIX_RAGAS_MIN_FAITHFULNESS", "pas-un-nombre")
    thr = runner_mod.thresholds_from_env()
    assert thr.faithfulness == 0.90  # défaut malgré la valeur invalide


# ===========================================================================
# 6. Backend ragas optionnel : dégradation propre (jamais de crash brut).
# ===========================================================================
def test_ragas_backend_degrades_gracefully():
    items = runner_mod.load_golden()
    with pytest.raises(runner_mod.RagasUnavailable):
        runner_mod.evaluate(items, backend="ragas")


# ===========================================================================
# 7. Cohérence du golden set (contrat de données, hors-LLM).
# ===========================================================================
def test_golden_set_shape_and_required_fields():
    items = runner_mod.load_golden()
    ids = set()
    required = {"id", "question", "reference_answer", "retrieved_contexts",
                "answer_to_grade"}
    for it in items:
        assert required.issubset(it.keys()), f"item {it.get('id')} : champs manquants"
        assert isinstance(it["retrieved_contexts"], list)
        assert it["id"] not in ids, f"id dupliqué : {it['id']}"
        ids.add(it["id"])
    # Les cas dégradés exigés par la recette sont présents.
    assert "G07" in ids and "G08" in ids
