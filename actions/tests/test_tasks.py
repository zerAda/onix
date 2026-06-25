# -*- coding: utf-8 -*-
"""Tests unitaires du cycle de vie des tâches locales (`app.tasks`).

Jusqu'ici `tasks` n'était exercé que via l'endpoint HTTP. On verrouille en direct :
  * `create_task` exige un titre non vide (fail-closed) et naît au statut `open` ;
  * une tâche créée est bien listée (global + filtre `open`) et ABSENTE du filtre
    `done` ;
  * `list_tasks` REFUSE un filtre `status` hors {open,done,cancelled} (fail-closed :
    pas de requête à l'aveugle sur un statut arbitraire).

Isolation : base SQLite temporaire + rechargement des modules (admin_state EN
PREMIER puis ses dépendants), `init_db()` pour créer la table.
"""
from __future__ import annotations

import importlib

import pytest


def _reload(monkeypatch, tmp_path):
    monkeypatch.setenv("ONIX_ACTIONS_DB", str(tmp_path / "tasks.sqlite"))
    monkeypatch.delenv("ONIX_ACTIONS_DB_URL", raising=False)
    import app.db as db
    import app.admin_state as admin_state
    import app.tasks as tasks

    for m in (db, admin_state, tasks):
        importlib.reload(m)
    tasks.init_db()
    return tasks


def test_create_task_titre_requis(monkeypatch, tmp_path):
    tasks = _reload(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        tasks.create_task(title="   ")  # titre vide après strip -> refus


def test_create_naissance_open_et_listing_par_statut(monkeypatch, tmp_path):
    tasks = _reload(monkeypatch, tmp_path)
    rec = tasks.create_task(title="Relancer ACME", client_id="acme")
    assert rec["status"] == "open"

    ids_all = [t["task_id"] for t in tasks.list_tasks()]
    ids_open = [t["task_id"] for t in tasks.list_tasks(status="open")]
    ids_done = [t["task_id"] for t in tasks.list_tasks(status="done")]
    assert rec["task_id"] in ids_all
    assert rec["task_id"] in ids_open
    assert rec["task_id"] not in ids_done  # une tâche 'open' n'est pas 'done'


def test_list_tasks_status_filtre_invalide_refuse(monkeypatch, tmp_path):
    tasks = _reload(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        tasks.list_tasks(status="bidon")
