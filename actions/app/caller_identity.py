"""caller_identity — Identité d'appelant VÉRIFIABLE (onix-actions).

WS2 fait passer le service d'une **clé API unique** (qui prouve seulement « un
appelant autorisé ») à une **identité d'appelant vérifiée** : on sait QUI appelle
(pour journaliser, quota, blocage par utilisateur), pas seulement QUE l'appel est
autorisé.

Deux mécanismes, au choix (le plus fort présent gagne), 100 % local/souverain :

  1. **HMAC par appel** (défaut souverain, aucune dépendance) — en-têtes :
       X-Onix-Caller : identifiant d'appelant en clair (UPN, service…)
       X-Onix-Timestamp : epoch (s) — anti-rejeu (fenêtre `ONIX_HMAC_MAX_SKEW`)
       X-Onix-Signature : hex( HMAC-SHA256(secret, f"{caller}\n{ts}\n{method}\n{path}") )
     Le secret est `ONIX_ACTIONS_CALLER_HMAC_SECRET`. La signature lie l'identité,
     l'horodatage et la requête : ni rejouable, ni transférable à une autre route.

  2. **JWT OIDC** (en-tête `Authorization: Bearer <jwt>`) — vérifie signature
     (HS256 natif, ou RS256/ES256 via PyJWT+JWKS si disponible), `exp`, `iss`,
     `aud`. Le `sub`/`preferred_username`/`upn`/`email` devient l'identité.

Dans les deux cas, on retourne un `CallerContext` portant l'identité EN CLAIR
(jamais journalisée telle quelle : on la hashe via `admin_state.hash_id`) et le
moyen d'authentification utilisé.

Si AUCUN mécanisme d'identité n'est configuré, on **dégrade proprement** vers
l'identité de service (clé API) : l'appel reste authentifié (clé API valide
exigée en amont), mais l'identité fine est `service`. C'est un choix explicite
de compatibilité — voir `docs/SECURITY_RGPD_ACTIONS.md` §Identité.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

# PyJWT est optionnel : présent -> RS256/ES256 + JWKS ; absent -> HS256 natif.
try:  # pragma: no cover - dépend de l'environnement
    import jwt as _pyjwt  # type: ignore
    from jwt import PyJWKClient  # type: ignore

    _HAS_PYJWT = True
except Exception:  # pragma: no cover
    _pyjwt = None
    _HAS_PYJWT = False


# ---------------------------------------------------------------------------
# Contexte d'appelant
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CallerContext:
    """Identité résolue d'un appelant. `caller_id` est EN CLAIR (à hasher avant
    tout log/persistance). `method` indique le moyen d'authentification."""

    caller_id: str
    method: str  # "hmac" | "jwt" | "service"
    is_service: bool = False


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _hmac_secret() -> Optional[str]:
    return _env("ONIX_ACTIONS_CALLER_HMAC_SECRET") or None


def _max_skew() -> int:
    try:
        return int(_env("ONIX_HMAC_MAX_SKEW", "300") or "300")
    except ValueError:
        return 300


def identity_required() -> bool:
    """L'identité d'appelant vérifiée est-elle EXIGÉE (fail-closed) ?

    `ONIX_ACTIONS_REQUIRE_CALLER_IDENTITY=true` impose un en-tête signé/JWT
    valide sur les endpoints métier (sinon 401). Par défaut `false` pour ne pas
    casser un déploiement existant à clé API seule — mais documenté comme à
    activer en production."""
    raw = _env("ONIX_ACTIONS_REQUIRE_CALLER_IDENTITY", "false").lower()
    return raw in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# HMAC par appel
# ---------------------------------------------------------------------------
def compute_hmac(secret: str, caller: str, ts: str, method: str, path: str) -> str:
    """Signature canonique d'une requête. Exposée pour les tests / clients."""
    msg = f"{caller}\n{ts}\n{method.upper()}\n{path}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _verify_hmac(
    *,
    caller: Optional[str],
    ts: Optional[str],
    signature: Optional[str],
    http_method: str,
    path: str,
) -> Optional[CallerContext]:
    secret = _hmac_secret()
    if not secret or not (caller and ts and signature):
        return None
    # Anti-rejeu : l'horodatage doit être récent.
    try:
        delta = abs(time.time() - float(ts))
    except (TypeError, ValueError):
        return None
    if delta > _max_skew():
        return None
    expected = compute_hmac(secret, caller, ts, http_method, path)
    # Comparaison en temps constant.
    if not hmac.compare_digest(expected, signature.strip()):
        return None
    return CallerContext(caller_id=caller.strip(), method="hmac")


# ---------------------------------------------------------------------------
# JWT (OIDC) — HS256 natif + RS256/ES256 via PyJWT si dispo
# ---------------------------------------------------------------------------
def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def _jwt_claims_identity(claims: Dict[str, Any]) -> Optional[str]:
    for key in ("preferred_username", "upn", "email", "sub"):
        val = claims.get(key)
        if val:
            return str(val)
    return None


def _verify_jwt_hs256(token: str, secret: str) -> Optional[Dict[str, Any]]:
    """Vérification HS256 autonome (sans PyJWT) : signature + `exp` OBLIGATOIRE.

    Fail-closed : un token sans `exp` (jamais expirable, donc non révocable) est
    REFUSÉ — parité avec la voie PyJWT (`options={'require': ['exp']}`)."""
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        return None
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    try:
        header = json.loads(_b64url_decode(header_b64))
        if header.get("alg") != "HS256":
            return None
        expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected_sig, _b64url_decode(sig_b64)):
            return None
        claims = json.loads(_b64url_decode(payload_b64))
    except Exception:
        return None
    # `exp` OBLIGATOIRE (fail-closed) + non périmé.
    exp = claims.get("exp")
    if exp is None:
        return None
    try:
        if float(exp) < time.time():
            return None
    except (TypeError, ValueError):
        return None
    return claims


def _verify_jwt(token: str) -> Optional[CallerContext]:
    issuer = _env("ONIX_OIDC_ISSUER") or None
    audience = _env("ONIX_OIDC_AUDIENCE") or None
    jwks_url = _env("ONIX_OIDC_JWKS_URL") or None
    hs_secret = _env("ONIX_OIDC_HS256_SECRET") or None

    # FAIL-CLOSED : on n'accepte un JWT que si `iss` ET `aud` attendus sont
    # configurés. Sinon, un token légitimement signé par l'IdP mais destiné à un
    # AUTRE relying party (aud différent) ou émis par un autre émetteur serait
    # accepté (PyJWT et la voie native ignorent un contrôle dont l'attendu est
    # None). On refuse donc d'authentifier par JWT tant que la config est
    # incomplète (on retombera sur HMAC / clé de service en amont).
    if not (issuer and audience):
        return None

    claims: Optional[Dict[str, Any]] = None

    # RS256/ES256 via PyJWT + JWKS (OIDC standard, ex: Entra ID).
    if _HAS_PYJWT and jwks_url:  # pragma: no cover - dépend de PyJWT + réseau
        try:
            signing_key = PyJWKClient(jwks_url).get_signing_key_from_jwt(token).key
            claims = _pyjwt.decode(
                token,
                signing_key,
                algorithms=["RS256", "ES256"],
                audience=audience,
                issuer=issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except Exception:
            return None
    elif hs_secret:
        claims = _verify_jwt_hs256(token, hs_secret)
        if claims is not None:
            # Contrôles iss/aud applicatifs (le chemin HS256 natif ne les fait pas).
            if claims.get("iss") != issuer:
                return None
            aud = claims.get("aud")
            aud_ok = audience == aud or (isinstance(aud, list) and audience in aud)
            if not aud_ok:
                return None
    else:
        return None

    if not claims:
        return None
    identity = _jwt_claims_identity(claims)
    if not identity:
        return None
    return CallerContext(caller_id=identity, method="jwt")


# ---------------------------------------------------------------------------
# Résolution générale
# ---------------------------------------------------------------------------
def resolve_caller(
    *,
    http_method: str,
    path: str,
    authorization: Optional[str],
    x_caller: Optional[str],
    x_timestamp: Optional[str],
    x_signature: Optional[str],
    body_caller_id: Optional[str] = None,
) -> CallerContext:
    """Résout l'identité d'appelant à partir des en-têtes. Le mécanisme le plus
    fort présent gagne : HMAC signé > JWT vérifié > (repli) clé de service.

    `body_caller_id` (champ `caller_id` du corps) n'est PAS une preuve
    d'identité : il sert UNIQUEMENT de repli d'étiquetage quand aucune identité
    vérifiable n'est configurée (compat). Il n'est jamais cru si un mécanisme
    vérifiable est actif et échoue (on lèvera en amont si identité exigée)."""
    # 1. HMAC par appel (le plus fort, lié à la requête).
    ctx = _verify_hmac(
        caller=x_caller,
        ts=x_timestamp,
        signature=x_signature,
        http_method=http_method,
        path=path,
    )
    if ctx:
        return ctx

    # 2. JWT OIDC (Bearer).
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        # Un JWT a 3 segments ; sinon c'est probablement la clé API en Bearer.
        if token.count(".") == 2:
            ctx = _verify_jwt(token)
            if ctx:
                return ctx

    # 3. Repli : identité de service (clé API validée en amont). Étiquette
    #    optionnelle via body.caller_id pour conserver la granularité d'usage.
    label = (body_caller_id or "").strip() or "service"
    return CallerContext(caller_id=label, method="service", is_service=True)
