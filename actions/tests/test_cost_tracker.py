# -*- coding: utf-8 -*-
"""Tests unitaires FinOps (`app.cost_tracker`) — décision de budget + rate card.

Couvre en direct (hors endpoint) la logique métier de coût :
  * `check_budget` : les 4 niveaux (unknown/ok/warning/exceeded) et leurs SEUILS ;
  * `load_rate_card` : robustesse fail-safe (JSON invalide, valeur négative, centre
    de coût inconnu) — jamais d'exception, jamais de tarif aberrant ;
  * `estimate_cost` : validation fail-closed + honnêteté de la source de tarif
    (A_VALIDER tant qu'aucune rate card n'est fournie, PARAMETRABLE sinon).
"""
from __future__ import annotations

import pytest


def test_check_budget_niveaux_et_seuils():
    from app.cost_tracker import check_budget

    # Pas de budget (None ou <= 0) -> niveau 'unknown' (on ne bloque pas à l'aveugle).
    assert check_budget(50.0, budget_eur=None)["level"] == "unknown"
    assert check_budget(50.0, budget_eur=0)["level"] == "unknown"

    # ok / warning / exceeded aux bons seuils (warn_pct = 80 %).
    assert check_budget(50.0, budget_eur=100.0, warn_pct=80)["level"] == "ok"        # 50 %
    assert check_budget(80.0, budget_eur=100.0, warn_pct=80)["level"] == "warning"   # 80 % (seuil)
    assert check_budget(99.0, budget_eur=100.0, warn_pct=80)["level"] == "warning"   # < 100 %
    assert check_budget(100.0, budget_eur=100.0, warn_pct=80)["level"] == "exceeded"  # 100 %
    assert check_budget(150.0, budget_eur=100.0, warn_pct=80)["level"] == "exceeded"

    # Le ratio est exposé (utile au pilotage / aux alertes).
    assert check_budget(80.0, budget_eur=100.0)["ratio_pct"] == 80.0


def test_load_rate_card_failsafe(monkeypatch):
    from app import cost_tracker

    # JSON invalide -> rate card par défaut (tous centres à 0), JAMAIS d'exception.
    monkeypatch.setenv("ONIX_RATE_CARD", "{pas du json valide")
    card = cost_tracker.load_rate_card()
    assert all(v == 0.0 for v in card.values())

    # Override : valide appliqué ; négatif rejeté ; centre inconnu ignoré.
    monkeypatch.setenv("ONIX_RATE_CARD", '{"ocr_page": 0.01, "llm_message": -5, "inconnu": 9}')
    card = cost_tracker.load_rate_card()
    assert card["ocr_page"] == 0.01      # tarif valide appliqué
    assert card["llm_message"] == 0.0    # négatif rejeté (reste au défaut)
    assert "inconnu" not in card         # centre de coût inconnu ignoré


def test_estimate_cost_validation_et_source(monkeypatch):
    from app import cost_tracker

    monkeypatch.delenv("ONIX_RATE_CARD", raising=False)
    # Fail-closed : centre inconnu / quantité négative -> ValueError.
    with pytest.raises(ValueError):
        cost_tracker.estimate_cost("inconnu", 1)
    with pytest.raises(ValueError):
        cost_tracker.estimate_cost("ocr_page", -1)

    # Sans rate card -> tarif 0 + source A_VALIDER (honnêteté : non valorisé).
    ev = cost_tracker.estimate_cost("ocr_page", 3)
    assert ev["estimated_cost_eur"] == 0.0
    assert ev["cost_source"] == "A_VALIDER"

    # Avec rate card -> PARAMETRABLE + montant calculé.
    monkeypatch.setenv("ONIX_RATE_CARD", '{"ocr_page": 0.02}')
    ev = cost_tracker.estimate_cost("ocr_page", 3)
    assert ev["estimated_cost_eur"] == round(0.02 * 3, 6)
    assert ev["cost_source"] == "PARAMETRABLE"
