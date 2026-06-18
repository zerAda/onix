"""retention — Rétention & effacement (RGPD art. 5-1-e & art. 17) (onix-actions).

WS2 — deux mécanismes complémentaires :

  1. **Purge par âge (TTL)** : supprime les données dont la date dépasse une
     durée de conservation configurable. Cible : `usage_events`, `tasks`
     terminées, et fichiers `.docx` générés (`/data/jobs`). TTL via
     `ONIX_RETENTION_DAYS` (défaut 365). Idempotent et borné.

  2. **Effacement ciblé par sujet** (droit à l'effacement, art. 17) : supprime
     toutes les traces rattachées à un sujet, désigné par le HASH de son
     identifiant (UPN/client). On n'efface JAMAIS le journal d'audit
     administrateur chaîné (obligation de traçabilité + intégrité de la chaîne) :
     il ne contient de toute façon que des hash, pas d'identité en clair.

L'effacement opère sur les colonnes hashées (`user_id_hash`, `client_id_hash`,
`owner_hash`) : on reçoit l'identifiant en clair, on le hashe via
`admin_state.hash_id`, et on supprime les lignes correspondantes — cohérent avec
le fait qu'aucune donnée n'est stockée en clair.

Pur SQL + système de fichiers ; aucune dépendance externe.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .admin_state import _connect, _lock, hash_id
from . import docgen
from . import objstore


def _retention_days(default: int = 365) -> int:
    raw = (os.environ.get("ONIX_RETENTION_DAYS", str(default)) or "").strip()
    try:
        d = int(raw)
        return d if d > 0 else default
    except ValueError:
        return default


def _cutoff_iso(days: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def purge_by_age(days: Optional[int] = None, *, purge_files: bool = True) -> Dict[str, Any]:
    """Supprime les données plus vieilles que `days` (défaut `ONIX_RETENTION_DAYS`).

    - `usage_events` : par `timestamp_utc` ;
    - `tasks` terminées (done/cancelled) : par `created_utc` (on conserve les
      tâches encore ouvertes quel que soit leur âge) ;
    - fichiers `.docx` (jobs) : par mtime du répertoire de job.
    NB : le journal d'audit administrateur n'est PAS purgé par âge (traçabilité).
    """
    days = days if days is not None else _retention_days()
    cutoff = _cutoff_iso(days)
    deleted_usage = 0
    deleted_tasks = 0
    deleted_jobs = 0

    with _lock, _connect() as conn:
        if _table_exists(conn, "usage_events"):
            cur = conn.execute(
                "DELETE FROM usage_events WHERE timestamp_utc < ?", (cutoff,)
            )
            deleted_usage = cur.rowcount or 0
        if _table_exists(conn, "tasks"):
            cur = conn.execute(
                "DELETE FROM tasks WHERE created_utc < ? AND status IN ('done','cancelled')",
                (cutoff,),
            )
            deleted_tasks = cur.rowcount or 0
        conn.commit()

    deleted_s3_objects = 0
    if purge_files:
        deleted_jobs = _purge_old_jobs(days)
        # En mode S3, les .docx vivent aussi (et surtout, en HA) dans le bucket :
        # purger par âge les objets correspondants (fail-safe si store local).
        if objstore.is_s3():
            cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
            deleted_s3_objects = objstore.delete_jobs_older_than(cutoff_ts)

    return {
        "retention_days": days,
        "cutoff_utc": cutoff,
        "deleted_usage_events": deleted_usage,
        "deleted_tasks": deleted_tasks,
        "deleted_job_dirs": deleted_jobs,
        "deleted_s3_objects": deleted_s3_objects,
    }


def _purge_old_jobs(days: int) -> int:
    """Supprime les répertoires de jobs `.docx` plus vieux que `days` (mtime).
    Confiné au répertoire des jobs ; ne suit aucun lien hors périmètre."""
    base = Path(docgen.jobs_dir())
    if not base.is_dir():
        return 0
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    removed = 0
    base_resolved = base.resolve()
    for child in base.iterdir():
        try:
            resolved = child.resolve()
            # Confinement : ne supprime que sous le répertoire des jobs.
            if base_resolved not in resolved.parents:
                continue
            if not child.is_dir():
                continue
            if child.stat().st_mtime < cutoff_ts:
                _rmtree_safe(child)
                removed += 1
        except OSError:
            continue
    return removed


def _rmtree_safe(path: Path) -> None:
    """Suppression récursive bornée d'un répertoire de job (.docx + dossier)."""
    for entry in path.iterdir():
        if entry.is_dir():
            _rmtree_safe(entry)
        else:
            try:
                entry.unlink()
            except OSError:
                pass
    try:
        path.rmdir()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Effacement ciblé par sujet (art. 17)
# ---------------------------------------------------------------------------
def _safe_job_token(token: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "", str(token))[:64]


def erase_subject(
    subject_id: Optional[str] = None,
    *,
    subject_hash: Optional[str] = None,
    erase_files: bool = True,
) -> Dict[str, Any]:
    """Efface toutes les traces d'un sujet (droit à l'effacement, art. 17).

    Le sujet est désigné par son identifiant EN CLAIR (`subject_id`, hashé ici)
    OU directement par son `subject_hash`. Supprime les lignes `usage_events` et
    `tasks` rattachées (user/client/owner) et — si `erase_files` — les fichiers
    `.docx` dont le nom contient le sujet (best-effort, voir note).

    Retourne le détail des suppressions. N'altère JAMAIS le journal d'audit
    chaîné (intégrité + il ne porte que des hash)."""
    h = (subject_hash or "").strip() or hash_id(subject_id)
    if not h:
        raise ValueError("Fournir subject_id ou subject_hash.")

    deleted_usage = 0
    deleted_tasks = 0
    with _lock, _connect() as conn:
        if _table_exists(conn, "usage_events"):
            cur = conn.execute(
                "DELETE FROM usage_events WHERE user_id_hash=? OR client_id_hash=?",
                (h, h),
            )
            deleted_usage = cur.rowcount or 0
        if _table_exists(conn, "tasks"):
            cur = conn.execute(
                "DELETE FROM tasks WHERE owner_hash=? OR client_id_hash=?", (h, h)
            )
            deleted_tasks = cur.rowcount or 0
        conn.commit()

    erased_files = 0
    erased_s3_objects = 0
    if erase_files and subject_id:
        erased_files = _erase_subject_files(subject_id)
        # En mode S3, les fiches .docx du sujet vivent dans le bucket partagé :
        # les effacer aussi (art. 17 exhaustif en HA). Fail-safe si store local.
        if objstore.is_s3():
            needle = docgen.safe_filename(subject_id).lower()
            if needle and needle != "client_inconnu":
                erased_s3_objects = objstore.delete_subject_docx(needle)

    return {
        "subject_hash": h,
        "deleted_usage_events": deleted_usage,
        "deleted_tasks": deleted_tasks,
        "erased_files": erased_files,
        "erased_s3_objects": erased_s3_objects,
    }


def _erase_subject_files(subject_id: str) -> int:
    """Supprime les fichiers `.docx` générés rattachés à un sujet.

    Les fiches sont nommées `Fiche_RDV_<nom_sanitisé>.docx` (cf. docgen). On
    efface tout fichier dont le nom contient le nom sanitisé du sujet. C'est un
    rapprochement best-effort (le nom de fichier n'est pas l'identité), documenté
    comme tel : pour un effacement exhaustif, coupler à un index sujet->jobs."""
    base = Path(docgen.jobs_dir())
    if not base.is_dir():
        return 0
    needle = docgen.safe_filename(subject_id).lower()
    if not needle or needle == "client_inconnu":
        return 0
    removed = 0
    base_resolved = base.resolve()
    for job_dir in base.iterdir():
        try:
            if not job_dir.is_dir():
                continue
            for f in job_dir.iterdir():
                resolved = f.resolve()
                if base_resolved not in resolved.parents:
                    continue
                if f.is_file() and f.name.lower().endswith(".docx") and needle in f.name.lower():
                    try:
                        f.unlink()
                        removed += 1
                    except OSError:
                        pass
            # Nettoie un répertoire de job devenu vide.
            try:
                if not any(job_dir.iterdir()):
                    job_dir.rmdir()
            except OSError:
                pass
        except OSError:
            continue
    return removed
