# -*- coding: utf-8 -*-
"""Validation des dashboards Grafana provisionnés (scope monitoring).

Un dashboard mal formé est un risque de PROVISIONING silencieux :
  * JSON invalide / sans `uid` → Grafana ne le charge pas (ou uid instable) ;
  * deux dashboards au MÊME `uid` → conflit de provisioning : l'un ÉCRASE l'autre
    silencieusement (on perd un tableau de bord sans erreur visible) ;
  * un panel référençant un datasource NON provisionné → panel « datasource not
    found » (graphe vide en prod).

Ce test charge les dashboards RÉELS + les datasources provisionnées et refuse ces
cas. Verrouille la qualité pour tout dashboard futur ajouté par négligence.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")  # pour lire datasources.yml

_ROOT = Path(__file__).resolve().parents[2]
_DASH_DIR = _ROOT / "monitoring" / "grafana" / "dashboards"
_DS_FILE = _ROOT / "monitoring" / "grafana" / "provisioning" / "datasources" / "datasources.yml"
_DASH_FILES = sorted(_DASH_DIR.glob("*.json"))
# Datasources « built-in » toujours disponibles (non provisionnées explicitement).
_BUILTIN_DS = {"grafana", "-- Grafana --", "-- Mixed --", "-- Dashboard --"}


def _provisioned_ds_uids():
    doc = yaml.safe_load(_DS_FILE.read_text(encoding="utf-8"))
    return {ds.get("uid") for ds in (doc or {}).get("datasources", []) if ds.get("uid")}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_des_dashboards_existent():
    # Garde-fou anti-vacuité (si le chemin change, le test ne devient pas trivial).
    assert _DASH_FILES, f"aucun dashboard sous {_DASH_DIR}"


@pytest.mark.parametrize("path", _DASH_FILES, ids=lambda p: p.name)
def test_dashboard_bien_forme(path):
    doc = _load(path)  # lève (JSONDecodeError) si JSON invalide
    assert isinstance(doc.get("uid"), str) and doc["uid"].strip(), f"{path.name} : uid manquant"
    assert isinstance(doc.get("title"), str) and doc["title"].strip(), f"{path.name} : title manquant"
    panels = doc.get("panels")
    assert isinstance(panels, list) and panels, f"{path.name} : aucun panel"


def test_uids_uniques_entre_dashboards():
    vus = {}
    for path in _DASH_FILES:
        uid = _load(path).get("uid")
        assert uid not in vus, (
            f"uid '{uid}' dupliqué entre {vus.get(uid)} et {path.name} "
            "(conflit de provisioning : un dashboard en écraserait un autre)"
        )
        vus[uid] = path.name


def test_panels_referencent_un_datasource_provisionne():
    allowed = _provisioned_ds_uids() | _BUILTIN_DS
    assert _provisioned_ds_uids(), "aucune datasource provisionnée chargée"
    for path in _DASH_FILES:
        for panel in _load(path).get("panels", []):
            ds = panel.get("datasource")
            if not isinstance(ds, dict):
                continue  # datasource absente/héritée/string legacy -> non vérifiée
            uid = ds.get("uid")
            # On ne valide que les uid LITTÉRAUX (pas les variables ${...}).
            if isinstance(uid, str) and uid and not uid.startswith("${"):
                assert uid in allowed, (
                    f"{path.name} : panel '{panel.get('title', '?')}' référence un "
                    f"datasource non provisionné '{uid}' (attendus : {sorted(allowed)})"
                )
