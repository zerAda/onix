# -*- coding: utf-8 -*-
"""Validation fail-closed des enums de configuration (`app.config`).

Un typo de déploiement sur un enum (`GATEWAY_GROUP_SOURCE`,
`GATEWAY_DOC_ACL_DEFAULT_POLICY`) ne doit PAS basculer silencieusement vers un
comportement par défaut inattendu : la passerelle REFUSE de démarrer avec un
message clair. Les valeurs valides restent acceptées (et normalisées casse/espaces).
"""
from __future__ import annotations

import pytest

import app.config as config


@pytest.fixture(autouse=True)
def _reset_settings():
    # État de cache propre avant ET après (ne pas polluer les autres modules).
    config.reset_settings_cache()
    yield
    config.reset_settings_cache()


def test_group_source_invalide_refuse_le_demarrage(monkeypatch):
    monkeypatch.setenv("GATEWAY_GROUP_SOURCE", "grpah")  # typo de 'graph'
    with pytest.raises(ValueError):
        config.get_settings()


def test_doc_acl_default_policy_invalide_refuse_le_demarrage(monkeypatch):
    monkeypatch.delenv("GATEWAY_GROUP_SOURCE", raising=False)  # défaut 'auto' (valide)
    monkeypatch.setenv("GATEWAY_DOC_ACL_DEFAULT_POLICY", "permit")  # ni deny ni allow
    with pytest.raises(ValueError):
        config.get_settings()


@pytest.mark.parametrize(
    "src,attendu",
    [("claims", "claims"), ("graph", "graph"), ("auto", "auto"),
     ("AUTO", "auto"), ("  graph  ", "graph")],
)
def test_group_source_valides_normalises(monkeypatch, src, attendu):
    monkeypatch.setenv("GATEWAY_GROUP_SOURCE", src)
    monkeypatch.delenv("GATEWAY_DOC_ACL_DEFAULT_POLICY", raising=False)
    assert config.get_settings().group_source == attendu


@pytest.mark.parametrize("policy", ["deny", "allow", "DENY", " allow "])
def test_doc_acl_default_policy_valides_normalises(monkeypatch, policy):
    monkeypatch.delenv("GATEWAY_GROUP_SOURCE", raising=False)
    monkeypatch.setenv("GATEWAY_DOC_ACL_DEFAULT_POLICY", policy)
    assert config.get_settings().doc_acl_default_policy == policy.strip().lower()
