"""Portage exécutable du dataset d'évaluation RAG (`dataset_eval.json`).

Mode contrat (défaut, hors-LLM)
  Valide la STRUCTURE et la COHÉRENCE du dataset (schéma, IDs uniques, présence
  des catégories nominal/négatif/red-team, non-vacuité des assertions sur les
  cas nominaux). Le dataset devient ainsi un artefact testé, pas un fichier mort.
  Vérifie en outre que chaque format attendu par les cas nominaux a bien son
  ancrage dans le prompt système (le contrat de sortie est cohérent avec l'éval).

Mode live (optionnel, ONIX_RAG_LIVE=1 + ONIX_API_URL)
  Rejoue chaque `userInput` contre l'API Onyx et applique mustContain /
  mustNotContain sur la réponse. Skippé proprement si l'API n'est pas configurée.
"""
from __future__ import annotations

import os

import pytest

from conftest import live_enabled, load_dataset, read_prompt_block

_DATA = load_dataset()
_CONV = _DATA["conversations"]


# ── Cohérence du dataset (hors-LLM) ────────────────────────────────────────
def test_dataset_loads_and_has_conversations():
    assert isinstance(_CONV, list) and len(_CONV) >= 15, (
        f"Dataset trop maigre ({len(_CONV)} paires Q/R) — viser ≥ 15."
    )


def test_dataset_ids_unique():
    ids = [c["id"] for c in _CONV]
    assert len(ids) == len(set(ids)), "IDs de conversation dupliqués dans le dataset."


@pytest.mark.parametrize("conv", _CONV, ids=[c["id"] for c in _CONV])
def test_dataset_entry_schema(conv):
    for key in ("id", "topic", "category", "userInput", "expectedBehavior",
                "mustContain", "mustNotContain"):
        assert key in conv, f"{conv.get('id', '?')} : clé '{key}' manquante."
    assert isinstance(conv["mustContain"], list)
    assert isinstance(conv["mustNotContain"], list)
    assert conv["userInput"].strip(), f"{conv['id']} : userInput vide."


def test_dataset_covers_required_categories():
    cats = {c["category"] for c in _CONV}
    # Doit couvrir au moins : nominal, un négatif, et au moins un red-team.
    assert any(c == "nominal" for c in cats), "Aucun cas nominal."
    assert any(c.startswith("negatif") for c in cats), "Aucun cas négatif."
    assert any("red_team" in c for c in cats), "Aucun cas red-team."


def test_nominal_cases_have_assertions():
    """Un cas nominal sans aucune assertion mustContain ne teste rien."""
    weak = [c["id"] for c in _CONV
            if c["category"] == "nominal" and not c["mustContain"]]
    assert not weak, f"Cas nominaux sans assertion mustContain : {weak}"


def test_dataset_covers_six_portfolio_topics():
    """Les 6 cas portefeuille doivent être représentés dans le jeu d'éval."""
    topics = " ".join(c["topic"].lower() for c in _CONV)
    required = ["concurrence", "tarifaire", "gap", "renouvellement",
                "sinistralité", "bord"]
    missing = [r for r in required if r not in topics]
    assert not missing, f"Cas portefeuille absents du dataset : {missing}"


@pytest.mark.parametrize(
    "conv",
    [c for c in _CONV if c["category"] == "nominal"],
    ids=[c["id"] for c in _CONV if c["category"] == "nominal"],
)
def test_nominal_assertions_are_anchored_in_prompt(conv):
    """Garantit que ce que l'éval EXIGE en sortie nominale est bien quelque chose
    que le prompt SAIT produire : chaque mustContain (hors valeurs purement
    dynamiques) doit avoir un ancrage dans le contrat de sortie du prompt.

    On ignore les jetons dynamiques (noms de clients/concurrents/nombres) qui ne
    figurent pas — par conception — dans un prompt générique.
    """
    block = read_prompt_block().lower()
    dynamic = {"axa", "malakoff", "ag2r", "gerep", "90", "alpha", "beta",
               "gamma", "delta"}
    for needle in conv["mustContain"]:
        n = needle.lower()
        if n in dynamic:
            continue
        assert n in block, (
            f"{conv['id']} : l'éval exige '{needle}' en sortie, mais le prompt "
            "ne l'ancre pas. Contrat éval/prompt incohérent."
        )


# ── Mode live optionnel ────────────────────────────────────────────────────
def query_onyx(user_input: str) -> str:
    """Envoie une question à l'agent Onyx et renvoie le texte de la réponse.

    Configuration par variables d'environnement :
      ONIX_API_URL   — base, ex. http://localhost:8080
      ONIX_API_KEY   — jeton (optionnel selon le déploiement)
      ONIX_PERSONA_ID— id de l'assistant (optionnel, défaut 0)

    Endpoint visé : POST {ONIX_API_URL}/chat/send-message (API Onyx). Le contrat
    exact peut varier selon la version ; cette fonction est volontairement
    tolérante et n'est appelée qu'en mode live.
    """
    import requests  # import paresseux

    base = os.environ["ONIX_API_URL"].rstrip("/")
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("ONIX_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "message": user_input,
        "persona_id": int(os.getenv("ONIX_PERSONA_ID", "0")),
        "chat_session_id": None,
        "parent_message_id": None,
        "prompt_id": None,
        "search_doc_ids": None,
        "retrieval_options": {"run_search": "auto"},
    }
    resp = requests.post(f"{base}/chat/send-message", json=payload,
                         headers=headers, timeout=120)
    resp.raise_for_status()
    # Onyx peut streamer en lignes JSON ; on concatène tout texte rencontré.
    text = resp.text
    try:
        data = resp.json()
        if isinstance(data, dict):
            return str(data.get("answer") or data.get("message") or text)
    except ValueError:
        pass
    return text


@pytest.mark.parametrize("conv", _CONV, ids=[c["id"] for c in _CONV])
def test_live_eval(conv):
    if not live_enabled():
        pytest.skip("Mode live désactivé (ONIX_RAG_LIVE=1 + ONIX_API_URL pour activer).")
    answer = query_onyx(conv["userInput"])
    low = answer.lower()
    for needle in conv["mustContain"]:
        assert needle.lower() in low, (
            f"{conv['id']} : réponse live sans '{needle}'. Réponse : {answer[:300]!r}"
        )
    for banned in conv["mustNotContain"]:
        assert banned.lower() not in low, (
            f"{conv['id']} : réponse live contient l'interdit '{banned}'. "
            f"Réponse : {answer[:300]!r}"
        )
