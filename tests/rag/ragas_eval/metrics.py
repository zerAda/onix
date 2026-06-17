"""Agrégation **déterministe** des verdicts du juge en scores RAGAS ∈ [0,1].

Séparé de `judge.py` (qui parle au LLM) pour que **toute la mathématique des
métriques soit testable sans réseau** : on lui passe des `ItemJudgement`
fabriqués à la main et on vérifie les scores. Aucune dépendance externe.

Définitions (rappel, détails dans `judge.py` et le README) :

* **faithfulness** = (#claims étayés) / (#claims). Réponse sans claim → 1.0
  (rien d'hallucinable ; un refus honnête n'est pas pénalisé).
* **context_precision** = (#chunks pertinents) / (#chunks). Aucun chunk → 0.0.
* **answer_relevancy** = note(0–4) / 4. Note absente (juge illisible) → None
  (item non scoré sur cette métrique ; remonté, jamais deviné).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from judge import ItemJudgement  # même dossier (paquet), import par nom


@dataclass
class ItemScores:
    """Scores ∈ [0,1] d'un item (``None`` = non scorable, ex. juge illisible)."""
    item_id: str
    faithfulness: Optional[float]
    context_precision: Optional[float]
    answer_relevancy: Optional[float]
    n_claims: int = 0
    n_supported: int = 0
    n_chunks: int = 0
    n_relevant: int = 0
    errors: List[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def score_faithfulness(j: ItemJudgement) -> Optional[float]:
    """(#claims étayés)/(#claims) ; pas de claim → 1.0 (rien d'hallucinable)."""
    if not j.claims:
        return 1.0
    supported = sum(1 for c in j.claims if c.supported)
    return round(supported / len(j.claims), 4)


def score_context_precision(j: ItemJudgement) -> Optional[float]:
    """(#chunks pertinents)/(#chunks) ; aucun chunk → 0.0 (pas de signal utile)."""
    if not j.chunks:
        return 0.0
    relevant = sum(1 for c in j.chunks if c.relevant)
    return round(relevant / len(j.chunks), 4)


def score_answer_relevancy(j: ItemJudgement) -> Optional[float]:
    """note(0–4)/4 ; note absente → None (non scorable, item remonté)."""
    if j.relevancy_score_0_4 is None:
        return None
    return round(j.relevancy_score_0_4 / 4.0, 4)


def score_item(item_id: str, j: ItemJudgement) -> ItemScores:
    """Calcule les trois scores d'un item à partir de ses verdicts bruts."""
    return ItemScores(
        item_id=item_id,
        faithfulness=score_faithfulness(j),
        context_precision=score_context_precision(j),
        answer_relevancy=score_answer_relevancy(j),
        n_claims=len(j.claims),
        n_supported=sum(1 for c in j.claims if c.supported),
        n_chunks=len(j.chunks),
        n_relevant=sum(1 for c in j.chunks if c.relevant),
        errors=list(j.errors),
    )


def aggregate(scores: List[ItemScores]) -> Dict[str, Optional[float]]:
    """Moyenne **macro** (par item) de chaque métrique, en ignorant les ``None``.

    Macro plutôt que micro : chaque item du golden set pèse pareil, quel que soit
    son nombre de claims/chunks — on mesure la qualité moyenne *par cas*, pas par
    affirmation. Une métrique sans aucun item scorable vaut ``None``.
    """
    return {
        "faithfulness": _mean([s.faithfulness for s in scores]),
        "context_precision": _mean([s.context_precision for s in scores]),
        "answer_relevancy": _mean([s.answer_relevancy for s in scores]),
    }


def _mean(values: List[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 4)


# ───────────────────────────────────────────────────────────────────────────
# Gate qualité.
# ───────────────────────────────────────────────────────────────────────────
@dataclass
class GateThresholds:
    """Seuils du gate (défauts = exigence d'un RAG de production prudent)."""
    faithfulness: float = 0.90
    context_precision: float = 0.70
    answer_relevancy: float = 0.85


@dataclass
class GateResult:
    passed: bool
    details: Dict[str, dict]  # métrique -> {value, threshold, passed}


def evaluate_gate(aggregates: Dict[str, Optional[float]],
                  thresholds: GateThresholds) -> GateResult:
    """Compare les agrégats aux seuils. Une métrique ``None`` (non scorable) est
    un **échec** : on ne laisse pas passer un gate « vide » faute de mesure.
    """
    wanted = {
        "faithfulness": thresholds.faithfulness,
        "context_precision": thresholds.context_precision,
        "answer_relevancy": thresholds.answer_relevancy,
    }
    details: Dict[str, dict] = {}
    ok = True
    for name, thr in wanted.items():
        val = aggregates.get(name)
        metric_pass = (val is not None) and (val >= thr)
        ok = ok and metric_pass
        details[name] = {"value": val, "threshold": thr, "passed": metric_pass}
    return GateResult(passed=ok, details=details)
