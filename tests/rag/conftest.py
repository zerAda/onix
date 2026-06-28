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

NB (correctif M2) : les helpers de lecture (`read_prompt_block`, etc.) vivent
désormais dans `prompt_loader.py` (module normal, importable hors pytest) et sont
**ré-exportés** ci-dessous. C'est `live_harness` qui les importe via
`prompt_loader` — surtout PAS via `conftest`, dont le nom est ambigu sous
`python -m ragas_eval.runner` (collision avec `ragas_eval/conftest.py`).
"""
from __future__ import annotations

import os
import sys
_ACTIONS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "actions"))
if _ACTIONS not in sys.path:
    sys.path.insert(0, _ACTIONS)  # rend `app.guardrail_core` importable (garde-fou partagé)

import pytest

# Ré-export des helpers de lecture (définis dans prompt_loader.py) pour que les
# tests existants `from conftest import read_prompt_block, ...` restent verts.
from prompt_loader import (  # noqa: F401  (ré-export volontaire)
    DATASET_PATH,
    EXAMPLES_PATH,
    PROMPT_PATH,
    load_dataset,
    read_prompt_block,
    read_prompt_markdown,
)


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
