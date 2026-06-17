"""Fixtures et helpers partagés du harnais de tests RAG (`tests/rag/`).

Deux modes :

* **Mode contrat (défaut, hors-LLM)** — aucune dépendance à un LLM ni à un
  réseau. On vérifie le *contrat* de l'agent : présence des garde-fous dans le
  prompt système, résistance des règles aux vecteurs red-team, et cohérence du
  dataset d'évaluation. C'est ce mode qui doit passer en CI (`make rag-test`).

* **Mode live (optionnel)** — activé via la variable d'environnement
  ``ONIX_RAG_LIVE=1`` + ``ONIX_API_URL`` (+ ``ONIX_API_KEY``). Rejoue le dataset
  contre une vraie API Onyx et applique les assertions mustContain /
  mustNotContain sur la réponse. Les tests live sont *skipped* si l'env n'est
  pas configuré, donc la recette hors-LLM reste verte sans LLM.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

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


# ── Fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def prompt_md() -> str:
    return read_prompt_markdown()


@pytest.fixture(scope="session")
def prompt_block() -> str:
    return read_prompt_block()


@pytest.fixture(scope="session")
def dataset() -> dict:
    return load_dataset()


# ── Mode live optionnel ──────────────────────────────────────────────────
def live_enabled() -> bool:
    return os.getenv("ONIX_RAG_LIVE", "").strip().lower() in {"1", "true", "yes"}


requires_live = pytest.mark.skipif(
    not live_enabled(),
    reason="Mode live désactivé (positionner ONIX_RAG_LIVE=1 + ONIX_API_URL pour rejouer contre Onyx).",
)
