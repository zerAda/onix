"""celery_app — File asynchrone Celery pour onix-actions (WS-CW1).

Décharge les traitements LONGS (OCR de gros PDF scannés, audits par lot) hors du
chemin requête HTTP, et permet le **scale-out indépendant** d'un pool de workers
(HPA 2→12 côté chart). L'API reste réactive : `POST /audit/file/async` renvoie
`202 Accepted` + un `task_id`, et `GET /jobs/{task_id}` rend le statut/résultat.

Le chart WS4 lance le worker avec **exactement** :

    celery -A app.celery_app.celery worker --loglevel=info --concurrency=4

→ l'objet applicatif DOIT s'appeler `celery` et être importable comme
`app.celery_app.celery`. C'est le cas ici.

Activation (opt-in) :
  * `ONIX_QUEUE_ENABLED=true`  — expose les endpoints async (sinon `404`/`503`) ;
  * `ONIX_BROKER_URL=...`      — broker (AMQP RabbitMQ en prod : amqp://… ; Redis
                                 en dev : redis://…) ; fourni par le chart ;
  * `ONIX_RESULT_BACKEND=...`  — backend de résultats (ex. `db+postgresql://…` ou
                                 `redis://…`) ; à défaut on dérive du broker Redis,
                                 sinon `rpc://`.
  * `ONIX_QUEUE_EAGER=true`    — exécution SYNCHRONE en process (tests / CI sans
                                 worker ni broker) : `.delay()` exécute tout de
                                 suite et renvoie un résultat prêt.

Sans `ONIX_QUEUE_ENABLED`, ce module reste importable mais l'app FastAPI
n'expose pas les routes async — le mode mono-poste par défaut est inchangé.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from celery import Celery


def _broker_url() -> str:
    """URL du broker. Priorité à `ONIX_BROKER_URL` (fourni par le chart). À défaut,
    un Redis local pour le dev. En mode EAGER, le broker n'est pas réellement
    contacté (mémoire), mais Celery exige une valeur non vide."""
    url = (os.environ.get("ONIX_BROKER_URL") or "").strip()
    if url:
        return url
    if _eager():
        return "memory://"
    return os.environ.get("ONIX_BROKER_FALLBACK", "redis://127.0.0.1:6379/0")


def _result_backend() -> Optional[str]:
    """Backend de résultats. `ONIX_RESULT_BACKEND` prioritaire ; sinon, si le
    broker est Redis on réutilise Redis ; en EAGER on garde le cache mémoire ;
    sinon `rpc://` (résultats via le broker AMQP)."""
    backend = (os.environ.get("ONIX_RESULT_BACKEND") or "").strip()
    if backend:
        return backend
    if _eager():
        return "cache+memory://"
    broker = _broker_url()
    if broker.startswith("redis://") or broker.startswith("rediss://"):
        return broker
    return "rpc://"


def _eager() -> bool:
    """Mode synchrone en process (tests / validation sans worker)."""
    raw = (os.environ.get("ONIX_QUEUE_EAGER") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def queue_enabled() -> bool:
    """La file async est-elle activée (`ONIX_QUEUE_ENABLED`) ? Gate les endpoints."""
    raw = (os.environ.get("ONIX_QUEUE_ENABLED") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


# Application Celery. Nom d'objet `celery` IMPOSÉ par la commande du chart.
celery = Celery(
    "onix_actions",
    broker=_broker_url(),
    backend=_result_backend(),
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    # Idempotence / robustesse : retry de connexion au broker au démarrage.
    broker_connection_retry_on_startup=True,
    # Mode EAGER : exécution synchrone en process (pas de worker requis).
    task_always_eager=_eager(),
    task_eager_propagates=False,
    # En EAGER, STOCKER le résultat dans le backend pour que GET /jobs/{id} puisse
    # le relire (sinon Celery avertit et ne persiste pas le résultat en eager).
    task_store_eager_result=True,
    # Résultats conservés 1 jour (suffisant pour un polling GET /jobs/{id}).
    result_expires=86400,
)


@celery.task(name="audit_file_async", bind=True)
def audit_file_async(
    self,
    file_b64: str,
    filename: str,
    reference: Optional[Any] = None,
    opts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Tâche d'audit OCR ASYNCHRONE. Réutilise EXACTEMENT la logique synchrone :
    `ocr.extract(...)` → `extract_canonical_fields(...)` → `audit_engine.audit(...)`.

    Le fichier est transmis encodé base64 (le payload Celery est JSON) ; on le
    décode ici. Renvoie le verdict d'audit (sérialisable JSON), persisté par le
    backend de résultats Celery.

    Imports paresseux des modules applicatifs : un worker Celery n'importe pas
    forcément `app.main` (et on évite toute dépendance circulaire)."""
    import base64

    from . import ocr as ocr_mod
    from . import usage_tracker
    from .audit_engine import audit as run_audit
    from .audit_engine import extract_canonical_fields, normalize_name

    opts = opts or {}
    data = base64.b64decode(file_b64.encode("ascii"))

    usage_tracker.track("ocr_started", action_name="audit_file_async")
    ocr_out = ocr_mod.extract(data, filename or "document")
    mode = ocr_out["metadata"]["extraction_mode"]
    if mode == "unavailable":
        usage_tracker.track(
            "ocr_failed", status="error", action_name="audit_file_async",
            error_code="ocr_unavailable",
        )
        return {
            "status": "error",
            "reason": "ocr_unavailable",
            "detail": ocr_out["metadata"].get("reason"),
        }
    usage_tracker.track(
        "ocr_completed", action_name="audit_file_async",
        page_count=ocr_out["metadata"].get("pages", 0),
    )

    document = extract_canonical_fields(ocr_out)

    # Résolution de la référence : objet unique OU liste filtrée par client_key.
    ref_record = reference
    client_key = opts.get("client_key")
    if isinstance(reference, list):
        ref_record = None
        if client_key:
            target = normalize_name(client_key)
            for rec in reference:
                if isinstance(rec, dict) and normalize_name(rec.get("nom_client")) == target:
                    ref_record = rec
                    break
        if ref_record is None and reference:
            ref_record = reference[0]

    result = run_audit({"document": document, "reference": ref_record or {}})
    result["_ocr_mode"] = mode
    result["status"] = "completed"
    usage_tracker.track(
        "audit_documentaire_completed", action_name="audit_file_async",
        client_id=result.get("client_document"), document_count=1,
    )
    return result
