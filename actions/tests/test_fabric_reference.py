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


# --- _select_record : adaptation aux formes de réponse SI (jusqu'ici non testé) ---
def test_select_record_dict_indexe_par_cle():
    from app.fabric_reference import _select_record
    data = {"acme assurances": {"nom_client": "ACME Assurances", "siret": "111"}}
    rec = _select_record(data, "acme assurances")
    assert rec and rec["siret"] == "111"


def test_select_record_enveloppe_clients_par_siret_ou_nom():
    from app.fabric_reference import _select_record
    data = {"clients": [
        {"nom_client": "ACME", "siret": "111"},
        {"nom_client": "Beta", "siret": "222"},
    ]}
    assert _select_record(data, "222")["nom_client"] == "Beta"   # par SIRET
    assert _select_record(data, "acme")["siret"] == "111"        # par nom (normalisé)


def test_select_record_liste_plate():
    from app.fabric_reference import _select_record
    data = [{"nom_client": "ACME", "siret": "111"}, {"nom_client": "Beta", "siret": "222"}]
    assert _select_record(data, "111")["nom_client"] == "ACME"


def test_select_record_dict_de_records_sans_cle_directe():
    from app.fabric_reference import _select_record
    # Dict dont les VALEURS sont des records (non indexé par la clé recherchée).
    data = {"r1": {"nom_client": "ACME", "siret": "111"}, "r2": {"nom_client": "Beta", "siret": "222"}}
    assert _select_record(data, "222")["nom_client"] == "Beta"


def test_select_record_absent_ou_non_dict_renvoie_none():
    from app.fabric_reference import _select_record
    assert _select_record({"clients": [{"nom_client": "ACME", "siret": "111"}]}, "inexistant") is None
    assert _select_record([], "x") is None
    assert _select_record(None, "x") is None


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


def test_extraction_ignore_disclaimer_et_alias_dossier():
    """Régression (contrat POC réel) : la phrase parasite « …Assistant Client 360 »
    ne doit PAS écraser nom_client (étiquette exacte « Client » préférée), et
    « Numero dossier » alimente numero_contrat."""
    from app.audit_engine import extract_canonical_fields
    ocr = {"fields": {
        "DOCUMENT DE TEST (POC Assistant Client 360)": "donnees fictives",
        "Client": "CLIENT BETA",
        "Numero dossier": "BETA-201",
        "Cotisation": "12 500 EUR / an",
    }}
    f = extract_canonical_fields(ocr)
    assert f["nom_client"] == "CLIENT BETA"
    assert f["numero_contrat"] == "BETA-201"
    assert f["cotisation_annuelle"] == "12 500 EUR / an"


def test_fiche_revue_ecart_liste_les_ecarts():
    """Verdict ECART → fiche à revoir, écarts listés (contrat vs SI) + reco."""
    from app.audit_engine import audit, build_review_fiche
    document = {"nom_client": "CLIENT BETA", "cotisation_annuelle": "12 500 EUR / an"}
    reference = {"nom_client": "CLIENT BETA", "cotisation_annuelle": "13000"}
    fiche = build_review_fiche(audit({"document": document, "reference": reference}), client_key="CLIENT BETA")
    assert fiche["verdict"] == "ECART"
    assert fiche["a_revoir"] is True
    assert fiche["nb_ecarts"] >= 1
    cot = [e for e in fiche["ecarts"] if e["champ"] == "cotisation_annuelle"][0]
    assert cot["valeur_contrat"] == "12 500 EUR / an" and cot["valeur_si"] == "13000"
    assert "arbitrage" in fiche["recommandation"].lower()


def test_fiche_revue_conforme_pas_a_revoir():
    from app.audit_engine import audit, build_review_fiche
    document = {"nom_client": "CLIENT GAMMA", "cotisation_annuelle": "8900"}
    reference = {"nom_client": "CLIENT GAMMA", "cotisation_annuelle": "8900"}
    fiche = build_review_fiche(audit({"document": document, "reference": reference}))
    assert fiche["verdict"] == "CONFORME"
    assert fiche["a_revoir"] is False and fiche["nb_ecarts"] == 0


def test_fiche_revue_failsafe_sur_entree_invalide():
    from app.audit_engine import build_review_fiche
    fiche = build_review_fiche(None)  # ne doit jamais lever
    assert fiche["a_revoir"] is False and fiche["ecarts"] == []


def test_fiche_revue_incertain_et_client_non_trouve_a_revoir():
    from app.audit_engine import build_review_fiche, _REVIEW_RECOS
    # Les 2 verdicts (avec ECART) qui pilotent la revue humaine : a_revoir=True AVEC
    # une recommandation SPÉCIFIQUE par verdict (pas le fallback générique) — un
    # INCERTAIN (vérifier les champs ambigus) et un CLIENT_NON_TROUVE (vérifier le
    # référencement) n'appellent PAS la même action.
    for verdict in ("INCERTAIN", "CLIENT_NON_TROUVE"):
        fiche = build_review_fiche({"verdict": verdict, "fields": []})
        assert fiche["a_revoir"] is True, f"{verdict} devrait requérir une revue"
        assert fiche["recommandation"] == _REVIEW_RECOS[verdict]
        assert fiche["recommandation"] != "Vérifier le dossier."  # pas le fallback


def test_garantie_alias_et_reconciliation():
    """Cas métier : la GARANTIE (risque couvert) du contrat doit être cohérente
    avec le SI. Vérifie l'aliasing + MATCH (garantie identique) + MISMATCH (→ ECART)."""
    from app.audit_engine import alias_field, audit
    assert alias_field("Garantie") == "garantie"
    assert alias_field("Risque couvert") == "garantie"
    base_doc = {"nom_client": "CLIENT BETA", "garantie": "Prevoyance collective"}
    # SI identique -> pas d'ecart sur la garantie
    r_ok = audit({"document": base_doc, "reference": {"nom_client": "CLIENT BETA", "garantie": "Prevoyance collective"}})
    g_ok = [f for f in r_ok["fields"] if f["champ"] == "garantie"][0]
    assert g_ok["statut"] == "MATCH"
    # SI divergent (Sante vs Prevoyance) -> ECART
    r_ko = audit({"document": base_doc, "reference": {"nom_client": "CLIENT BETA", "garantie": "Sante collective"}})
    g_ko = [f for f in r_ko["fields"] if f["champ"] == "garantie"][0]
    assert g_ko["statut"] == "MISMATCH"
    assert r_ko["verdict"] == "ECART"


def test_garantie_projetee_depuis_le_si():
    """La garantie doit survivre à map_reference (sinon non comparée)."""
    ref = map_reference({"nom_client": "ACME", "garantie": "Sante collective", "autre": "x"})
    assert ref["garantie"] == "Sante collective"


def test_reconcile_batch_synthese_portefeuille():
    """Réconciliation de PORTEFEUILLE : un lot mixte produit la bonne synthèse."""
    from app.fabric_reference import reconcile_batch
    si = {
        "acme": {"nom_client": "ACME", "cotisation_annuelle": "1000"},
        "beta": {"nom_client": "BETA", "cotisation_annuelle": "2000"},
    }
    items = [
        {"client_key": "acme", "document": {"nom_client": "ACME", "cotisation_annuelle": "1000"}},   # CONFORME
        {"client_key": "beta", "document": {"nom_client": "BETA", "cotisation_annuelle": "9999"}},    # ECART
        {"client_key": "gamma", "document": {"nom_client": "GAMMA", "cotisation_annuelle": "500"}},   # CLIENT_NON_TROUVE
        {"client_key": "x"},                                                                          # INVALIDE (pas de document)
    ]
    rapport = reconcile_batch(items, reference_reader=lambda k: si.get(k))
    s = rapport["synthese"]
    assert s["total"] == 4
    assert s["CONFORME"] == 1
    assert s["ECART"] == 1
    assert s["CLIENT_NON_TROUVE"] == 1
    assert s["invalides"] == 1
    assert s["a_revoir"] == 3            # ECART + CLIENT_NON_TROUVE + INVALIDE (pas CONFORME)
    assert len(rapport["fiches"]) == 4


def test_reconcile_batch_vide_et_fail_safe():
    from app.fabric_reference import reconcile_batch
    # Lot vide -> synthèse à zéro, jamais d'exception.
    r0 = reconcile_batch([])
    assert r0["synthese"]["total"] == 0 and r0["fiches"] == []
    # Items non-dict / sans document -> comptés invalides, sans exception ni I/O SI.
    r1 = reconcile_batch([None, "pas un dict", {"client_key": "y"}], reference_reader=lambda k: None)
    assert r1["synthese"]["total"] == 3 and r1["synthese"]["invalides"] == 3
    # AUDIT (fix) : `items` non-itérable / non-liste -> lot VIDE, jamais de TypeError
    # (un body JSON malformé `"items": 42` ne doit pas faire crasher la réconciliation).
    for mauvais in (42, "abc", None, {"document": {"nom_client": "X"}}):
        r = reconcile_batch(mauvais)
        assert r["synthese"]["total"] == 0 and r["fiches"] == []


def test_fetch_retry_apres_blip_reseau(monkeypatch):
    """Un blip réseau momentané (1re lecture KO) ⇒ la 2e tentative réussit."""
    import app.fabric_reference as fr
    monkeypatch.setattr(fr, "_sleep", lambda s: None)  # pas de vraie pause en test
    monkeypatch.setenv("ONIX_FABRIC_READ_ATTEMPTS", "3")
    calls = {"n": 0}

    def flaky(key):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("OneLake blip")
        return {"nom_client": "ACME", "siret": key}

    ref = fr.fetch_client_reference("123", reader=flaky)
    assert ref is not None and ref["nom_client"] == "ACME"
    assert calls["n"] == 2  # a bien retenté une fois


def test_fetch_failclosed_apres_toutes_tentatives(monkeypatch):
    """Toutes les tentatives KO ⇒ None (fail-closed, pas d'invention)."""
    import app.fabric_reference as fr
    monkeypatch.setattr(fr, "_sleep", lambda s: None)
    monkeypatch.setenv("ONIX_FABRIC_READ_ATTEMPTS", "2")
    calls = {"n": 0}

    def always_ko(key):
        calls["n"] += 1
        raise RuntimeError("OneLake down")

    assert fr.fetch_client_reference("123", reader=always_ko) is None
    assert calls["n"] == 2  # tenté 2 fois puis abandon


def test_read_attempts_et_timeout_bornes(monkeypatch):
    import app.fabric_reference as fr
    monkeypatch.setenv("ONIX_FABRIC_READ_ATTEMPTS", "99")
    assert fr._read_attempts() == 5  # borné à 5
    monkeypatch.setenv("ONIX_FABRIC_READ_ATTEMPTS", "abc")
    assert fr._read_attempts() == 2  # défaut si illisible
    monkeypatch.setenv("ONIX_FABRIC_READ_TIMEOUT", "1")
    assert fr._read_timeout() == 3  # borné au plancher 3 s


def test_storage_token_fallback_statique(monkeypatch):
    from app.fabric_reference import _storage_token
    monkeypatch.delenv("ONIX_FABRIC_SP_CLIENT_ID", raising=False)
    monkeypatch.setenv("ONIX_FABRIC_TOKEN", "tok-statique")
    assert _storage_token() == "tok-statique"


def test_storage_token_none_si_rien_configure(monkeypatch):
    from app.fabric_reference import _storage_token
    for v in ("ONIX_FABRIC_SP_CLIENT_ID", "ONIX_FABRIC_SP_CLIENT_SECRET",
              "ONIX_FABRIC_SP_TENANT", "ONIX_FABRIC_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    assert _storage_token() is None


def test_reference_non_configuree_par_defaut(monkeypatch):
    monkeypatch.delenv("ONIX_FABRIC_REFERENCE_URL", raising=False)
    assert fabric_reference_configured() is False
    # Sans source configurée, le lecteur par défaut renvoie None (fail-closed).
    assert fetch_client_reference("acme") is None
