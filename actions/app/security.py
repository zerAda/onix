"""security — Authn (clé de service + identité d'appelant) + authz + quota.

WS2 durcit l'authentification du microservice :

  * **Clé de SERVICE** (`ONIX_ACTIONS_API_KEY`, en-tête `X-API-Key` ou
    `Authorization: Bearer`) — comparée en temps constant. Prouve que l'appel
    vient d'un client autorisé (Onyx). Sans elle, le service refuse tout (503).
  * **Identité d'appelant VÉRIFIÉE** (cf. `caller_identity`) : HMAC par appel ou
    JWT OIDC. On ne se contente plus de « clé partagée » : on sait QUI appelle
    (pour quota, blocage par utilisateur, journalisation). Exigible (fail-closed)
    via `ONIX_ACTIONS_REQUIRE_CALLER_IDENTITY=true`.
  * **Clé ADMIN distincte** (`ONIX_ACTIONS_ADMIN_KEY`, en-tête `X-Admin-Key`)
    pour `/admin/*`. WS2 la rend **obligatoire par défaut** (fail-closed) : sans
    clé admin configurée, `/admin/*` est refusé (403), sauf opt-out explicite
    `ONIX_ACTIONS_ADMIN_KEY_OPTIONAL=true` (compat).
  * **Rate-limiting par appelant** (slowapi) : quota par identité (parité AC360),
    avec repli en mémoire si slowapi est absent.

Garde-fous d'entrée (taille de fichier, extensions, anti path-traversal) conservés.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
import time
from typing import Dict, Optional, Tuple

from fastapi import Depends, Header, HTTPException, Request, status

from .caller_identity import CallerContext, identity_required, resolve_caller

# slowapi est la dépendance de RÉFÉRENCE pour le rate-limiting (format de quota,
# store partagé possible). L'enforcement effectif PAR APPELANT se fait toutefois
# dans la dépendance d'auth (après résolution d'identité — un middleware ne
# verrait que l'IP) via une fenêtre glissante en mémoire. On signale juste sa
# disponibilité (diagnostic / évolution multi-instance).
try:  # pragma: no cover - dépend de l'environnement
    import slowapi  # noqa: F401

    _HAS_SLOWAPI = True
except Exception:  # pragma: no cover
    _HAS_SLOWAPI = False

# Limites d'entrée (configurables).
MAX_UPLOAD_BYTES = int(os.environ.get("ONIX_MAX_UPLOAD_BYTES", str(15 * 1024 * 1024)))
ALLOWED_UPLOAD_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp")


# ---------------------------------------------------------------------------
# Clés (service / admin)
# ---------------------------------------------------------------------------
def _expected_key() -> str:
    return os.environ.get("ONIX_ACTIONS_API_KEY", "").strip()


def _expected_admin_key() -> Optional[str]:
    k = os.environ.get("ONIX_ACTIONS_ADMIN_KEY", "").strip()
    return k or None


def _admin_key_optional() -> bool:
    """Par défaut FALSE (fail-closed) : la clé admin distincte est OBLIGATOIRE.
    `ONIX_ACTIONS_ADMIN_KEY_OPTIONAL=true` rétablit l'ancien comportement."""
    raw = os.environ.get("ONIX_ACTIONS_ADMIN_KEY_OPTIONAL", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _constant_eq(a: str, b: str) -> bool:
    # Comparaison en temps constant (anti timing) sur des digests de taille fixe.
    da = hashlib.sha256((a or "").encode()).digest()
    db = hashlib.sha256((b or "").encode()).digest()
    return hmac.compare_digest(da, db)


def _extract_key(x_api_key: Optional[str], authorization: Optional[str]) -> str:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        # Un JWT (3 segments) n'est PAS la clé de service : ne pas le confondre.
        if token.count(".") != 2:
            return token
    return ""


def _check_service_key(x_api_key: Optional[str], authorization: Optional[str]) -> None:
    """Valide la clé de service. Lève 503 si non configurée, 401 si invalide."""
    expected = _expected_key()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service non configuré (ONIX_ACTIONS_API_KEY absente).",
        )
    provided = _extract_key(x_api_key, authorization)
    if not provided or not _constant_eq(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé de service invalide ou absente.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


# ---------------------------------------------------------------------------
# Dépendance : authn de service + résolution d'identité d'appelant
# ---------------------------------------------------------------------------
def _resolve_identity(
    request: Request,
    x_api_key: Optional[str],
    authorization: Optional[str],
    x_caller: Optional[str],
    x_timestamp: Optional[str],
    x_signature: Optional[str],
) -> CallerContext:
    """Valide la clé de service et résout l'identité d'appelant (sans quota)."""
    _check_service_key(x_api_key, authorization)
    ctx = resolve_caller(
        http_method=request.method,
        path=request.url.path,
        authorization=authorization,
        x_caller=x_caller,
        x_timestamp=x_timestamp,
        x_signature=x_signature,
    )
    # Fail-closed si l'identité vérifiée est exigée mais absente (repli service).
    if identity_required() and ctx.is_service:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identité d'appelant vérifiée requise (HMAC signé ou JWT OIDC).",
            headers={"WWW-Authenticate": "Signature"},
        )
    request.state.caller = ctx
    return ctx


def require_caller(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
    x_caller: Optional[str] = Header(default=None, alias="X-Onix-Caller"),
    x_timestamp: Optional[str] = Header(default=None, alias="X-Onix-Timestamp"),
    x_signature: Optional[str] = Header(default=None, alias="X-Onix-Signature"),
) -> CallerContext:
    """Dépendance FastAPI principale (endpoints métier) : clé de service valide +
    identité résolue + **quota par appelant**."""
    ctx = _resolve_identity(request, x_api_key, authorization, x_caller, x_timestamp, x_signature)
    # Quota PAR APPELANT (parité AC360). Clé = identité vérifiée si présente ;
    # sinon (clé de service seule) on retombe sur l'IP source pour conserver une
    # granularité par client (sans cela, tous les appels « service » partageraient
    # un unique seau et un client bruyant affamerait les autres).
    enforce_rate_limit(_rate_key(ctx, request))
    return ctx


def _rate_key(ctx: CallerContext, request: Request) -> str:
    if not ctx.is_service:
        return f"id:{ctx.caller_id}"
    # Identité de service : différencier par source réseau.
    host = request.client.host if request.client else "unknown"
    return f"svc:{host}"


# Rétrocompat : ancien nom de dépendance (retourne juste un identifiant).
def require_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
    x_caller: Optional[str] = Header(default=None, alias="X-Onix-Caller"),
    x_timestamp: Optional[str] = Header(default=None, alias="X-Onix-Timestamp"),
    x_signature: Optional[str] = Header(default=None, alias="X-Onix-Signature"),
) -> str:
    ctx = require_caller(request, x_api_key, authorization, x_caller, x_timestamp, x_signature)
    return ctx.caller_id


def require_admin(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
    x_caller: Optional[str] = Header(default=None, alias="X-Onix-Caller"),
    x_timestamp: Optional[str] = Header(default=None, alias="X-Onix-Timestamp"),
    x_signature: Optional[str] = Header(default=None, alias="X-Onix-Signature"),
) -> CallerContext:
    """Dépendance pour `/admin/*` : clé de service + clé admin distincte
    OBLIGATOIRE (fail-closed). Sans clé admin configurée -> 403, sauf opt-out
    explicite. La clé admin présente doit matcher l'en-tête `X-Admin-Key`.

    NB : l'administration N'EST PAS soumise au quota par appelant — une action de
    sécurité (kill-switch, déblocage) ne doit jamais être bloquée en 429 par un
    pic d'abus sur les endpoints métier. L'accès admin reste protégé par la
    double clé (service + admin)."""
    ctx = _resolve_identity(request, x_api_key, authorization, x_caller, x_timestamp, x_signature)
    admin_expected = _expected_admin_key()
    if admin_expected is None:
        if _admin_key_optional():
            return ctx  # opt-out explicite (compat) : clé de service suffit
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administration verrouillée : ONIX_ACTIONS_ADMIN_KEY non "
            "configurée (fail-closed). Définissez une clé admin distincte.",
        )
    if not x_admin_key or not _constant_eq(x_admin_key.strip(), admin_expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Droit administrateur requis (X-Admin-Key).",
        )
    return ctx


# ---------------------------------------------------------------------------
# Rate-limiting par appelant
# ---------------------------------------------------------------------------
def _default_limit() -> str:
    """Quota par défaut (parité quota AC360). Format slowapi : « N/minute ».
    Variable non définie -> « 60/minute » ; définie à vide/0 -> désactivé."""
    raw = os.environ.get("ONIX_ACTIONS_RATE_LIMIT")
    if raw is None:
        return "60/minute"
    return raw.strip()


def _limit_disabled(spec: str) -> bool:
    """Le quota est-il désactivé ? Vide, mots-clés, OU compteur nul/négatif
    quel que soit l'unité (« 0 », « 0/minute », « 0/second », « 0/hour »…)."""
    s = (spec or "").strip().lower()
    if s in ("", "off", "none", "disabled"):
        return True
    count_s = s.partition("/")[0].strip()
    try:
        return int(count_s) <= 0
    except ValueError:
        return False


def _parse_limit(spec: str) -> Tuple[int, int]:
    """Parse « N/minute|second|hour » -> (max, fenêtre_secondes). Défaut 60/min."""
    try:
        count_s, _, period = spec.partition("/")
        count = int(count_s.strip())
        period = period.strip().lower()
        window = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600}.get(period, 60)
        return max(count, 1), window
    except Exception:
        return 60, 60


# Repli mémoire (fenêtre glissante simplifiée) si slowapi absent.
_mem_lock = threading.Lock()
_mem_hits: Dict[str, list] = {}


def enforce_rate_limit(caller_id: str) -> None:
    """Applique le quota PAR APPELANT (fenêtre glissante) une fois l'identité
    résolue. Lève 429 (avec `Retry-After`) au-delà de `ONIX_ACTIONS_RATE_LIMIT`.

    On applique le quota dans la dépendance (et non via un middleware slowapi)
    car l'identité d'appelant n'est connue qu'APRÈS résolution : un middleware ne
    verrait que l'IP. La fenêtre glissante en mémoire est déterministe, bornée et
    suffisante pour un microservice interne mono-instance ; pour du
    multi-instance, brancher un store partagé (Redis) via slowapi.

    Désactivable à 0 (`ONIX_ACTIONS_RATE_LIMIT=0[/unité]` ou vide)."""
    spec = _default_limit()
    if _limit_disabled(spec):
        return
    max_hits, window = _parse_limit(spec)
    now = time.time()
    with _mem_lock:
        bucket = _mem_hits.setdefault(caller_id, [])
        # Purge des hits hors fenêtre (fenêtre glissante).
        cutoff = now - window
        bucket[:] = [t for t in bucket if t >= cutoff]
        if len(bucket) >= max_hits:
            retry = int(window - (now - bucket[0])) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Quota dépassé ({spec}). Réessayez plus tard.",
                headers={"Retry-After": str(max(retry, 1))},
            )
        bucket.append(now)


def reset_rate_limits() -> None:
    """Réinitialise le repli mémoire (utilitaire de test)."""
    with _mem_lock:
        _mem_hits.clear()


# ---------------------------------------------------------------------------
# Validation d'upload (inchangée)
# ---------------------------------------------------------------------------
def validate_upload(filename: str, size: int) -> None:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f"Extension non autorisée : {ext or '(aucune)'}")
    if size <= 0:
        raise HTTPException(status_code=400, detail="Fichier vide.")
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Fichier trop volumineux (> {MAX_UPLOAD_BYTES // (1024*1024)} Mo).",
        )
