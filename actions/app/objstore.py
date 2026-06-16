"""objstore — Stockage objet S3/MinIO opt-in pour les fiches .docx (onix-actions).

WS-CW1 — Pour le multi-réplica, les fichiers `.docx` générés ne peuvent plus
vivre sur le disque LOCAL d'une réplique : `GET /download/{job_id}` pourrait
tomber sur une autre réplique qui n'a pas le fichier. On déporte donc le
stockage objet vers **S3/MinIO** (déjà disponible dans la stack), partagé par
toutes les répliques.

  * **Disque local par défaut** (`ONIX_OBJECT_STORE` absent ou `=local`) —
    comportement historique mono-poste **strictement inchangé** (docgen écrit
    sous `ONIX_JOBS_DIR`, download lit depuis là).
  * **S3/MinIO opt-in** (`ONIX_OBJECT_STORE=s3`) — docgen écrit l'objet sous la
    clé `jobs/{job_id}/{filename}` dans le bucket `ONIX_S3_BUCKET`, et download le
    relit depuis S3 → fonctionne en multi-réplica.

Variables (alignées docker-compose / chart) :
  * `S3_ENDPOINT_URL`         — endpoint S3 (MinIO : http://minio:9000) ;
  * `S3_AWS_ACCESS_KEY_ID`    — clé d'accès ;
  * `S3_AWS_SECRET_ACCESS_KEY`— clé secrète ;
  * `ONIX_S3_BUCKET`          — bucket (défaut `onyx-file-store-bucket`) ;
  * `S3_REGION`               — région (défaut `us-east-1`, ignorée par MinIO).

boto3 (client S3 standard, compatible MinIO) n'est importé QUE si le mode S3 est
actif (dépendance optionnelle ; le mode local n'en a pas besoin).
"""
from __future__ import annotations

import os
import threading
from typing import Optional, Tuple

# Préfixe de clé sous lequel sont rangés les fichiers de jobs (miroir de la
# structure locale `<jobs_dir>/<job_id>/<filename>`).
_KEY_PREFIX = "jobs"

_DEFAULT_BUCKET = "onyx-file-store-bucket"

_client_lock = threading.Lock()
_cached: dict = {}


def backend() -> str:
    """Backend de stockage objet actif : 's3' si `ONIX_OBJECT_STORE=s3`, sinon
    'local'. Lu à chaque appel (compat rechargement de modules en test)."""
    raw = (os.environ.get("ONIX_OBJECT_STORE") or "local").strip().lower()
    return "s3" if raw in ("s3", "minio", "object") else "local"


def is_s3() -> bool:
    return backend() == "s3"


def bucket_name() -> str:
    return (os.environ.get("ONIX_S3_BUCKET") or _DEFAULT_BUCKET).strip()


def object_key(job_id: str, filename: str) -> str:
    """Clé S3 d'un fichier de job (déterministe, miroir du chemin local)."""
    return f"{_KEY_PREFIX}/{job_id}/{filename}"


def _client():
    """Client S3 boto3 (mémoïsé par configuration). Compatible MinIO via
    `endpoint_url` + `path`-style. Import paresseux de boto3."""
    endpoint = (os.environ.get("S3_ENDPOINT_URL") or "").strip()
    key = os.environ.get("S3_AWS_ACCESS_KEY_ID") or ""
    secret = os.environ.get("S3_AWS_SECRET_ACCESS_KEY") or ""
    region = (os.environ.get("S3_REGION") or "us-east-1").strip()
    sig = (endpoint, key[:6], region)
    with _client_lock:
        if _cached.get("sig") == sig and _cached.get("client") is not None:
            return _cached["client"]
        import boto3  # import paresseux : requis seulement en mode S3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            aws_access_key_id=key or None,
            aws_secret_access_key=secret or None,
            region_name=region,
            # MinIO exige le path-style addressing (pas de virtual-host buckets).
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        _cached["sig"] = sig
        _cached["client"] = client
        return client


def ensure_bucket() -> None:
    """Crée le bucket s'il n'existe pas (idempotent). MinIO peut aussi le
    pré-créer via `MINIO_DEFAULT_BUCKETS`, mais on est défensif."""
    client = _client()
    bucket = bucket_name()
    try:
        client.head_bucket(Bucket=bucket)
        return
    except Exception:
        pass
    try:
        client.create_bucket(Bucket=bucket)
    except Exception:
        # Course bénigne (déjà créé par une autre réplique) : ignorer.
        pass


def put_file(job_id: str, filename: str, local_path: str) -> str:
    """Téléverse un fichier local vers S3 sous la clé du job. Retourne la clé."""
    client = _client()
    key = object_key(job_id, filename)
    ensure_bucket()
    with open(local_path, "rb") as fh:
        client.put_object(
            Bucket=bucket_name(),
            Key=key,
            Body=fh.read(),
            ContentType=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
        )
    return key


def get_bytes(job_id: str, filename: str) -> bytes:
    """Récupère le contenu d'un fichier de job depuis S3. Lève FileNotFoundError
    si l'objet n'existe pas (mappé en 404 par l'appelant)."""
    client = _client()
    key = object_key(job_id, filename)
    try:
        resp = client.get_object(Bucket=bucket_name(), Key=key)
        return resp["Body"].read()
    except Exception as e:
        # boto3 lève ClientError(404) ; on normalise en FileNotFoundError.
        if "NoSuchKey" in str(e) or "Not Found" in str(e) or "404" in str(e):
            raise FileNotFoundError(f"Objet introuvable : {key}") from e
        raise


def find_job_docx(job_id: str) -> Optional[str]:
    """Retourne le nom du premier `.docx` d'un job dans S3, ou None s'il n'y en a
    pas. (Le job est un préfixe `jobs/<job_id>/`.)"""
    client = _client()
    prefix = f"{_KEY_PREFIX}/{job_id}/"
    try:
        resp = client.list_objects_v2(Bucket=bucket_name(), Prefix=prefix)
    except Exception:
        return None
    for item in resp.get("Contents", []) or []:
        key = item.get("Key", "")
        name = key.rsplit("/", 1)[-1]
        if name.lower().endswith(".docx"):
            return name
    return None


def delete_job(job_id: str) -> int:
    """Supprime tous les objets d'un job (préfixe). Retourne le nombre supprimé.
    Utilisé par la rétention / l'effacement RGPD en mode S3."""
    client = _client()
    prefix = f"{_KEY_PREFIX}/{job_id}/"
    deleted = 0
    try:
        resp = client.list_objects_v2(Bucket=bucket_name(), Prefix=prefix)
        keys = [{"Key": it["Key"]} for it in (resp.get("Contents") or [])]
        if keys:
            client.delete_objects(Bucket=bucket_name(), Delete={"Objects": keys})
            deleted = len(keys)
    except Exception:
        pass
    return deleted


def reset_cache() -> None:
    """Réinitialise le client mémoïsé (utilitaire de test / changement de config)."""
    with _client_lock:
        _cached.clear()
