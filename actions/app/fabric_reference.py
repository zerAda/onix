"""fabric_reference — référence client depuis le **SI Fabric (OneLake)** pour la
**réconciliation contrat** (POC AC360). Read-only, **fail-closed**, stdlib-first.

`audit_engine.audit({document, reference})` compare un document OCRisé à une
référence. Ici la référence = la donnée **STRUCTURÉE du SI** lue dans Fabric
OneLake (nom, SIRET, plafond hospitalisation, date d'effet, numéro de contrat) —
au lieu d'un fichier local. C'est le maillon manquant pour reproduire le flux
AC360 : « document SharePoint → OCR → **référence Fabric** → comparaison → verdict ».

Lecteur **injectable** (tests 100 % offline). Toute absence / erreur ⇒ **None**
(→ verdict `CLIENT_NON_TROUVE`) : on n'invente JAMAIS une référence.
Sécurité : HTTPS-only, jeton via env (jamais en repo), aucune écriture.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, Optional

# Champs canoniques attendus par audit_engine (mêmes clés que `_FIELD_ALIASES`).
REFERENCE_FIELDS = (
    "nom_client",
    "siret",
    "plafond_hospitalisation",
    "cotisation_annuelle",
    "garantie",
    "date_effet",
    "numero_contrat",
)

# Lecteur : clé d'identité client (nom OU SIRET, normalisée) -> enregistrement brut du SI.
ReferenceReader = Callable[[str], Optional[Dict[str, Any]]]


def _nk(value: Any) -> str:
    """Normalise une clé/valeur d'identité pour comparaison (strip + lower)."""
    return str(value or "").strip().lower()


def map_reference(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Projette un enregistrement SI brut sur les champs canoniques (les clés du
    SI sont tolérées insensibles à la casse). Une clé absente reste `None`."""
    if not isinstance(raw, dict):
        return {}
    idx = {_nk(k): v for k, v in raw.items()}
    return {f: idx.get(f) for f in REFERENCE_FIELDS}


def fabric_reference_configured() -> bool:
    """True si une source SI Fabric est configurée (`ONIX_FABRIC_REFERENCE_URL`)."""
    return bool(os.environ.get("ONIX_FABRIC_REFERENCE_URL", "").strip())


# Résilience lecture OneLake (blips réseau). Bornés pour rester sûrs/prévisibles.
_BACKOFF_BASE = 0.25  # secondes ; backoff linéaire entre deux tentatives


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    """Entier d'env borné [lo, hi] ; revient au défaut si absent/illisible."""
    try:
        return max(lo, min(int(os.environ.get(name, str(default))), hi))
    except (TypeError, ValueError):
        return default


def _read_attempts() -> int:
    """Nombre de tentatives de lecture du SI (env ``ONIX_FABRIC_READ_ATTEMPTS``, défaut 2, borné [1,5])."""
    return _env_int("ONIX_FABRIC_READ_ATTEMPTS", 2, 1, 5)


def _read_timeout() -> int:
    """Timeout HTTP de la lecture OneLake (env ``ONIX_FABRIC_READ_TIMEOUT``, défaut 15 s, borné [3,60])."""
    return _env_int("ONIX_FABRIC_READ_TIMEOUT", 15, 3, 60)


def _sleep(seconds: float) -> None:
    """Pause entre tentatives — isolée pour être neutralisable en test (monkeypatch)."""
    import time

    time.sleep(seconds)


def fetch_client_reference(
    client_key: Optional[str], *, reader: Optional[ReferenceReader] = None
) -> Optional[Dict[str, Any]]:
    """Référence du client `client_key` (nom OU SIRET) depuis le **SI Fabric**.

    Renvoie le dict de référence **canonique**, ou **None** si : clé vide, client
    absent du SI, lecture impossible, ou aucune source configurée. **Fail-closed** :
    aucune référence inventée → l'audit conclura `CLIENT_NON_TROUVE`."""
    key = _nk(client_key)
    if not key:
        return None
    read = reader or _default_onelake_reader
    attempts = _read_attempts()
    raw: Any = None
    for i in range(attempts):
        try:
            raw = read(key)
            break  # succès (raw peut être None = client absent du SI, PAS une erreur)
        except Exception:
            # Blip réseau / OneLake momentané : on retente (backoff). Jamais de fuite
            # d'erreur/jeton ; toutes les tentatives KO ⇒ fail-closed (pas de référence).
            if i + 1 >= attempts:
                return None
            _sleep(_BACKOFF_BASE * (i + 1))
    if not isinstance(raw, dict) or not raw:
        return None
    ref = map_reference(raw)
    # Une référence sans nom de client n'est pas exploitable par l'audit.
    return ref if ref.get("nom_client") else None


def _select_record(data: Any, key: str) -> Optional[Dict[str, Any]]:
    """Sélectionne l'enregistrement du client `key` dans la donnée SI : dict indexé
    par clé, dict enveloppé sous `clients`, ou liste d'enregistrements (filtrée par
    SIRET/nom)."""
    if isinstance(data, dict):
        rec = data.get(key)
        if isinstance(rec, dict):
            return rec
        wrapped = data.get("clients")
        data = wrapped if isinstance(wrapped, list) else list(data.values())
    if isinstance(data, list):
        for r in data:
            if isinstance(r, dict) and key in (_nk(r.get("siret")), _nk(r.get("nom_client"))):
                return r
    return None


def reconcile_batch(items: Any, *, reference_reader: Optional[ReferenceReader] = None) -> Dict[str, Any]:
    """Réconcilie un LOT de contrats (PORTEFEUILLE) contre le SI Fabric.

    `items` : **liste (ou tuple)** de dicts ``{"document": {champs canoniques}, "client_key": str}``.
    Pour CHAQUE contrat : référence SI (`fetch_client_reference`, fail-closed → client
    absent ⇒ verdict `CLIENT_NON_TROUVE`) → `audit` → fiche de revue. Retourne un
    rapport de portefeuille : la liste des **fiches** + une **synthèse** (compteurs par
    verdict + nb à revoir + nb d'items invalides). Utile au back-office : réconcilier
    tout un portefeuille en un appel et obtenir un tableau de bord d'arbitrage.

    Fail-closed et SANS exception : un item mal formé (sans ``document`` exploitable)
    est compté en `invalides` et marqué à revoir, jamais propagé. Lecture seule : ne
    modifie/écrit RIEN (parité AC360, prompt agent « lecture seule »)."""
    # Import local : audit_engine ne dépend pas de fabric_reference (relation à sens
    # unique) — l'import ici évite tout couplage de module au chargement.
    from .audit_engine import audit, build_review_fiche

    fiches: list = []
    synthese: Dict[str, int] = {
        "total": 0, "a_revoir": 0, "invalides": 0,
        "CONFORME": 0, "ECART": 0, "INCERTAIN": 0, "CLIENT_NON_TROUVE": 0,
    }
    # Fail-closed sur entrée non conforme : `items` DOIT être une liste/tuple ; toute
    # autre valeur (nombre, chaîne, dict, None) -> lot vide, JAMAIS d'exception
    # (respecte la promesse « SANS exception » même sur un body JSON malformé).
    safe_items = items if isinstance(items, (list, tuple)) else []
    for item in safe_items:
        synthese["total"] += 1
        doc = item.get("document") if isinstance(item, dict) else None
        client_key = item.get("client_key") if isinstance(item, dict) else None
        if not isinstance(doc, dict) or not doc:
            synthese["invalides"] += 1
            synthese["a_revoir"] += 1
            fiches.append({
                "client": client_key, "verdict": "INVALIDE", "a_revoir": True,
                "nb_ecarts": 0, "ecarts": [],
                "recommandation": "Item sans document exploitable — vérifier la saisie.",
            })
            continue
        reference = fetch_client_reference(client_key, reader=reference_reader) or {}
        fiche = build_review_fiche(
            audit({"document": doc, "reference": reference}), client_key=client_key
        )
        fiches.append(fiche)
        verdict = fiche.get("verdict", "INCONNU")
        synthese[verdict] = synthese.get(verdict, 0) + 1
        if fiche.get("a_revoir"):
            synthese["a_revoir"] += 1
    return {"fiches": fiches, "synthese": synthese}


# Cache mémoire du jeton OneLake (évite un aller-retour AAD par requête).
_TOKEN_CACHE: Dict[str, Any] = {"value": None, "expires": 0.0}


def _storage_token() -> Optional[str]:
    """Jeton d'accès OneLake (audience ``storage.azure.com``). Deux modes :

      * **Service principal** (recommandé, auto-rafraîchi) si ``ONIX_FABRIC_SP_CLIENT_ID``
        + ``ONIX_FABRIC_SP_CLIENT_SECRET`` + ``ONIX_FABRIC_SP_TENANT`` sont fournis :
        ``client_credentials`` → jeton mis en cache jusqu'à ~1 min avant expiration ;
      * **jeton statique** ``ONIX_FABRIC_TOKEN`` sinon (déballage manuel, expire vite).

    Tous les secrets viennent de l'**environnement** (jamais du repo). Retourne
    ``None`` si rien n'est configuré ou si le mint AAD échoue (fail-closed)."""
    cid = os.environ.get("ONIX_FABRIC_SP_CLIENT_ID", "").strip()
    sec = os.environ.get("ONIX_FABRIC_SP_CLIENT_SECRET", "").strip()
    ten = os.environ.get("ONIX_FABRIC_SP_TENANT", "").strip()
    if not (cid and sec and ten):
        return os.environ.get("ONIX_FABRIC_TOKEN", "").strip() or None

    import time

    now = time.time()
    if _TOKEN_CACHE["value"] and _TOKEN_CACHE["expires"] > now + 60:
        return _TOKEN_CACHE["value"]
    import urllib.parse
    import urllib.request

    data = urllib.parse.urlencode({
        "client_id": cid, "client_secret": sec,
        "scope": "https://storage.azure.com/.default",
        "grant_type": "client_credentials",
    }).encode("utf-8")
    url = "https://login.microsoftonline.com/" + ten + "/oauth2/v2.0/token"
    try:
        req = urllib.request.Request(url, data=data)  # AAD, https
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 - endpoint AAD https constant
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    tok = payload.get("access_token")
    if tok:
        _TOKEN_CACHE["value"] = tok
        _TOKEN_CACHE["expires"] = now + float(payload.get("expires_in", 3600))
    return tok


def _default_onelake_reader(key: str) -> Optional[Dict[str, Any]]:
    """Lecteur par défaut : JSON de référence du SI Fabric (lakehouse, OneLake),
    indexé par identité client. Configuration **par env** (jamais en repo) :

      * ``ONIX_FABRIC_REFERENCE_URL`` : URL **HTTPS** du JSON (OneLake DFS).
      * auth via :func:`_storage_token` (service principal auto-rafraîchi, ou jeton statique).

    Le JSON peut être ``{clé: enregistrement}`` OU une liste d'enregistrements.
    Absence de config / URL non-HTTPS ⇒ ``None`` (fail-closed, anti-SSRF)."""
    url = os.environ.get("ONIX_FABRIC_REFERENCE_URL", "").strip()
    if not url or not url.lower().startswith("https://"):
        return None
    import urllib.request

    req = urllib.request.Request(url)  # https only (vérifié ci-dessus)
    token = _storage_token()
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=_read_timeout()) as resp:  # nosec B310 - URL env, https-only
        data = json.loads(resp.read().decode("utf-8"))
    return _select_record(data, key)
