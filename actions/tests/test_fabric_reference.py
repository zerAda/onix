# -*- coding: utf-8 -*-
"""Tests du POC **réconciliation contrat ↔ SI Fabric** (`app.fabric_reference`).

Vérifie le maillon manquant d'AC360 : la référence client est LUE dans le SI
Fabric (lecteur injectable, 100 % offline ici), projetée sur les champs canoniques
d'`audit_engine`, puis comparée au document → verdict. Fail-closed : client absent
/ source non configurée ⇒ référence None ⇒ verdict `CLIENT_NON_TROUVE`.
"""
from __future__ import annotations

from app.audit_engine import audit
from app.fabric_reference import (
    fabric_reference_configured,
    fetch_client_reference,
    map_reference,
)


def test_map_reference_projette_les_champs_canoniques():
    raw = {
        "Nom_Client": "ACME Assurances",
        "SIRET": "12345678900011",
        "Plafond_Hospitalisation": "500",
        "Date_Effet": "2025-01-01",
        "Numero_Contrat": "C-001",
        "colonne_si_inutile": "ignorée",
    }
    ref = map_reference(raw)
    assert ref["nom_client"] == "ACME Assurances"
    assert ref["siret"] == "12345678900011"
    assert ref["plafond_hospitalisation"] == "500"
    assert ref["numero_contrat"] == "C-001"
    assert "colonne_si_inutile" not in ref


def test_fetch_via_lecteur_injecte_par_siret():
    si = {"clients": [
        {"nom_client": "ACME Assurances", "siret": "12345678900011", "plafond_hospitalisation": "500"},
        {"nom_client": "Beta SA", "siret": "98765432100022", "plafond_hospitalisation": "300"},
    ]}

    def reader(key):  # simule la lecture OneLake (filtre par siret/nom)
        for r in si["clients"]:
            if key in (r["siret"].lower(), r["nom_client"].lower()):
                return r
        return None

    ref = fetch_client_reference("12345678900011", reader=reader)
    assert ref is not None and ref["nom_client"] == "ACME Assurances"


def test_fetch_client_absent_du_si_est_none():
    # Fail-closed : client introuvable dans le SI ⇒ None (→ CLIENT_NON_TROUVE).
    assert fetch_client_reference("00000000000000", reader=lambda k: None) is None


def test_fetch_cle_vide_est_none():
    assert fetch_client_reference("", reader=lambda k: {"nom_client": "X"}) is None


def test_fetch_lecteur_en_erreur_est_none():
    def boom(key):
        raise RuntimeError("OneLake injoignable")

    assert fetch_client_reference("acme", reader=boom) is None


def test_reconciliation_verdict_ecart_sur_divergence():
    """Le flux complet : document OCRisé vs référence Fabric → verdict ECART."""
    document = {"nom_client": "ACME Assurances", "plafond_hospitalisation": "500"}
    reference = {"nom_client": "ACME Assurances", "plafond_hospitalisation": "800"}  # écart réel
    result = audit({"document": document, "reference": reference})
    assert result["verdict"] in ("ECART", "INCERTAIN")  # divergence détectée


def test_reconciliation_verdict_conforme_si_aligne():
    document = {"nom_client": "ACME Assurances", "plafond_hospitalisation": "500"}
    reference = {"nom_client": "ACME Assurances", "plafond_hospitalisation": "500"}
    assert audit({"document": document, "reference": reference})["verdict"] == "CONFORME"


def test_reconciliation_ecart_cotisation_via_si():
    """Cas métier GEREP : la cotisation du contrat (SharePoint) diverge du SI Fabric
    → ECART. Vérifie que `cotisation_annuelle` survit à `map_reference` puis est
    comparée par l'audit (contrat 12 500 €/an vs SI 13 000 €)."""
    si_record = {
        "nom_client": "CLIENT BETA", "numero_contrat": "BETA-201",
        "date_effet": "01/01/2026", "cotisation_annuelle": "13000",
    }
    ref = fetch_client_reference(
        "client beta",
        reader=lambda k: si_record if k in ("client beta", "beta-201") else None,
    )
    assert ref is not None and ref["cotisation_annuelle"] == "13000"
    document = {
        "nom_client": "CLIENT BETA", "numero_contrat": "BETA-201",
        "date_effet": "01/01/2026", "cotisation_annuelle": "12 500 EUR / an",
    }
    result = audit({"document": document, "reference": ref})
    assert result["verdict"] == "ECART"
    cot = [f for f in result["fields"] if f["champ"] == "cotisation_annuelle"][0]
    assert cot["statut"] == "MISMATCH"


def test_reference_non_configuree_par_defaut(monkeypatch):
    monkeypatch.delenv("ONIX_FABRIC_REFERENCE_URL", raising=False)
    assert fabric_reference_configured() is False
    # Sans source configurée, le lecteur par défaut renvoie None (fail-closed).
    assert fetch_client_reference("acme") is None
