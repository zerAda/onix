# -*- coding: utf-8 -*-
"""Tests du choix de backend de stockage objet (`app.objstore`) — sûreté HA.

Les opérations S3 sont déjà couvertes (client factice dans `test_security_rgpd`).
Ici on verrouille le CŒUR de sélection, pur (sans boto3) :
  * `backend()` ne bascule en S3 que sur un alias EXPLICITE (s3/minio/object,
    insensible casse/espaces) ; toute autre valeur (typo, vide) retombe en
    **local** — fail-safe : on ne tente JAMAIS S3 par accident (sinon échec au
    démarrage faute de creds, ou comportement multi-réplica inattendu) ;
  * `object_key` est déterministe (miroir du chemin local) — un changement
    casserait le download cross-réplica ;
  * `bucket_name` retombe sur le bucket par défaut si non configuré.
"""
from __future__ import annotations

import pytest

from app import objstore


def test_backend_defaut_local_si_absent(monkeypatch):
    monkeypatch.delenv("ONIX_OBJECT_STORE", raising=False)
    assert objstore.backend() == "local"
    assert objstore.is_s3() is False


@pytest.mark.parametrize("valeur", ["amazon", "true", "1", "azure", "s3 bucket", "loc"])
def test_backend_valeur_inconnue_retombe_en_local(monkeypatch, valeur):
    # Fail-safe : une valeur non reconnue NE doit PAS activer S3 par accident.
    monkeypatch.setenv("ONIX_OBJECT_STORE", valeur)
    assert objstore.backend() == "local", f"{valeur!r} aurait dû defaulter en local"
    assert objstore.is_s3() is False


@pytest.mark.parametrize("valeur", ["s3", "S3", "minio", " MinIO ", "object"])
def test_backend_alias_explicites_activent_s3(monkeypatch, valeur):
    monkeypatch.setenv("ONIX_OBJECT_STORE", valeur)
    assert objstore.is_s3() is True, f"{valeur!r} aurait dû activer S3"


def test_object_key_deterministe():
    # Miroir exact du chemin local jobs/<job_id>/<filename>.
    assert objstore.object_key("job-1", "Fiche_RDV.docx") == "jobs/job-1/Fiche_RDV.docx"


def test_bucket_name_defaut_et_override(monkeypatch):
    monkeypatch.delenv("ONIX_S3_BUCKET", raising=False)
    assert objstore.bucket_name() == "onyx-file-store-bucket"
    monkeypatch.setenv("ONIX_S3_BUCKET", "mon-bucket")
    assert objstore.bucket_name() == "mon-bucket"
