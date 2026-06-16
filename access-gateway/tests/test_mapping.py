"""Tests du mapping groupe -> Document Set (deny-by-default, formes, casse)."""
from __future__ import annotations

import pytest

from app.mapping import GroupMap, load_group_map, load_group_map_from_obj

GUID = "11111111-1111-1111-1111-111111111111"


def test_simple_form():
    gm = load_group_map_from_obj({GUID: ["clients-nord"], "Equipe-Sud": ["clients-sud"]})
    assert gm.authorized_document_sets([GUID]) == ["clients-nord"]
    assert gm.authorized_document_sets(["Equipe-Sud"]) == ["clients-sud"]


def test_structured_form_with_defaults():
    gm = load_group_map_from_obj(
        {
            "version": 1,
            "default_document_sets": ["catalogue-public"],
            "groups": {GUID: {"document_sets": ["clients-nord"], "label": "Nord"}},
        }
    )
    # Le set par défaut est toujours présent ; le groupe ajoute le sien.
    assert gm.authorized_document_sets([GUID]) == ["catalogue-public", "clients-nord"]
    # Un utilisateur sans groupe connu n'a que le défaut.
    assert gm.authorized_document_sets(["inconnu"]) == ["catalogue-public"]


def test_deny_by_default_unknown_group():
    gm = load_group_map_from_obj({GUID: ["clients-nord"]})
    assert gm.authorized_document_sets(["99999999-0000-0000-0000-000000000000"]) == []
    assert gm.authorized_document_sets([]) == []


def test_case_insensitive_keys():
    gm = load_group_map_from_obj({"Commerciaux-Nord": ["clients-nord"]})
    assert gm.authorized_document_sets(["commerciaux-NORD"]) == ["clients-nord"]


def test_union_and_dedup_multiple_groups():
    gm = load_group_map_from_obj(
        {"a": ["s1", "s2"], "b": ["s2", "s3"]}
    )
    assert gm.authorized_document_sets(["a", "b"]) == ["s1", "s2", "s3"]


def test_missing_file_yields_empty_map(tmp_path):
    gm = load_group_map(str(tmp_path / "absent.json"))
    assert isinstance(gm, GroupMap)
    assert gm.authorized_document_sets(["anything"]) == []


def test_invalid_sets_type_raises():
    with pytest.raises(ValueError):
        load_group_map_from_obj({"a": {"document_sets": "pas-une-liste"}})
