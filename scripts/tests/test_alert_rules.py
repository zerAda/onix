# -*- coding: utf-8 -*-
"""Validation de la QUALITÉ des règles d'alerte Prometheus (scope monitoring).

Une règle d'alerte mal formée est un risque OPÉRATIONNEL silencieux :
  * sans label `severity`, l'alertmanager ne sait pas la ROUTER → elle part dans
    le vide (receiver par défaut) : personne n'est notifié ;
  * sans `summary`/`description`, la notification est sans contexte (inactionnable).

Ce test charge les fichiers de règles RÉELS (ceux montés en production) et refuse
toute règle d'alerte incomplète. Les recording rules (`record:`) sont validées à
part (elles n'ont ni severity ni annotations, c'est normal). Verrouille la qualité
pour toute règle future ajoutée par négligence.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")  # pyyaml présent dans le venv de test

_RULES_DIR = Path(__file__).resolve().parents[2] / "monitoring" / "prometheus" / "rules"
_RULE_FILES = sorted(_RULES_DIR.glob("*.yml"))
# Niveaux routables par l'alertmanager (toute autre valeur = alerte non routée).
_SEVERITIES = {"info", "warning", "critical"}


def _iter_rules():
    """Rend (fichier, groupe, règle) pour chaque règle de chaque fichier de règles."""
    out = []
    for path in _RULE_FILES:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for group in (doc or {}).get("groups", []):
            for rule in group.get("rules", []):
                out.append((path.name, group.get("name", "?"), rule))
    return out


def test_des_fichiers_de_regles_existent():
    # Garde-fou anti-vacuité : si le chemin change, le test ne doit pas devenir
    # trivialement vrai (0 règle paramétrée = 0 assertion).
    assert _RULE_FILES, f"aucun fichier de règles trouvé sous {_RULES_DIR}"
    assert _iter_rules(), "aucune règle chargée (structure groups/rules inattendue ?)"


@pytest.mark.parametrize("fichier,groupe,regle", _iter_rules())
def test_chaque_regle_est_bien_formee(fichier, groupe, regle):
    # Une règle est SOIT une alerte (`alert:`) SOIT une recording rule (`record:`).
    is_alert = "alert" in regle
    is_record = "record" in regle
    assert is_alert ^ is_record, (
        f"{fichier}/{groupe} : règle ni alert ni record (ou les deux) : {regle!r}"
    )
    # `expr` obligatoire dans tous les cas (une règle sans expression est invalide).
    assert str(regle.get("expr", "")).strip(), f"{fichier}/{groupe} : expr manquante"

    if is_alert:
        nom = regle["alert"]
        labels = regle.get("labels") or {}
        annotations = regle.get("annotations") or {}
        # severity présent ET routable — sinon l'alerte n'atteint aucun receiver.
        sev = labels.get("severity")
        assert sev in _SEVERITIES, (
            f"{fichier}/{nom} : severity {sev!r} absente ou hors {_SEVERITIES} "
            "(l'alerte ne serait pas routée)"
        )
        # Notification actionnable : summary ET description non vides.
        assert str(annotations.get("summary", "")).strip(), f"{fichier}/{nom} : summary manquant"
        assert str(annotations.get("description", "")).strip(), (
            f"{fichier}/{nom} : description manquante"
        )
