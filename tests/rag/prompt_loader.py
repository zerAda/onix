"""Helpers de lecture des artefacts RAG (prompt système, dataset) — SANS dépendance
pytest, importables aussi bien par les tests (via `conftest`) que par le runtime
NON-pytest (`python -m ragas_eval.runner` → `live_harness`).

Pourquoi ce module existe (correctif M2 / RAGAS-FIX) : ces fonctions vivaient dans
`conftest.py`. Mais `conftest` est un nom **spécial pytest** ; sous
`python -m ragas_eval.runner`, l'instruction `from conftest import read_prompt_block`
de `live_harness.py` se résolvait vers `tests/rag/ragas_eval/conftest.py` (un simple
shim `sys.path`, qui ne définit PAS ces fonctions) → `ImportError`, et le runner
sortait en code 2 AVANT toute évaluation. Le gate RAGAS nightly n'a donc jamais
évalué une seule réponse (pytest restait vert car il charge le bon conftest). On
extrait les helpers ici, dans un module normal au nom non ambigu, et `conftest.py`
les ré-exporte pour ne rien casser côté pytest.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# ── Localisation des artefacts (chemins absolus, robustes au cwd) ──────────
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
PROMPT_PATH = _REPO_ROOT / "prompts" / "agent_commercial_systeme.md"
EXAMPLES_PATH = _REPO_ROOT / "prompts" / "exemples_questions.md"
DATASET_PATH = _HERE / "dataset_eval.json"


def read_prompt_markdown() -> str:
    """Contenu brut (markdown complet) du fichier de prompt système."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def read_prompt_block() -> str:
    """Le bloc de prompt à copier dans Onyx (contenu entre la 1re paire de ```).

    C'est ce bloc — et non le markdown d'accompagnement — qui constitue le
    contrat de sécurité collé dans l'agent. On le teste isolément pour qu'un
    garde-fou déplacé hors du bloc fasse échouer la recette.
    """
    md = read_prompt_markdown()
    m = re.search(r"```(?:\w+)?\n(.*?)\n```", md, re.DOTALL)
    assert m, "Bloc de prompt (``` … ```) introuvable dans agent_commercial_systeme.md"
    return m.group(1)


def load_dataset() -> dict:
    with DATASET_PATH.open(encoding="utf-8") as f:
        return json.load(f)
