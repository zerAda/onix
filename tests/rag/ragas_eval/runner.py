"""Runner d'évaluation RAGAS souveraine — charge le golden set, score, *gate*.

Usage (live ; nécessite Ollama) ::

    ONIX_LIVE_OLLAMA=1 ONIX_LIVE_MODEL=qwen2.5:7b-instruct \\
        python -m tests.rag.ragas_eval.runner [--json scores.json]

ou, depuis le dossier `tests/rag/` (imports plats) ::

    python -m ragas_eval.runner --json scores.json

Comportement :

* calcule les métriques **par item** + **agrégées** (cf. `metrics.py`) ;
* imprime un **rapport français** lisible (tableau par item + agrégats +
  PASS/FAIL) ;
* applique un **gate** (seuils par défaut faithfulness ≥ 0.90,
  context_precision ≥ 0.70, answer_relevancy ≥ 0.85 — surchargeables par env
  ``ONIX_RAGAS_MIN_FAITHFULNESS`` / ``…_CONTEXT_PRECISION`` / ``…_ANSWER_RELEVANCY``) ;
* **sort en code non nul** si le gate échoue (CI/recette) ;
* ``--json OUT`` sérialise tous les scores ;
* ``--backend {sovereign,ragas}`` : défaut = juge local souverain ; ``ragas``
  importe **paresseusement** la vraie librairie et **dégrade proprement** si
  absente.

Robustesse : une réponse LLM illisible sur un item ne fait **jamais** crasher le
runner — elle est comptée, l'item continue, et l'anomalie est remontée dans le
rapport (compteur d'erreurs + détail).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

# ── tests/rag importable en nom plat (comme run_live.py) ───────────────────
_HERE = Path(__file__).resolve().parent
_RAG_DIR = _HERE.parent
for _p in (str(_HERE), str(_RAG_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import judge as judge_mod  # noqa: E402  (paquet : import par nom)
import metrics as metrics_mod  # noqa: E402

GOLDEN_PATH = _HERE / "golden_fr.json"


# ───────────────────────────────────────────────────────────────────────────
# Chargement du golden set.
# ───────────────────────────────────────────────────────────────────────────
def load_golden(path: Optional[Path] = None) -> List[dict]:
    p = path or GOLDEN_PATH
    with p.open(encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", [])
    if not items:
        raise ValueError(f"Golden set vide ou mal formé : {p}")
    return items


# ───────────────────────────────────────────────────────────────────────────
# Seuils du gate (env-surchargeables).
# ───────────────────────────────────────────────────────────────────────────
def thresholds_from_env() -> metrics_mod.GateThresholds:
    base = metrics_mod.GateThresholds()
    return metrics_mod.GateThresholds(
        faithfulness=_env_float("ONIX_RAGAS_MIN_FAITHFULNESS", base.faithfulness),
        context_precision=_env_float("ONIX_RAGAS_MIN_CONTEXT_PRECISION",
                                     base.context_precision),
        answer_relevancy=_env_float("ONIX_RAGAS_MIN_ANSWER_RELEVANCY",
                                    base.answer_relevancy),
    )


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[avertissement] {name}={raw!r} non numérique — défaut {default} utilisé.",
              file=sys.stderr)
        return default


# ───────────────────────────────────────────────────────────────────────────
# Backend SOUVERAIN (juge local) : score chaque item via le LLM injectable.
# ───────────────────────────────────────────────────────────────────────────
def run_sovereign(items: List[dict],
                  llm: Optional[judge_mod.LLM] = None) -> List[metrics_mod.ItemScores]:
    """Score chaque item via le juge local. ``llm`` injectable (défaut = Ollama).

    C'est le point d'injection clé pour les tests : on passe un faux juge scripté
    et on évalue tout le pipeline **sans réseau**.
    """
    judge_llm = llm or judge_mod.default_llm
    scored: List[metrics_mod.ItemScores] = []
    for it in items:
        judgement = judge_mod.judge_item(
            question=it.get("question", ""),
            answer=it.get("answer_to_grade", ""),
            contexts=list(it.get("retrieved_contexts", []) or []),
            llm=judge_llm,
        )
        scored.append(metrics_mod.score_item(it.get("id", "?"), judgement))
    return scored


# ───────────────────────────────────────────────────────────────────────────
# Backend RAGAS optionnel (import paresseux + dégradation propre).
# ───────────────────────────────────────────────────────────────────────────
def run_ragas(items: List[dict]) -> List[metrics_mod.ItemScores]:
    """Adaptateur vers la VRAIE librairie ``ragas`` (optionnelle).

    Importe ``ragas`` **paresseusement** : s'il n'est pas installé, on lève une
    ``RagasUnavailable`` avec un message clair (le `main` la rattrape et explique
    comment l'installer, sans crasher l'outil par défaut). Ce backend n'est PAS
    sur le chemin par défaut : le défaut reste le juge local souverain.

    NB : on ne contraint pas le format interne de RAGAS ici (l'API évolue) — cet
    adaptateur documente l'intention et fournit un point d'extension. Le défaut
    souverain reste la voie testée et garantie hors-ligne.
    """
    try:
        import ragas  # noqa: F401  (import paresseux, optionnel)
    except ImportError as e:
        raise RagasUnavailable(
            "Backend 'ragas' demandé mais la librairie n'est pas installée. "
            "C'est une dépendance OPTIONNELLE et non souveraine (tire datasets, "
            "souvent un LLM/embeddings cloud). Installe-la explicitement : "
            "`pip install ragas` — ou reste sur le backend par défaut "
            "(--backend sovereign), qui score en LOCAL via Ollama."
        ) from e
    # Si un jour on câble le vrai pipeline RAGAS, c'est ici. Tant que ce n'est pas
    # fait, on est honnête : on signale que l'intégration complète reste à brancher.
    raise RagasUnavailable(
        "La librairie 'ragas' est présente, mais l'intégration complète n'est pas "
        "câblée dans ce harnais (l'API RAGAS et ses LLM/embeddings ne sont pas "
        "souverains par défaut). Utilise --backend sovereign (juge local Ollama)."
    )


class RagasUnavailable(RuntimeError):
    """Le backend ragas n'est pas utilisable (absent ou non câblé)."""


# ───────────────────────────────────────────────────────────────────────────
# Rapport texte (français) + sérialisation JSON.
# ───────────────────────────────────────────────────────────────────────────
def _fmt(v: Optional[float]) -> str:
    return "  n/a " if v is None else f"{v:5.3f}"


def build_report(scores: List[metrics_mod.ItemScores],
                 aggregates: Dict[str, Optional[float]],
                 gate: metrics_mod.GateResult, *, model: str,
                 backend: str) -> str:
    lines: List[str] = []
    bar = "=" * 78
    lines.append(bar)
    lines.append("ÉVALUATION QUALITÉ RAG — méthodologie RAGAS (juge LOCAL souverain)")
    lines.append(f"Backend : {backend}    Modèle juge : {model}")
    lines.append(bar)
    lines.append("")
    lines.append("Métriques (∈ [0,1]) :")
    lines.append("  faithfulness       = affirmations de la réponse étayées par le contexte")
    lines.append("  context_precision  = chunks de contexte pertinents pour la question")
    lines.append("  answer_relevancy   = la réponse adresse directement la question")
    lines.append("")
    header = (f"  {'ID':<6} {'faithf.':>8} {'ctx_prec':>9} {'ans_rel':>8} "
              f"{'claims':>8} {'chunks':>8} {'err':>4}")
    lines.append(header)
    lines.append(f"  {'-'*6} {'-'*8} {'-'*9} {'-'*8} {'-'*8} {'-'*8} {'-'*4}")
    for s in scores:
        claims_cell = f"{s.n_supported}/{s.n_claims}"
        chunks_cell = f"{s.n_relevant}/{s.n_chunks}"
        lines.append(
            f"  {s.item_id:<6} {_fmt(s.faithfulness):>8} "
            f"{_fmt(s.context_precision):>9} {_fmt(s.answer_relevancy):>8} "
            f"{claims_cell:>8} {chunks_cell:>8} {len(s.errors):>4}")
    lines.append("")
    lines.append("Agrégats (moyenne macro par item) :")
    for name in ("faithfulness", "context_precision", "answer_relevancy"):
        d = gate.details[name]
        verdict = "PASS" if d["passed"] else "FAIL"
        lines.append(
            f"  {name:<20} = {_fmt(aggregates.get(name))}  "
            f"(seuil {d['threshold']:.2f}) → {verdict}")
    lines.append("")

    # Anomalies (réponses LLM illisibles) — transparence.
    anomalies = [(s.item_id, e) for s in scores for e in s.errors]
    if anomalies:
        lines.append(f"Anomalies de jugement remontées ({len(anomalies)}) :")
        for item_id, err in anomalies:
            lines.append(f"  - [{item_id}] {err}")
        lines.append("")

    lines.append("=" * 78)
    lines.append(f"VERDICT GATE : {'PASS ✅' if gate.passed else 'FAIL ❌'}")
    lines.append("=" * 78)
    return "\n".join(lines)


def to_json(scores: List[metrics_mod.ItemScores],
            aggregates: Dict[str, Optional[float]],
            gate: metrics_mod.GateResult, *, model: str, backend: str) -> dict:
    return {
        "backend": backend,
        "judge_model": model,
        "items": [
            {
                "id": s.item_id,
                "faithfulness": s.faithfulness,
                "context_precision": s.context_precision,
                "answer_relevancy": s.answer_relevancy,
                "n_claims": s.n_claims,
                "n_supported": s.n_supported,
                "n_chunks": s.n_chunks,
                "n_relevant": s.n_relevant,
                "errors": s.errors,
            }
            for s in scores
        ],
        "aggregates": aggregates,
        "gate": {
            "passed": gate.passed,
            "details": gate.details,
        },
    }


# ───────────────────────────────────────────────────────────────────────────
# Orchestration testable (sans I/O d'argv) + main CLI.
# ───────────────────────────────────────────────────────────────────────────
def evaluate(items: List[dict], *, backend: str = "sovereign",
             llm: Optional[judge_mod.LLM] = None,
             thresholds: Optional[metrics_mod.GateThresholds] = None):
    """Score + agrège + gate. Renvoie ``(scores, aggregates, gate)``.

    Point d'entrée **pur** (pas d'argparse, pas de sys.exit) → directement
    testable en injectant ``llm`` et ``thresholds``.
    """
    thr = thresholds or metrics_mod.GateThresholds()
    if backend == "ragas":
        scores = run_ragas(items)
    else:
        scores = run_sovereign(items, llm=llm)
    aggregates = metrics_mod.aggregate(scores)
    gate = metrics_mod.evaluate_gate(aggregates, thr)
    return scores, aggregates, gate


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Évaluation RAGAS souveraine (juge LOCAL Ollama) du golden set FR.")
    ap.add_argument("--backend", choices=["sovereign", "ragas"], default="sovereign",
                    help="sovereign = juge local Ollama (défaut) ; ragas = librairie optionnelle.")
    ap.add_argument("--json", dest="json_out", metavar="OUT",
                    help="Écrit tous les scores en JSON dans ce fichier.")
    ap.add_argument("--golden", metavar="FICHIER",
                    help="Chemin d'un golden set alternatif (défaut : golden_fr.json).")
    args = ap.parse_args(argv)

    # Import paresseux du harnais live UNIQUEMENT pour le backend souverain : on
    # vérifie la joignabilité d'Ollama et on récupère le nom de modèle pour le rapport.
    model = "(n/a)"
    if args.backend == "sovereign":
        try:
            import live_harness as lh
            model = lh.ollama_model()
            if not lh.ollama_reachable():
                print(f"ERREUR : Ollama injoignable sur {lh.ollama_base()}. "
                      "Démarre le conteneur et pose ONIX_LIVE_MODEL "
                      "(ou utilise le mode test avec juge injecté).", file=sys.stderr)
                return 2
        except Exception as e:  # harnais absent / import cassé
            print(f"ERREUR : harnais live indisponible ({type(e).__name__}: {e}).",
                  file=sys.stderr)
            return 2

    golden_path = Path(args.golden) if args.golden else None
    try:
        items = load_golden(golden_path)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"ERREUR : chargement du golden set impossible — {e}", file=sys.stderr)
        return 2

    thresholds = thresholds_from_env()
    try:
        scores, aggregates, gate = evaluate(
            items, backend=args.backend, thresholds=thresholds)
    except RagasUnavailable as e:
        print(f"[backend ragas indisponible] {e}", file=sys.stderr)
        return 3

    print(build_report(scores, aggregates, gate, model=model, backend=args.backend))

    if args.json_out:
        payload = to_json(scores, aggregates, gate, model=model, backend=args.backend)
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[écrit] {args.json_out}")

    return 0 if gate.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
