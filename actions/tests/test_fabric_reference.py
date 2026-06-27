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


def test_reconcile_batch_synthese_coherente_et_ordre():
    """Lot mixte incluant un INCERTAIN : la synthèse est COHÉRENTE (les compteurs
    somment au total) et l'ORDRE des fiches suit l'ordre des items (le back-office
    doit pouvoir remonter chaque fiche à sa ligne de portefeuille)."""
    from app.fabric_reference import reconcile_batch
    si = {
        "acme": {"nom_client": "ACME", "cotisation_annuelle": "1000"},
        "beta": {"nom_client": "BETA", "cotisation_annuelle": "2000"},
        "delta": {"nom_client": "DELTA", "cotisation_annuelle": "2000"},
    }
    items = [
        {"client_key": "acme", "document": {"nom_client": "ACME", "cotisation_annuelle": "1000"}},   # CONFORME
        {"client_key": "beta", "document": {"nom_client": "BETA", "cotisation_annuelle": "9999"}},    # ECART
        {"client_key": "delta", "document": {"nom_client": "DELTA", "cotisation_annuelle": "2010"}},  # INCERTAIN (0,5 %)
        {"client_key": "zz", "document": {"nom_client": "ZZ"}},                                       # CLIENT_NON_TROUVE
        {"client_key": "x"},                                                                          # INVALIDE
    ]
    rapport = reconcile_batch(items, reference_reader=lambda k: si.get(k))
    s = rapport["synthese"]
    assert s["INCERTAIN"] == 1
    # Cohérence : aucun item perdu ni compté deux fois.
    assert s["CONFORME"] + s["ECART"] + s["INCERTAIN"] + s["CLIENT_NON_TROUVE"] + s["invalides"] == s["total"] == 5
    # Ordre préservé (vérifié via la séquence de verdicts).
    assert [f["verdict"] for f in rapport["fiches"]] == [
        "CONFORME", "ECART", "INCERTAIN", "CLIENT_NON_TROUVE", "INVALIDE"
    ]
    # Une fiche valide porte bien le contrat canonique.
    valides = [f for f in rapport["fiches"] if f["verdict"] != "INVALIDE"]
    for f in valides:
        assert {"client", "verdict", "a_revoir", "nb_ecarts", "ecarts", "recommandation"} <= set(f)


def test_reconcile_batch_pas_d_io_si_sur_items_invalides():
    """Un item invalide (sans document) NE déclenche AUCUNE lecture du SI : le
    `continue` intervient avant `fetch_client_reference` (pas d'I/O gaspillée)."""
    from app.fabric_reference import reconcile_batch
    appels = []

    def reader(key):
        appels.append(key)
        return None

    items = [
        {"client_key": "x"},                                     # invalide -> pas de lecture SI
        {"client_key": "y", "document": {}},                     # document vide -> invalide -> idem
        {"client_key": "ok", "document": {"nom_client": "OK"}},  # valide -> 1 seule lecture SI
    ]
    reconcile_batch(items, reference_reader=reader)
    assert appels == ["ok"]


def test_batch_to_csv_structure_et_echappement():
    """Export CSV : en-tête + 1 ligne/fiche dans l'ORDRE ; un nom avec virgule et
    guillemets est correctement échappé (échappement CSV natif)."""
    from app.fabric_reference import reconcile_batch, batch_to_csv
    si = {"acme": {"nom_client": 'ACME, Inc "AC"', "cotisation_annuelle": "1000"}}
    items = [
        {"client_key": "acme", "document": {"nom_client": 'ACME, Inc "AC"', "cotisation_annuelle": "9999"}},  # ECART
        {"client_key": "x"},  # INVALIDE
    ]
    rapport = reconcile_batch(items, reference_reader=lambda k: si.get(k))
    lignes = batch_to_csv(rapport).splitlines()
    assert lignes[0] == "client,verdict,a_revoir,nb_ecarts,recommandation"
    assert len(lignes) == 3
    assert '"ACME, Inc ""AC"""' in lignes[1]          # virgule + guillemets protégés
    assert ",ECART," in lignes[1] and ",INVALIDE," in lignes[2]  # ordre préservé


def test_batch_to_csv_anti_injection_formule():
    """Sécurité : une valeur commençant par une formule (= + - @) est neutralisée
    (préfixe apostrophe) pour qu'Excel/Sheets l'affiche en texte, pas l'évalue."""
    from app.fabric_reference import batch_to_csv
    rapport = {"fiches": [{
        "client": "=cmd|' /c calc'!A1", "verdict": "CONFORME", "a_revoir": False,
        "nb_ecarts": 0, "recommandation": "@SUM(1+1)",
    }]}
    out = batch_to_csv(rapport)
    assert "'=cmd" in out and "'@SUM" in out


def test_csv_safe_neutralise_tab_et_cr():
    """AUDIT : le jeu OWASP complet inclut TAB et CR en tête — eux aussi neutralisés."""
    from app.fabric_reference import _csv_safe
    assert _csv_safe("\t=cmd").startswith("'")   # tabulation en tête
    assert _csv_safe("\r=cmd").startswith("'")   # retour chariot en tête
    assert _csv_safe("=cmd").startswith("'")     # non-régression (formule classique)
    assert _csv_safe("Dupont") == "Dupont"       # valeur normale inchangée
    assert _csv_safe(None) == ""                 # None -> vide


def test_batch_to_csv_crlf_ne_forge_pas_de_ligne():
    """SÉCURITÉ : un nom contenant CRLF ne crée PAS de fausse ligne CSV (csv.writer
    quote la valeur) — re-parsé, on retrouve EXACTEMENT 1 ligne data par fiche."""
    import csv as _csv
    import io as _io
    from app.fabric_reference import batch_to_csv
    rapport = {"fiches": [
        {"client": "Evil\r\nFAKE,row,injection", "verdict": "CONFORME", "a_revoir": False,
         "nb_ecarts": 0, "recommandation": "ok"},
        {"client": "Normal", "verdict": "ECART", "a_revoir": True, "nb_ecarts": 1,
         "recommandation": "arbitrer"},
    ]}
    rows = list(_csv.reader(_io.StringIO(batch_to_csv(rapport))))
    assert len(rows) == 3                              # en-tête + 2 fiches (pas plus)
    assert rows[0][0] == "client"
    assert rows[2][0] == "Normal" and rows[2][1] == "ECART"  # 2e fiche non décalée


def test_batch_to_csv_roundtrip_csv_reader():
    """Round-trip : csv.reader redonne fidèlement en-tête + lignes (ordre + valeurs)."""
    import csv as _csv
    import io as _io
    from app.fabric_reference import reconcile_batch, batch_to_csv
    si = {"acme": {"nom_client": "ACME", "cotisation_annuelle": "1000"}}
    items = [
        {"client_key": "acme", "document": {"nom_client": "ACME", "cotisation_annuelle": "1000"}},  # CONFORME
        {"client_key": "x"},  # INVALIDE
    ]
    rapport = reconcile_batch(items, reference_reader=lambda k: si.get(k))
    rows = list(_csv.reader(_io.StringIO(batch_to_csv(rapport))))
    assert rows[0] == ["client", "verdict", "a_revoir", "nb_ecarts", "recommandation"]
    assert rows[1][0] == "ACME" and rows[1][1] == "CONFORME"
    assert rows[2][0] == "x" and rows[2][1] == "INVALIDE"


def test_batch_to_csv_pas_de_bom_dans_la_fonction():
    """Le BOM est ajouté SEULEMENT à l'endpoint : la fonction reste pure (re-parseable)
    → csv.reader lit `client` en 1re colonne, sans BOM parasite."""
    import csv as _csv
    import io as _io
    from app.fabric_reference import batch_to_csv
    out = batch_to_csv({"fiches": []})
    assert not out.startswith(chr(0xFEFF))            # pas de BOM dans la fonction
    rows = list(_csv.reader(_io.StringIO(out)))
    assert rows[0][0] == "client"                     # 1re colonne propre


def test_batch_to_csv_failsafe_rapport_malforme():
    """Fail-closed : rapport None / fiches non-liste / fiches non-dict -> CSV avec
    UNIQUEMENT l'en-tête, jamais d'exception."""
    from app.fabric_reference import batch_to_csv
    for mauvais in (None, {}, {"fiches": "pas une liste"}, {"fiches": [None, 42]}):
        lignes = batch_to_csv(mauvais).splitlines()
        assert lignes == ["client,verdict,a_revoir,nb_ecarts,recommandation"]


def test_client_360_agregation_sources_injectees():
    """Agrégation : réf SI + tâches ouvertes + usage, via sources INJECTÉES (offline)."""
    from app.fabric_reference import client_360
    ref = {"nom_client": "ACME", "cotisation_annuelle": "1000"}
    taches = [{"task_id": "t1", "status": "open"}, {"task_id": "t2", "status": "open"}]
    vue = client_360(
        "acme",
        reference_reader=lambda ck, **kw: ref,
        tasks_lister=lambda ck: taches,
        usage_counter=lambda ck: 7,
    )
    assert vue["client_key"] == "acme"
    assert vue["reference_trouvee"] is True and vue["reference"]["nom_client"] == "ACME"
    assert vue["nb_taches_ouvertes"] == 2 and vue["taches_ouvertes"] == taches
    assert vue["nb_evenements_usage"] == 7


def test_client_360_client_absent_fail_closed():
    """Client absent du SI -> reference_trouvee False, reference None (pas d'invention)."""
    from app.fabric_reference import client_360
    vue = client_360(
        "inconnu",
        reference_reader=lambda ck, **kw: None,
        tasks_lister=lambda ck: [],
        usage_counter=lambda ck: 0,
    )
    assert vue["reference_trouvee"] is False and vue["reference"] is None
    assert vue["nb_taches_ouvertes"] == 0 and vue["nb_evenements_usage"] == 0


def test_client_360_failsafe_sources_qui_levent():
    """Fail-safe : une source qui LÈVE -> champ vide/0, JAMAIS d'exception propagée."""
    from app.fabric_reference import client_360

    def boom(ck):
        raise RuntimeError("source down")

    vue = client_360("x", reference_reader=lambda ck, **kw: None,
                     tasks_lister=boom, usage_counter=boom)
    assert vue["nb_taches_ouvertes"] == 0 and vue["taches_ouvertes"] == []
    assert vue["nb_evenements_usage"] == 0
    assert vue["reference_trouvee"] is False


def test_client_360_isolation_source_taches_qui_leve():
    """Isolation INDIVIDUELLE : la source TÂCHES qui lève n'empêche PAS référence ni
    usage (les 3 sources sont dans des try/except SÉPARÉS)."""
    from app.fabric_reference import client_360

    def boom(ck):
        raise RuntimeError("tasks down")

    vue = client_360(
        "acme",
        reference_reader=lambda ck, **kw: {"nom_client": "ACME"},
        tasks_lister=boom,
        usage_counter=lambda ck: 5,
    )
    assert vue["reference_trouvee"] is True             # référence OK malgré l'échec tâches
    assert vue["nb_evenements_usage"] == 5              # usage OK malgré l'échec tâches
    assert vue["taches_ouvertes"] == [] and vue["nb_taches_ouvertes"] == 0  # tâches fail-safe


def test_client_360_defauts_base_isolee(monkeypatch, tmp_path):
    """Les VRAIS helpers défaut sur base SQLite isolée : tâche + usages d'un client sont
    retrouvés par HASH ; un autre client / une clé vide -> 0 (isolation + garde clé)."""
    import importlib
    monkeypatch.setenv("ONIX_ACTIONS_DB", str(tmp_path / "c360.sqlite"))
    monkeypatch.delenv("ONIX_ACTIONS_DB_URL", raising=False)
    monkeypatch.setenv("ONIX_ACTIONS_AUDIT_HMAC_KEY", "cle-de-test-32-octets-minimum!!")
    import app.db as db
    import app.admin_state as admin_state
    import app.tasks as tasks
    import app.usage_tracker as ut
    import app.fabric_reference as fr
    for m in (db, admin_state, tasks, ut, fr):
        importlib.reload(m)
    tasks.init_db()
    ut.init_db()   # crée la table usage_events dans la base isolée

    # Client "X" : 1 tâche OUVERTE + 2 événements d'usage (par client_id="X").
    tasks.create_task(title="Relancer X", client_id="X")
    ut.track("audit_documentaire_started", client_id="X", action_name="a")
    ut.track("audit_documentaire_completed", client_id="X", action_name="a")

    vue = fr.client_360("X")  # SANS sources injectées -> défauts réels
    assert vue["nb_taches_ouvertes"] == 1     # tâche retrouvée par hash(X)
    assert vue["nb_evenements_usage"] == 2     # 2 usages comptés par hash(X)
    assert vue["reference_trouvee"] is False   # SI non configuré -> None (fail-closed)

    # Isolation par hash : un autre client / une clé vide ne matchent RIEN.
    assert fr.client_360("Y")["nb_taches_ouvertes"] == 0
    assert fr.client_360("Y")["nb_evenements_usage"] == 0
    vide = fr.client_360("")
    assert vide["nb_taches_ouvertes"] == 0 and vide["nb_evenements_usage"] == 0


def test_client_360_taches_data_minimisation(monkeypatch, tmp_path):
    """AUDIT data-minimisation : la vue 360 ne remonte PAS `notes` (champ libre PII)
    ni les hash internes des tâches — seulement le résumé d'identification."""
    import importlib
    monkeypatch.setenv("ONIX_ACTIONS_DB", str(tmp_path / "c360min.sqlite"))
    monkeypatch.delenv("ONIX_ACTIONS_DB_URL", raising=False)
    monkeypatch.setenv("ONIX_ACTIONS_AUDIT_HMAC_KEY", "cle-de-test-32-octets-minimum!!")
    import app.db as db
    import app.admin_state as admin_state
    import app.tasks as tasks
    import app.fabric_reference as fr
    for m in (db, admin_state, tasks, fr):
        importlib.reload(m)
    tasks.init_db()
    # Tâche avec des NOTES sensibles (champ libre, PII) -> ne doivent PAS fuiter.
    tasks.create_task(title="Relancer X", client_id="X", notes="NIR 1 85 12 75 116 001 25")
    t = fr.client_360("X")["taches_ouvertes"][0]
    assert set(t.keys()) == {"task_id", "title", "due_date", "status"}
    assert "notes" not in t and "client_id_hash" not in t and "owner_hash" not in t


def test_portfolio_360_lignes_et_totaux():
    """Tableau de bord portefeuille : résumés par client + totaux (sources injectées)."""
    from app.fabric_reference import portfolio_360
    refs = {"a": {"nom_client": "A"}}                       # "a" a une réf, "b" non
    taches = {"a": [{"task_id": "t1"}], "b": [{"task_id": "t2"}, {"task_id": "t3"}]}
    usages = {"a": 4, "b": 1}
    rapport = portfolio_360(
        ["a", "b"],
        reference_reader=lambda ck, **kw: refs.get(ck),
        tasks_lister=lambda ck: taches.get(ck, []),
        usage_counter=lambda ck: usages.get(ck, 0),
    )
    lignes = rapport["lignes"]
    assert [l["client_key"] for l in lignes] == ["a", "b"]   # ordre préservé
    assert lignes[0] == {"client_key": "a", "reference_trouvee": True,
                         "nb_taches_ouvertes": 1, "nb_evenements_usage": 4}
    assert lignes[1] == {"client_key": "b", "reference_trouvee": False,
                         "nb_taches_ouvertes": 2, "nb_evenements_usage": 1}
    assert rapport["totaux"] == {"nb_clients": 2, "nb_avec_reference": 1,
                                 "total_taches_ouvertes": 3, "total_usage": 5}
    # Data-minimisation : ni `reference` complète ni `taches_ouvertes` dans les lignes.
    assert "reference" not in lignes[0] and "taches_ouvertes" not in lignes[0]


def test_portfolio_360_dedoublonne_et_ignore_cles_invalides():
    from app.fabric_reference import portfolio_360
    rapport = portfolio_360(
        ["a", "a", "b", "", None, 42, "a"],   # doublons + clés vides/non-str
        reference_reader=lambda ck, **kw: None,
        tasks_lister=lambda ck: [],
        usage_counter=lambda ck: 0,
    )
    assert [l["client_key"] for l in rapport["lignes"]] == ["a", "b"]
    assert rapport["totaux"]["nb_clients"] == 2


def test_portfolio_360_non_iterable_vide():
    from app.fabric_reference import portfolio_360
    for mauvais in (None, 42, "abc", {"a": 1}):
        rapport = portfolio_360(mauvais)
        assert rapport["lignes"] == [] and rapport["totaux"]["nb_clients"] == 0


def test_portfolio_360_to_csv_structure_et_anti_injection():
    from app.fabric_reference import portfolio_360_to_csv
    rapport = {"lignes": [
        {"client_key": "=cmd", "reference_trouvee": True, "nb_taches_ouvertes": 1, "nb_evenements_usage": 2},
        {"client_key": "B,Inc", "reference_trouvee": False, "nb_taches_ouvertes": 0, "nb_evenements_usage": 0},
    ]}
    lignes = portfolio_360_to_csv(rapport).splitlines()
    assert lignes[0] == "client_key,reference_trouvee,nb_taches_ouvertes,nb_evenements_usage"
    assert len(lignes) == 3
    out = portfolio_360_to_csv(rapport)
    assert "'=cmd" in out                  # anti-injection de formule
    assert '"B,Inc"' in out                # virgule échappée
    # Fail-safe : rapport malformé -> en-tête seul.
    assert portfolio_360_to_csv(None).splitlines() == [
        "client_key,reference_trouvee,nb_taches_ouvertes,nb_evenements_usage"]


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
