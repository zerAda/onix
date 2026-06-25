# -*- coding: utf-8 -*-
"""Tests des garde-fous anti Path-Traversal / IDOR de `app.docgen` (sécurité).

Le sanitiseur `safe_filename` (nom des fiches générées) et les gardes de download
(`_safe_download_inputs`, `resolve_download`) sont SÉCURITÉ-CRITIQUES : un nom de
client ou un paramètre de téléchargement malveillant ne doit jamais permettre de
sortir du répertoire des jobs. Jusqu'ici non testés en direct avec des entrées
adverses — on verrouille ici les DEUX couches de défense.
"""
from __future__ import annotations

import pytest

from app import docgen


def test_safe_filename_neutralise_path_traversal():
    for malicious in ("../../etc/passwd", "..\\..\\windows\\system32", "a/b/c",
                      "....//....//", "../", "/etc/shadow"):
        out = docgen.safe_filename(malicious)
        assert ".." not in out
        assert "/" not in out and "\\" not in out
        assert out  # jamais vide (fallback client_inconnu)


def test_safe_filename_allowlist_stricte_et_controle():
    out = docgen.safe_filename("Jean:Dupont*<x>|y\x00z;rm -rf")
    # Seuls [a-zA-Z0-9_-] subsistent (les espaces deviennent _).
    assert all(c.isalnum() or c in "_-" for c in out)
    assert "\x00" not in out and ";" not in out


def test_safe_filename_accents_vide_et_borne():
    assert docgen.safe_filename("Élodie Côté") == "Elodie_Cote"   # accents -> ASCII
    assert docgen.safe_filename("") == "client_inconnu"
    assert docgen.safe_filename("   ") == "client_inconnu"
    assert docgen.safe_filename("../") == "client_inconnu"        # tout retiré -> fallback
    assert len(docgen.safe_filename("a" * 200)) == 64             # longueur bornée


def test_safe_download_inputs_rejette_traversal_et_idor():
    for bad in ("../secret.docx", "a/b.docx", "a\\b.docx", "..\\x"):
        with pytest.raises(PermissionError):
            docgen._safe_download_inputs("job-1", bad)
    # job_id entièrement retiré par l'allowlist -> refus (jamais de job_id vide).
    with pytest.raises(PermissionError):
        docgen._safe_download_inputs("/../", "ok.docx")
    # Entrées valides -> job_id sanitisé renvoyé.
    assert docgen._safe_download_inputs("job-1", "Fiche.docx") == "job-1"


def test_resolve_download_confine_sous_jobs_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("ONIX_JOBS_DIR", str(tmp_path))
    # Filename de traversal -> PermissionError (rejeté AVANT toute résolution).
    with pytest.raises(PermissionError):
        docgen.resolve_download("job-1", "../escape.docx")
    # Nom valide mais fichier inexistant -> FileNotFoundError (et NON Permission).
    with pytest.raises(FileNotFoundError):
        docgen.resolve_download("job-1", "absent.docx")
