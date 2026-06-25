# -*- coding: utf-8 -*-
"""Validation de la config de collecte Prometheus (scope monitoring).

Boucle la boucle d'observabilité : les règles d'alerte (cf. test_alert_rules) ne
servent QUE si Prometheus les CHARGE et ROUTE leurs alertes. Ce test vérifie sur
le `prometheus.yml` RÉEL que :
  * `rule_files` est présent + non vide ET le dossier de règles contient bien des
    fichiers → les alertes sont effectivement chargées (sinon : inertes EN SILENCE) ;
  * `alerting.alertmanagers` a au moins une cible → les alertes sont routées
    (complément de la garde fail-closed alertmanager M7) ;
  * chaque `scrape_config` a un `job_name` UNIQUE + un moyen de trouver des cibles ;
  * les jobs applicatifs (onix-actions, onix-access-gateway) — sources des
    métriques `onix_*` utilisées par les alertes — sont bien présents.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_ROOT = Path(__file__).resolve().parents[2]
_PROM = _ROOT / "monitoring" / "prometheus" / "prometheus.yml"
_RULES_DIR = _ROOT / "monitoring" / "prometheus" / "rules"
_BLACKBOX = _ROOT / "monitoring" / "blackbox" / "blackbox.yml"


def _load():
    return yaml.safe_load(_PROM.read_text(encoding="utf-8"))


def test_rule_files_charge_les_regles():
    doc = _load()
    assert doc.get("rule_files"), "rule_files absent : les règles d'alerte ne seraient pas chargées"
    # ...et il y a bien des fichiers de règles à charger (sinon le glob est vide).
    assert list(_RULES_DIR.glob("*.yml")), f"aucun fichier de règles sous {_RULES_DIR}"


def test_alertmanager_route_les_alertes():
    ams = (_load().get("alerting") or {}).get("alertmanagers") or []
    targets = [
        t for am in ams for sc in (am.get("static_configs") or []) for t in (sc.get("targets") or [])
    ]
    assert targets, "aucune cible alertmanager : les alertes ne seraient routées nulle part"


def test_scrape_configs_bien_formes():
    jobs = _load().get("scrape_configs") or []
    assert jobs, "aucun scrape_config"
    vus = set()
    for job in jobs:
        name = job.get("job_name")
        assert name, "scrape_config sans job_name"
        assert name not in vus, f"job_name dupliqué : {name}"
        vus.add(name)
        static_targets = [
            t for sc in (job.get("static_configs") or []) for t in (sc.get("targets") or [])
        ]
        has_sd = any(k.endswith("_sd_configs") for k in job)
        assert static_targets or has_sd, (
            f"job '{name}' sans cible (ni static_configs avec targets, ni service discovery)"
        )


def test_jobs_applicatifs_onix_presents():
    names = {j.get("job_name") for j in (_load().get("scrape_configs") or [])}
    for requis in ("onix-actions", "onix-access-gateway"):
        assert requis in names, (
            f"job de scrape '{requis}' manquant : les métriques onix_* qu'il expose "
            "ne seraient pas collectées (alertes basées dessus inertes)"
        )


def test_blackbox_modules_bien_formes():
    # Chaque module de sonde a un `prober` (http/tcp/...) — sinon module inutilisable.
    bb = yaml.safe_load(_BLACKBOX.read_text(encoding="utf-8"))
    modules = (bb or {}).get("modules") or {}
    assert modules, "aucun module défini dans blackbox.yml"
    for name, conf in modules.items():
        assert isinstance(conf, dict) and conf.get("prober"), f"module blackbox '{name}' sans prober"


def test_modules_blackbox_references_par_prometheus_sont_definis():
    """Cross-check : tout module blackbox référencé par un job Prometheus
    (`params.module`) DOIT exister dans blackbox.yml — sinon la sonde casse
    (« module not found ») et les alertes de disponibilité (ProbeFailed /
    ActionsServiceDown, basées sur `probe_success`) deviennent inertes EN SILENCE."""
    defined = set((yaml.safe_load(_BLACKBOX.read_text(encoding="utf-8")) or {}).get("modules", {}))
    referenced = set()
    for job in _load().get("scrape_configs") or []:
        referenced.update((job.get("params") or {}).get("module") or [])
    manquants = referenced - defined
    assert not manquants, (
        f"modules blackbox référencés mais NON définis : {sorted(manquants)} "
        f"(définis : {sorted(defined)}) — sondes cassées"
    )
