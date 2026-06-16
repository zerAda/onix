"""Tests du durcissement du filtre Document Set (cœur du trimming FOSS)."""
from __future__ import annotations

import pytest

from app.onyx_proxy import AccessDenied, enforce_document_sets


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
