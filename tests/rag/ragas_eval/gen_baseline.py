#!/usr/bin/env python3
"""Générateur **déterministe** de la baseline RAGAS — provenance reproductible.

Régénère `baseline_scores.json` **sans aucun modèle live** : il score le golden
set (`golden_fr.json`) avec le **juge scripté déterministe** (`scripted_judge.py`)
et écrit les agrégats. Le résultat est **reproductible byte-level** : n'importe
qui peut relancer ce script et obtenir le fichier committé à l'octet près.

Pourquoi ce script existe (réponse à l'audit) :
    `docs/audit-reality/rag-prompts.md` notait que la baseline livrée
    (0.75 / 0.875 / 1.0) n'avait **pas de provenance reproductible** au repo (le
    « juge scripté » était enfoui dans les tests, non outillé). Ce générateur rend
    la provenance **explicite, déterministe et réexécutable**.

Usage (offline, aucune dépendance hors stdlib + harnais `tests/rag/`) ::

    cd tests/rag
    python -m ragas_eval.gen_baseline            # imprime les agrégats + diff
    python -m ragas_eval.gen_baseline --write     # (ré)écrit baseline_scores.json
    python -m ragas_eval.gen_baseline --check     # exit≠0 si baseline périmée (CI)

⚠️ Cette baseline est une **graine de référence déterministe** (juge scripté),
PAS un run d'un vrai juge ≥ 7B. Un LLM-juge réel score différemment : après le
premier run nightly sain, rafraîchir la baseline depuis ce run réel via
`compare_scores --update` (cf. `docs/RAG_EVAL.md`).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ── tests/rag + ragas_eval importables en nom plat (comme runner.py) ──────────
_HERE = Path(__file__).resolve().parent
_RAG_DIR = _HERE.parent
for _p in (str(_HERE), str(_RAG_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import runner as runner_mod  # noqa: E402
from scripted_judge import ScriptedJudge  # noqa: E402

BASELINE_PATH = _HERE / "baseline_scores.json"

# Commentaire de provenance inscrit en tête du fichier (clé `_comment`).
_COMMENT = (
    "Baseline anti-régression RAGAS — agrégats produits DÉTERMINISTEMENT par le "
    "juge scripté (tests/rag/ragas_eval/scripted_judge.py) sur golden_fr.json, "
    "via `python -m ragas_eval.gen_baseline --write` (aucun modèle live, "
    "reproductible byte-level). Graine de référence : un vrai juge ≥ 7B score "
    "différemment → rafraîchir via `compare_scores --update` après le premier run "
    "nightly sain. Cf. docs/RAG_EVAL.md."
)


def compute_aggregates() -> dict:
    """Score le golden set avec le juge scripté et renvoie les agrégats arrondis.

    L'arrondi (6 décimales) garantit un rendu **stable** et lisible quel que soit
    l'OS/Python (évite les queues binaires de flottants dans le JSON committé)."""
    items = runner_mod.load_golden()
    scores, aggregates, _gate = runner_mod.evaluate(
        items, backend="sovereign", llm=ScriptedJudge())
    return {k: (round(v, 6) if v is not None else None)
            for k, v in aggregates.items()}


def render_baseline(aggregates: dict) -> str:
    """Sérialise la baseline au format committé (mêmes clés/indentation)."""
    payload = {"_comment": _COMMENT, "aggregates": aggregates}
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Régénère DÉTERMINISTEMENT la baseline RAGAS (juge scripté).")
    ap.add_argument("--write", action="store_true",
                    help="(Ré)écrit baseline_scores.json.")
    ap.add_argument("--check", action="store_true",
                    help="Sort en code ≠0 si le fichier committé diffère du calcul.")
    args = ap.parse_args(argv)

    aggregates = compute_aggregates()
    rendered = render_baseline(aggregates)

    print("Agrégats déterministes (juge scripté) :")
    for k, v in aggregates.items():
        print(f"  {k:<20} = {v}")

    if args.check:
        current = BASELINE_PATH.read_text(encoding="utf-8") if BASELINE_PATH.exists() else ""
        if current != rendered:
            print("\n[ÉCHEC] baseline_scores.json est PÉRIMÉ vs le calcul déterministe.\n"
                  "        Relance `python -m ragas_eval.gen_baseline --write` "
                  "puis revois le diff.", file=sys.stderr)
            return 1
        print("\n[OK] baseline_scores.json est à jour (reproductible byte-level).")
        return 0

    if args.write:
        BASELINE_PATH.write_text(rendered, encoding="utf-8")
        print(f"\n[écrit] {BASELINE_PATH}")
        return 0

    # Sans drapeau : aperçu seulement (ne touche rien).
    print("\n(aperçu seul ; --write pour écrire, --check pour vérifier en CI)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
