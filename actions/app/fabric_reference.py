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

import csv
import io
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


# ── Export CSV de la synthèse de portefeuille (back-office → Excel) ───────────
# Préfixes de formule Excel/Sheets à neutraliser (anti-injection CSV, CWE-1236).
# Jeu COMPLET recommandé par l'OWASP : `= + - @` PLUS la tabulation et le retour
# chariot en tête (eux aussi interprétés comme amorce de formule par les tableurs).
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: Any) -> str:
    """Valeur prête pour CSV : None → vide, et ANTI-INJECTION — une valeur commençant
    par un préfixe de formule (`= + - @`, TAB ou CR) est préfixée d'une apostrophe pour
    qu'Excel/Sheets l'affiche en TEXTE au lieu de l'ÉVALUER (exfiltration / exécution)."""
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in _CSV_FORMULA_PREFIXES:
        return "'" + s
    return s


def batch_to_csv(rapport: Any) -> str:
    """Export CSV des FICHES d'un rapport `reconcile_batch` (back-office → Excel).

    Colonnes : ``client, verdict, a_revoir, nb_ecarts, recommandation``. Échappement
    CSV natif (stdlib `csv` : virgules / guillemets / retours-ligne dans une valeur
    sont correctement protégés) + anti-injection de formule. **Fail-closed et SANS
    exception** : un rapport mal formé ⇒ CSV avec uniquement l'en-tête. N'ajoute AUCUNE
    PII au-delà de ce que la fiche porte déjà (`client` y figure ; lecture seule)."""
    colonnes = ["client", "verdict", "a_revoir", "nb_ecarts", "recommandation"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(colonnes)
    fiches = rapport.get("fiches") if isinstance(rapport, dict) else None
    for fiche in fiches if isinstance(fiches, list) else []:
        if not isinstance(fiche, dict):
            continue
        writer.writerow([_csv_safe(fiche.get(c)) for c in colonnes])
    return buf.getvalue()


# ── Synthèse CLIENT-360 (agrège réf SI + tâches + usage, RGPD-safe) ───────────
def _default_open_tasks(client_key: Any) -> list:
    """Tâches OUVERTES du client (filtrées par HASH). Best-effort, fail-safe → [].
    Import LOCAL (tasks/admin_state) pour éviter tout cycle au chargement.

    Choix : passe par l'API `tasks.list_tasks` (DÉCOUPLÉ du schéma SQL) puis filtre
    par hash en Python — O(tâches ouvertes), suffisant à l'échelle POC. À passer en
    SQL filtré `WHERE client_id_hash=? AND status='open'` si le volume devient élevé.

    **Data-minimisation (RGPD)** : la vue 360 ne remonte qu'un RÉSUMÉ d'identification
    (`task_id, title, due_date, status`). On EXCLUT `notes` (champ libre, PII probable),
    les hash internes (`client_id_hash`/`owner_hash`) et `webhook_status` (hors-sujet)."""
    try:
        from . import tasks
        from .admin_state import hash_id
        h = hash_id(client_key)
        if not h:
            return []
        return [
            {"task_id": t.get("task_id"), "title": t.get("title"),
             "due_date": t.get("due_date"), "status": t.get("status")}
            for t in tasks.list_tasks(status="open")
            if isinstance(t, dict) and t.get("client_id_hash") == h
        ]
    except Exception:
        return []


def _default_usage_count(client_key: Any) -> int:
    """Nombre d'événements d'usage du client (par HASH, lecture seule). Best-effort,
    fail-safe → 0 (table absente / base inaccessible)."""
    try:
        from .admin_state import _connect, _lock, hash_id
        h = hash_id(client_key)
        if not h:
            return 0
        with _lock, _connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM usage_events WHERE client_id_hash=?", (h,)
            ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def client_360(
    client_key: Any,
    *,
    reference_reader: Optional[ReferenceReader] = None,
    tasks_lister: Optional[Callable[[Any], list]] = None,
    usage_counter: Optional[Callable[[Any], int]] = None,
) -> Dict[str, Any]:
    """Vue **client-360** : agrège en une synthèse ce qu'onix sait d'un client — sa
    **référence SI** (Fabric), ses **tâches ouvertes** et son **volume d'usage**.

    Sources INJECTABLES (tests offline) ; sinon défauts réels (SI + base SQLite, par
    HASH). RGPD : opère par hash (`admin_state.hash_id`), **LECTURE SEULE** (n'écrit
    RIEN), **fail-closed et SANS exception** — toute erreur d'une source ⇒ cette source
    vide/0, jamais propagée. C'est la brique « Assistant Client 360 ».

    CONTRAT (important) : `client_key` doit être le **même identifiant canonique** que
    celui passé en `client_id` à `tasks.create_task` et au tracking d'usage — c'est sur
    son HASH que tâches et usage sont retrouvés. Une clé différente (nom vs SIRET vs id)
    renverrait 0 tâche / 0 usage À TORT (fail-closed, jamais d'erreur, mais incohérent)."""
    # (1) Référence SI : fail-closed → None si client absent / source non configurée.
    try:
        reference = fetch_client_reference(client_key, reader=reference_reader)
    except Exception:
        reference = None
    # (2) Tâches ouvertes : source injectée OU défaut base, chaque source ISOLÉE.
    try:
        taches = (tasks_lister(client_key) if tasks_lister is not None
                  else _default_open_tasks(client_key)) or []
    except Exception:
        taches = []
    if not isinstance(taches, list):
        taches = []
    # (3) Volume d'usage : source injectée OU défaut base.
    try:
        nb_usage = int(usage_counter(client_key) if usage_counter is not None
                       else _default_usage_count(client_key))
    except Exception:
        nb_usage = 0
    return {
        "client_key": client_key,
        "reference": reference,
        "reference_trouvee": reference is not None,
        "nb_taches_ouvertes": len(taches),
        "taches_ouvertes": taches,
        "nb_evenements_usage": nb_usage,
    }


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
