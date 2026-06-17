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


def test_normalize_name_and_contract():
    assert normalize_name("Société Générale") == "SOCIETE GENERALE"
    assert normalize_contract("ctr-2024/AB.12") == "CTR2024AB12"


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
