"""Tests du durcissement du filtre Document Set (cœur du trimming FOSS)."""
from __future__ import annotations

import pytest

from app.onyx_proxy import AccessDenied, enforce_document_sets, force_internal_search


def test_no_authorized_sets_denies():
    with pytest.raises(AccessDenied):
        enforce_document_sets({"message": "x"}, [], deny_if_empty=True)


def test_no_filter_imposes_full_authorized():
    out = enforce_document_sets({"message": "x"}, ["clients-nord"])
    assert out["retrieval_options"]["filters"]["document_set"] == ["clients-nord"]


def test_client_request_is_intersected():
    payload = {
        "message": "x",
        "retrieval_options": {"filters": {"document_set": ["clients-nord", "clients-sud"]}},
    }
    # L'utilisateur n'est autorisé que sur 'clients-nord' -> 'clients-sud' est retiré.
    out = enforce_document_sets(payload, ["clients-nord"])
    assert out["retrieval_options"]["filters"]["document_set"] == ["clients-nord"]


def test_client_requesting_only_unauthorized_is_denied():
    payload = {"retrieval_options": {"filters": {"document_set": ["secret-rh"]}}}
    with pytest.raises(AccessDenied):
        enforce_document_sets(payload, ["clients-nord"])


def test_cannot_escape_via_search_doc_ids():
    payload = {"message": "x", "search_doc_ids": [1, 2, 3]}
    out = enforce_document_sets(payload, ["clients-nord"])
    # L'accès direct par id de document est neutralisé (non vérifiable en FOSS).
    assert "search_doc_ids" not in out


def test_does_not_mutate_input():
    payload = {"message": "x", "retrieval_options": {"filters": {}}}
    _ = enforce_document_sets(payload, ["clients-nord"])
    assert payload["retrieval_options"]["filters"] == {}  # original intact


def test_malformed_retrieval_options_is_repaired():
    payload = {"message": "x", "retrieval_options": "pas-un-objet"}
    out = enforce_document_sets(payload, ["clients-nord"])
    assert out["retrieval_options"]["filters"]["document_set"] == ["clients-nord"]


def test_permissive_policy_passes_without_filter():
    # deny_if_empty=False : aucun set autorisé mais politique permissive -> pas de filtre.
    out = enforce_document_sets({"message": "x"}, [], deny_if_empty=False)
    assert "retrieval_options" not in out or "document_set" not in out.get(
        "retrieval_options", {}
    ).get("filters", {})


# --- Defense-in-depth API-compat : périmètre posé aussi sur internal_search_filters ---
def test_perimetre_pose_sur_les_deux_champs_filtres():
    out = enforce_document_sets({"message": "x"}, ["clients-nord"])
    assert out["retrieval_options"]["filters"]["document_set"] == ["clients-nord"]
    assert out["internal_search_filters"]["document_set"] == ["clients-nord"]


def test_internal_search_filters_client_ne_peut_pas_elargir():
    # Un client pré-remplissant internal_search_filters est écrasé par le périmètre
    # effectif (déjà borné aux sets autorisés) — pas d'élargissement possible.
    payload = {"message": "x", "internal_search_filters": {"document_set": ["secret-rh"]}}
    out = enforce_document_sets(payload, ["clients-nord"])
    assert out["internal_search_filters"]["document_set"] == ["clients-nord"]


# --- RAG NON-AGENTIQUE : forçage de la recherche documentaire (stopgap CPU, #12) ---
def test_force_internal_search_injecte_outil_recherche():
    # Pose forced_tool_id + allowed_tool_ids => Onyx exécute la recherche (REQUIRED)
    # au lieu de laisser un modèle faible la rater. Réponse sourcée (prouvé live).
    out = force_internal_search({"message": "x"}, enabled=True, tool_id=1)
    assert out["forced_tool_id"] == 1
    assert out["allowed_tool_ids"] == [1]


def test_force_internal_search_tool_id_configurable():
    out = force_internal_search({"message": "x"}, enabled=True, tool_id=7)
    assert out["forced_tool_id"] == 7 and out["allowed_tool_ids"] == [7]


def test_force_internal_search_noop_si_desactive():
    # enabled=False => agentique natif (modèle à function-calling fiable / GPU).
    out = force_internal_search({"message": "x"}, enabled=False, tool_id=1)
    assert "forced_tool_id" not in out and "allowed_tool_ids" not in out


def test_force_internal_search_respecte_choix_client():
    # Un appel avancé qui a déjà choisi ses outils n'est PAS écrasé.
    payload = {"message": "x", "forced_tool_id": 3, "allowed_tool_ids": [3]}
    out = force_internal_search(payload, enabled=True, tool_id=1)
    assert out["forced_tool_id"] == 3 and out["allowed_tool_ids"] == [3]
