"""Tests du moteur d'audit — cas nominal + écarts, repris de la logique AC360,
plus les normalisations (montant/date/nom/contrat) et l'aliasing OCR."""
from __future__ import annotations

from app.audit_engine import (
    alias_field,
    audit,
    extract_canonical_fields,
    normalize_amount,
    normalize_contract,
    normalize_date,
    normalize_name,
)


# --- Normalisations ---------------------------------------------------------
def test_normalize_amount_formats():
    assert normalize_amount("1 000,50 €") == 1000.5
    assert normalize_amount("1,000.50") == 1000.5
    assert normalize_amount("2000") == 2000.0
    assert normalize_amount("") is None
    assert normalize_amount(None) is None


def test_normalize_date_iso():
    assert normalize_date("01/02/2024") == "2024-02-01"
    assert normalize_date("2024-02-01") == "2024-02-01"
    assert normalize_date("not a date") is None


def test_normalize_date_francais_en_toutes_lettres():
    # Dates FR en lettres, courantes sur les contrats/avenants.
    assert normalize_date("1er janvier 2026") == "2026-01-01"
    assert normalize_date("15 mars 2025") == "2025-03-15"
    assert normalize_date("1 août 2026") == "2026-08-01"          # accent
    assert normalize_date("3 janv. 2025") == "2025-01-03"         # abréviation + point
    assert normalize_date("31 février 2026") is None              # date impossible -> fail-closed
    assert normalize_date("le 1er janvier 2026") is None          # pas une date pure


def test_reconciliation_date_francais_vs_si_iso():
    """Métier : une date d'effet en lettres (contrat) doit MATCHer la date ISO du SI
    — pas de faux ECART dû au format."""
    from app.audit_engine import audit
    doc = {"nom_client": "CLIENT X", "date_effet": "1er janvier 2026"}
    ref = {"nom_client": "CLIENT X", "date_effet": "01/01/2026"}
    result = audit({"document": doc, "reference": ref})
    champ_date = [f for f in result["fields"] if f["champ"] == "date_effet"][0]
    assert champ_date["statut"] == "MATCH"


def test_normalize_name_and_contract():
    assert normalize_name("Société Générale") == "SOCIETE GENERALE"
    assert normalize_contract("ctr-2024/AB.12") == "CTR2024AB12"


def test_compare_name_paliers_de_verdict(monkeypatch):
    # Paliers métier (l.63-64) : MATCH >=95, UNCERTAIN >=85, sinon MISMATCH. On
    # INJECTE le score pour exercer les frontières indépendamment du fuzzer
    # (thefuzz peut être absent). UNCERTAIN = revue humaine, pas auto-ECART.
    from app import audit_engine

    def set_score(v):
        monkeypatch.setattr(audit_engine, "_name_score", lambda a, b: v)

    set_score(98)
    assert audit_engine.compare_name("ACME", "ACME")[0] == "MATCH"
    set_score(95)
    assert audit_engine.compare_name("ACME", "ACME")[0] == "MATCH"        # frontière incluse
    set_score(94)
    assert audit_engine.compare_name("ACME", "ACMX")[0] == "UNCERTAIN"
    set_score(85)
    assert audit_engine.compare_name("ACME", "ACMX")[0] == "UNCERTAIN"    # frontière incluse
    set_score(84)
    assert audit_engine.compare_name("ACME", "XYZ")[0] == "MISMATCH"


def test_compare_name_missing_si_champ_vide():
    from app import audit_engine
    # Un nom absent d'un côté -> MISSING (ni MATCH ni MISMATCH : champ non comparable).
    assert audit_engine.compare_name("", "ACME")[0] == "MISSING"
    assert audit_engine.compare_name("ACME", None)[0] == "MISSING"


def test_compare_date_quatre_verdicts():
    from app.audit_engine import compare_date
    # ISO vs FR équivalentes -> MATCH ; valides différentes -> MISMATCH.
    assert compare_date("2025-12-31", "31/12/2025")[0] == "MATCH"
    assert compare_date("2025-12-31", "2025-01-01")[0] == "MISMATCH"
    # Champ absent -> MISSING. Distinction clé : une date PRÉSENTE mais ILLISIBLE
    # (OCR garbage) -> UNCERTAIN (revue humaine), surtout pas MISMATCH (faux écart).
    assert compare_date("", "2025-12-31")[0] == "MISSING"
    assert compare_date("date ???illisible", "2025-12-31")[0] == "UNCERTAIN"


def test_compare_contract_verdicts():
    from app.audit_engine import compare_contract
    # Même numéro à la ponctuation/casse près -> MATCH ; différents -> MISMATCH.
    assert compare_contract("CTR-2024/AB12", "ctr 2024 ab12")[0] == "MATCH"
    assert compare_contract("CTR-001", "CTR-002")[0] == "MISMATCH"
    assert compare_contract("", "CTR-001")[0] == "MISSING"


def test_alias_field_specificity():
    assert alias_field("Raison sociale") == "nom_client"
    assert alias_field("Plafond hospi.") == "plafond_hospitalisation"
    assert alias_field("N° de contrat") == "numero_contrat"
    assert alias_field("libellé inconnu xyz") is None


def test_extract_canonical_from_kv_and_tables():
    ocr = {
        "fields": {
            "Raison sociale": {"value": "ACME SAS"},
            "Date d'effet": {"value": "01/01/2024"},
        },
        "tables": [
            {"cells": [
                {"row_index": 0, "column_index": 0, "content": "Plafond hospitalisation"},
                {"row_index": 0, "column_index": 1, "content": "2 000 €"},
            ]}
        ],
    }
    fields = extract_canonical_fields(ocr)
    assert fields["nom_client"] == "ACME SAS"
    assert fields["date_effet"] == "01/01/2024"
    assert fields["plafond_hospitalisation"] == "2 000 €"


# --- Audit : cas nominal ----------------------------------------------------
def test_audit_conforme():
    result = audit({
        "document": {
            "nom_client": "ACME SAS",
            "plafond_hospitalisation": "2000",
            "date_effet": "01/01/2024",
            "numero_contrat": "CTR-2024-001",
        },
        "reference": {
            "nom_client": "ACME SAS",
            "plafond_hospitalisation": "2000,00",
            "date_effet": "2024-01-01",
            "numero_contrat": "ctr2024001",
        },
    })
    assert result["verdict"] == "CONFORME"
    assert result["score_correspondance_nom"] == 100.0
    statuses = {f["champ"]: f["statut"] for f in result["fields"]}
    assert statuses["plafond_hospitalisation"] == "MATCH"
    assert statuses["numero_contrat"] == "MATCH"


# --- Audit : écart sur un montant -------------------------------------------
def test_audit_ecart_montant():
    result = audit({
        "document": {"nom_client": "ACME SAS", "plafond_hospitalisation": "5000",
                     "date_effet": "01/01/2024", "numero_contrat": "CTR-1"},
        "reference": {"nom_client": "ACME SAS", "plafond_hospitalisation": "2000",
                      "date_effet": "2024-01-01", "numero_contrat": "CTR-1"},
    })
    assert result["verdict"] == "ECART"
    plafond = next(f for f in result["fields"] if f["champ"] == "plafond_hospitalisation")
    assert plafond["statut"] == "MISMATCH"


# --- Audit : client non trouvé (nom différent) ------------------------------
def test_audit_client_non_trouve():
    result = audit({
        "document": {"nom_client": "ENTREPRISE ALPHA"},
        "reference": {"nom_client": "SOCIETE OMEGA"},
    })
    assert result["verdict"] == "CLIENT_NON_TROUVE"


# --- Audit : incertain (montant proche < 1 %) -------------------------------
def test_audit_incertain_montant_proche():
    result = audit({
        "document": {"nom_client": "ACME SAS", "plafond_hospitalisation": "2010",
                     "date_effet": "01/01/2024", "numero_contrat": "CTR-1"},
        "reference": {"nom_client": "ACME SAS", "plafond_hospitalisation": "2000",
                      "date_effet": "2024-01-01", "numero_contrat": "CTR-1"},
    })
    # 0,5 % d'écart -> UNCERTAIN -> verdict INCERTAIN
    assert result["verdict"] == "INCERTAIN"
