"""Comparateur **anti-régression** des scores RAGAS agrégés vs une *baseline*.

Pourquoi un comparateur EN PLUS du gate du runner ?
---------------------------------------------------
Le `runner.py` applique un **gate absolu** : chaque métrique doit dépasser un
seuil (faithfulness ≥ 0.90 …, surchargeable par env). Utile contre un effondrement
brutal, mais **aveugle à la dérive lente** : une qualité qui glisse de 0.97 à 0.91
reste « PASS » alors qu'on a perdu 6 points. Ce module ferme ce trou : il compare
les **agrégats du run du jour** à une **baseline committée** et **échoue si une
métrique chute de plus d'une tolérance** (défaut 0.05) sous la référence.

Deux garde-fous **complémentaires**, donc :

* **gate absolu** (runner)      → « est-ce assez bon dans l'absolu ? » ;
* **gate relatif** (ce module)  → « a-t-on RÉGRESSÉ par rapport à hier ? ».

Tolérance, pas égalité — pourquoi ?
-----------------------------------
Le juge est un **LLM** (Ollama local, petit modèle en CI) : ses verdicts **varient**
d'un run à l'autre (échantillonnage, formulation), même à température 0. Exiger
l'égalité stricte rendrait le job **rouge en permanence** pour du bruit. On tolère
donc une marge (`--tolerance`) : seule une **vraie** dégradation, au-delà du bruit,
fait échouer le job. Une **hausse** ne pénalise jamais (c'est une amélioration).

Schéma d'entrée (`scores.json` produit par `runner.py --json`) ::

    {
      "aggregates": {"faithfulness": 0.97, "context_precision": 0.81,
                     "answer_relevancy": 0.93},
      ...
    }

La baseline réutilise **exactement** ce schéma (on peut donc committer un
`scores.json` tel quel comme baseline, ou utiliser ``--update``).

CLI ::

    # compare le run du jour à la baseline, échoue si régression > tolérance
    python -m ragas_eval.compare_scores scores.json \\
        --baseline baseline_scores.json [--tolerance 0.05]

    # rafraîchit la baseline à partir d'un run de référence (sain)
    python -m ragas_eval.compare_scores scores.json \\
        --baseline baseline_scores.json --update

Codes de sortie : 0 = pas de régression (ou ``--update``) ; 1 = régression
détectée ; 2 = erreur d'usage (fichier illisible, schéma invalide).

100 % hors-ligne et **stdlib uniquement** → testable sous `make pytest` sans réseau.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Les trois métriques cœur, dans l'ordre d'affichage. Source de vérité unique :
# on n'compare QUE ces clés (le reste du JSON — items, gate… — est ignoré).
METRIC_KEYS: Tuple[str, ...] = (
    "faithfulness",
    "context_precision",
    "answer_relevancy",
)

DEFAULT_TOLERANCE = 0.05


# ───────────────────────────────────────────────────────────────────────────
# Chargement tolérant.
# ───────────────────────────────────────────────────────────────────────────
def load_aggregates(path: Path) -> Dict[str, Optional[float]]:
    """Charge la section ``aggregates`` d'un fichier de scores RAGAS.

    Accepte deux formes : le JSON complet du runner (avec une clé ``aggregates``)
    OU directement un objet d'agrégats « plat » (``{"faithfulness": …}``) — ce qui
    rend une baseline écrite à la main triviale. Lève ``ValueError`` si aucune
    métrique connue n'est présente (schéma invalide → on ne compare pas à vide).
    """
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} : JSON racine attendu = objet, obtenu {type(data).__name__}.")
    raw = data.get("aggregates", data)  # tolère le JSON complet OU déjà plat
    if not isinstance(raw, dict):
        raise ValueError(f"{path} : clé 'aggregates' attendue = objet.")
    agg: Dict[str, Optional[float]] = {}
    for k in METRIC_KEYS:
        v = raw.get(k)
        agg[k] = None if v is None else float(v)
    if all(agg[k] is None for k in METRIC_KEYS):
        raise ValueError(
            f"{path} : aucune métrique connue {METRIC_KEYS} trouvée — schéma invalide.")
    return agg


# ───────────────────────────────────────────────────────────────────────────
# Cœur de comparaison (PUR : pas d'I/O, directement testable).
# ───────────────────────────────────────────────────────────────────────────
class MetricDelta:
    """Verdict de comparaison pour UNE métrique.

    ``regressed`` est vrai uniquement si la valeur courante est mesurée, plus
    basse que la baseline, et l'écart dépasse la tolérance — OU si la métrique
    était mesurée dans la baseline mais ne l'est plus (perte de mesure = régression
    : on ne « valide » jamais une métrique qu'on ne sait plus calculer).
    """

    __slots__ = ("name", "baseline", "current", "tolerance", "delta", "regressed", "note")

    def __init__(self, name: str, baseline: Optional[float], current: Optional[float],
                 tolerance: float) -> None:
        self.name = name
        self.baseline = baseline
        self.current = current
        self.tolerance = tolerance
        self.delta: Optional[float] = None
        self.regressed = False
        self.note = ""
        self._evaluate()

    def _evaluate(self) -> None:
        # Pas de référence : on ne peut pas parler de régression (nouvelle métrique).
        if self.baseline is None:
            self.note = "pas de baseline (métrique nouvelle) — ignorée"
            return
        # Référence présente mais mesure du jour absente = perte de signal → régression.
        if self.current is None:
            self.regressed = True
            self.note = "non mesurée ce run alors qu'elle l'était dans la baseline"
            return
        self.delta = round(self.current - self.baseline, 6)
        if self.delta < -self.tolerance:
            self.regressed = True
            self.note = f"chute de {abs(self.delta):.4f} > tolérance {self.tolerance:.4f}"
        elif self.delta < 0:
            self.note = f"baisse de {abs(self.delta):.4f} ≤ tolérance (bruit toléré)"
        else:
            self.note = f"stable/amélioration (+{self.delta:.4f})"


def compare(current: Dict[str, Optional[float]],
            baseline: Dict[str, Optional[float]],
            tolerance: float = DEFAULT_TOLERANCE) -> List[MetricDelta]:
    """Compare agrégats courants vs baseline ; renvoie un ``MetricDelta`` par métrique."""
    if tolerance < 0:
        raise ValueError(f"tolérance négative interdite : {tolerance!r}")
    return [
        MetricDelta(k, baseline.get(k), current.get(k), tolerance)
        for k in METRIC_KEYS
    ]


def has_regression(deltas: List[MetricDelta]) -> bool:
    return any(d.regressed for d in deltas)


# ───────────────────────────────────────────────────────────────────────────
# Rapport texte (français).
# ───────────────────────────────────────────────────────────────────────────
def _fmt(v: Optional[float]) -> str:
    return "  n/a " if v is None else f"{v:6.4f}"


def _fmt_delta(d: Optional[float]) -> str:
    if d is None:
        return "   —   "
    sign = "+" if d >= 0 else "−"
    return f"{sign}{abs(d):6.4f}"


def build_report(deltas: List[MetricDelta], tolerance: float) -> str:
    lines: List[str] = []
    bar = "=" * 72
    lines.append(bar)
    lines.append("COMPARAISON ANTI-RÉGRESSION RAGAS (agrégats vs baseline)")
    lines.append(f"Tolérance de régression : {tolerance:.4f} (une baisse au-delà = FAIL)")
    lines.append(bar)
    lines.append(f"  {'métrique':<20} {'baseline':>9} {'courant':>9} {'delta':>9}  verdict")
    lines.append(f"  {'-'*20} {'-'*9} {'-'*9} {'-'*9}  -------")
    for d in deltas:
        verdict = "RÉGRESSION" if d.regressed else "ok"
        lines.append(
            f"  {d.name:<20} {_fmt(d.baseline):>9} {_fmt(d.current):>9} "
            f"{_fmt_delta(d.delta):>9}  {verdict}")
        lines.append(f"      ↳ {d.note}")
    lines.append(bar)
    if has_regression(deltas):
        worst = [d.name for d in deltas if d.regressed]
        lines.append(f"VERDICT : RÉGRESSION DÉTECTÉE ❌  ({', '.join(worst)})")
        lines.append("Si c'est attendu (nouveau modèle/golden set revu), rafraîchis la "
                     "baseline : compare_scores … --update")
    else:
        lines.append("VERDICT : PAS DE RÉGRESSION ✅")
    lines.append(bar)
    return "\n".join(lines)


# ───────────────────────────────────────────────────────────────────────────
# Mise à jour de baseline.
# ───────────────────────────────────────────────────────────────────────────
def write_baseline(path: Path, current: Dict[str, Optional[float]]) -> None:
    """Écrit/rafraîchit la baseline au schéma ``{"aggregates": {…}}`` (mémo en tête).

    On reproduit le schéma du runner (clé ``aggregates``) pour que baseline et
    sortie du runner soient interchangeables. Un champ ``_comment`` documente la
    provenance pour le relecteur du diff git (une baseline DOIT venir d'un run sain).
    """
    payload = {
        "_comment": (
            "Baseline anti-régression RAGAS — agrégats d'un run de référence SAIN. "
            "Rafraîchir UNIQUEMENT via `compare_scores --update` après revue. "
            "Le comparateur échoue si une métrique chute de plus de la tolérance "
            "sous ces valeurs. Cf. docs/RAG_EVAL.md."
        ),
        "aggregates": {k: current.get(k) for k in METRIC_KEYS},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


# ───────────────────────────────────────────────────────────────────────────
# CLI.
# ───────────────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Compare les agrégats RAGAS du jour à une baseline ; "
                    "échoue si régression au-delà de la tolérance.")
    ap.add_argument("scores", help="Fichier de scores du run (runner --json).")
    ap.add_argument("--baseline", required=True, metavar="FICHIER",
                    help="Fichier baseline (même schéma ; cf. baseline_scores.json).")
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                    metavar="T", help=f"Marge tolérée (défaut {DEFAULT_TOLERANCE}).")
    ap.add_argument("--update", action="store_true",
                    help="Écrase la baseline avec les scores du jour (run sain) et sort 0.")
    args = ap.parse_args(argv)

    scores_path = Path(args.scores)
    baseline_path = Path(args.baseline)

    try:
        current = load_aggregates(scores_path)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"ERREUR : scores illisibles — {e}", file=sys.stderr)
        return 2

    # Mode rafraîchissement : on écrit la baseline depuis le run du jour, point.
    if args.update:
        write_baseline(baseline_path, current)
        print(f"[baseline mise à jour] {baseline_path} ← {scores_path}")
        for k in METRIC_KEYS:
            print(f"  {k:<20} = {_fmt(current.get(k))}")
        return 0

    try:
        baseline = load_aggregates(baseline_path)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"ERREUR : baseline illisible — {e} "
              "(créer/rafraîchir avec --update depuis un run sain).", file=sys.stderr)
        return 2

    try:
        deltas = compare(current, baseline, tolerance=args.tolerance)
    except ValueError as e:
        print(f"ERREUR : {e}", file=sys.stderr)
        return 2

    print(build_report(deltas, args.tolerance))
    return 1 if has_regression(deltas) else 0


if __name__ == "__main__":
    raise SystemExit(main())
