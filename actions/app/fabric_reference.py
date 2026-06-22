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
    try:
        raw = read(key)
    except Exception:
        # Jamais de fuite d'erreur/jeton : une lecture KO = pas de référence.
        return None
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


def _default_onelake_reader(key: str) -> Optional[Dict[str, Any]]:
    """Lecteur par défaut : JSON de référence du SI Fabric (lakehouse **gold**,
    OneLake), indexé par identité client. Configuration **par env** (jamais en repo) :

      * ``ONIX_FABRIC_REFERENCE_URL`` : URL **HTTPS** du JSON (OneLake DFS / gold).
      * ``ONIX_FABRIC_TOKEN``         : jeton Bearer (audience ``storage.azure.com``).

    Le JSON peut être ``{clé: enregistrement}`` OU une liste d'enregistrements.
    Absence de config / URL non-HTTPS ⇒ ``None`` (fail-closed, anti-SSRF)."""
    url = os.environ.get("ONIX_FABRIC_REFERENCE_URL", "").strip()
    if not url or not url.lower().startswith("https://"):
        return None
    import urllib.request

    req = urllib.request.Request(url)  # https only (vérifié ci-dessus)
    token = os.environ.get("ONIX_FABRIC_TOKEN", "").strip()
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 - URL d'exploitation env, https-only
        data = json.loads(resp.read().decode("utf-8"))
    return _select_record(data, key)
