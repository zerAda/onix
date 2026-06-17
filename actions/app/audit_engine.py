"""audit_engine — Moteur d'audit documentaire générique, robuste et typé.

Import-safe : n'importe AUCUN SDK cloud, ne lit AUCUNE donnée externe. Pur calcul.

Rôle :
  * aliasing des libellés OCR arbitraires (« Raison sociale », « Plafond hospi. »…)
    vers des champs canoniques ;
  * normalisation des montants / dates / noms / numéros de contrat ;
  * comparaison TYPÉE document vs enregistrement de référence avec statut
    MATCH / MISMATCH / UNCERTAIN / MISSING et score de confiance par champ ;
  * verdict global CONFORME / ECART / INCERTAIN / CLIENT_NON_TROUVE.

Aucune dépendance à un fournisseur d'IA, de stockage ou de bureautique : c'est
une bibliothèque de calcul pur, réutilisable et testable hors ligne.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:  # comparaison de noms ; dégradée (égalité stricte) si thefuzz absent
    from thefuzz import fuzz as _fuzz
except Exception:  # pragma: no cover - dépend de l'environnement
    _fuzz = None

# ---------------------------------------------------------------------------
# Aliasing libellés OCR -> champs canoniques
# ---------------------------------------------------------------------------
_FIELD_ALIASES: Dict[str, List[str]] = {
    "nom_client": [
        "nom client", "raison sociale", "client", "societe", "denomination",
        "souscripteur", "assure", "nom",
    ],
    "plafond_hospitalisation": [
        "plafond hospitalisation", "plafond hospi", "hospitalisation",
        "chambre particuliere", "plafond chambre", "plafond",
    ],
    "date_effet": ["date d effet", "date effet", "prise d effet", "effet"],
    "numero_contrat": [
        "numero de contrat", "numero contrat", "n contrat", "no contrat",
        "numero police", "police", "contrat",
    ],
    "motif_operation": [
        "motif operation", "motif", "nature operation", "objet",
    ],
    "siret": ["siret", "n siret", "numero siret", "siren"],
}

# Tolérances / seuils (centralisés).
AMOUNT_ABS_TOL = 0.01
AMOUNT_REL_TOL = 0.01      # 1 % -> UNCERTAIN
NAME_MATCH_MIN = 95        # >= -> MATCH
NAME_UNCERTAIN_MIN = 85    # >= -> UNCERTAIN, sinon MISMATCH


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _norm_key(text: Any) -> str:
    """Normalise un libellé : sans accents, minuscules, alphanum + espaces."""
    t = _strip_accents(str(text or "")).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def alias_field(label: Any) -> Optional[str]:
    """Retourne le champ canonique correspondant à un libellé OCR, sinon None.

    Recherche déterministe : pour chaque champ, le libellé normalisé doit
    contenir l'un des alias normalisés. On teste les alias les plus spécifiques
    d'abord (longueur décroissante) pour éviter qu'un alias générique (« nom »)
    ne capture un libellé plus précis.
    """
    norm = _norm_key(label)
    if not norm:
        return None
    best: Optional[Tuple[int, str]] = None  # (longueur alias, champ)
    for canonical, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            na = _norm_key(alias)
            if na and (na in norm or norm in na):
                if best is None or len(na) > best[0]:
                    best = (len(na), canonical)
    return best[1] if best else None


def _value_of(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("value", "")).strip()
    return str(raw or "").strip()


def _find_in_tables(tables: List[dict], aliases: List[str]) -> Optional[str]:
    """Trouve une valeur par adjacence géométrique (cellule à droite, sinon
    dessous) de la cellule-libellé, via row_index/column_index."""
    norm_aliases = [_norm_key(a) for a in aliases]
    for table in tables or []:
        cells = table.get("cells", [])
        grid = {
            (c.get("row_index"), c.get("column_index")): str(c.get("content", "")).strip()
            for c in cells
        }
        for c in cells:
            content_norm = _norm_key(c.get("content", ""))
            if not content_norm:
                continue
            if any(a and a in content_norm for a in norm_aliases):
                r, col = c.get("row_index"), c.get("column_index")
                if r is None or col is None:
                    continue
                right = grid.get((r, col + 1))
                if right:
                    return right
                below = grid.get((r + 1, col))
                if below:
                    return below
    return None


def extract_canonical_fields(ocr_result: dict) -> Dict[str, str]:
    """Construit un dict de champs canoniques à partir d'une sortie OCR brute
    (libellés arbitraires). Ne lève jamais."""
    out: Dict[str, str] = {}
    fields = ocr_result.get("fields", {}) if isinstance(ocr_result, dict) else {}
    for raw_key, raw_val in fields.items():
        canonical = alias_field(raw_key)
        if canonical and canonical not in out:
            value = _value_of(raw_val)
            if value:
                out[canonical] = value
    # Fallback géométrique dans les tableaux pour le plafond.
    if "plafond_hospitalisation" not in out:
        v = _find_in_tables(
            ocr_result.get("tables", []) if isinstance(ocr_result, dict) else [],
            _FIELD_ALIASES["plafond_hospitalisation"],
        )
        if v:
            out["plafond_hospitalisation"] = v
    return out


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
def normalize_amount(value: Any) -> Optional[float]:
    """'1 000,50 €' -> 1000.5 ; '1,000.50' -> 1000.5 ; '2000' -> 2000.0."""
    if value is None:
        return None
    t = str(value).replace(" ", " ")
    t = re.sub(r"[^0-9,.\-]", "", t)
    if not t or t in {"-", ".", ","}:
        return None
    if "," in t and "." in t:
        # Le dernier séparateur rencontré est le séparateur décimal.
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def normalize_date(value: Any) -> Optional[str]:
    """Retourne une date ISO 'YYYY-MM-DD' ou None si non parsable."""
    if not value:
        return None
    t = str(value).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_name(value: Any) -> str:
    if not value:
        return ""
    t = _strip_accents(str(value)).upper()
    t = re.sub(r"[^A-Z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def normalize_contract(value: Any) -> str:
    if not value:
        return ""
    return re.sub(r"[^A-Za-z0-9]+", "", str(value)).upper()


# ---------------------------------------------------------------------------
# Comparaison typée
# ---------------------------------------------------------------------------
def _name_score(a: str, b: str) -> int:
    if _fuzz is None:  # pragma: no cover - dépend de l'environnement
        return 100 if a == b else 0
    return int(_fuzz.token_sort_ratio(a, b))


def compare_name(doc: Any, ref: Any) -> Tuple[str, float]:
    a, b = normalize_name(doc), normalize_name(ref)
    if not a or not b:
        return "MISSING", 0.0
    score = _name_score(a, b)
    if score >= NAME_MATCH_MIN:
        return "MATCH", round(score / 100, 3)
    if score >= NAME_UNCERTAIN_MIN:
        return "UNCERTAIN", round(score / 100, 3)
    return "MISMATCH", round(1 - score / 100, 3)


def compare_amount(doc: Any, ref: Any) -> Tuple[str, float]:
    a, b = normalize_amount(doc), normalize_amount(ref)
    if a is None or b is None:
        return "MISSING", 0.0
    if abs(a - b) <= AMOUNT_ABS_TOL:
        return "MATCH", 1.0
    if b and abs(a - b) / abs(b) <= AMOUNT_REL_TOL:
        return "UNCERTAIN", 0.5
    return "MISMATCH", 0.95


def compare_date(doc: Any, ref: Any) -> Tuple[str, float]:
    a, b = normalize_date(doc), normalize_date(ref)
    if a is None or b is None:
        return ("MISSING", 0.0) if (not doc or not ref) else ("UNCERTAIN", 0.4)
    return ("MATCH", 1.0) if a == b else ("MISMATCH", 0.95)


def compare_contract(doc: Any, ref: Any) -> Tuple[str, float]:
    a, b = normalize_contract(doc), normalize_contract(ref)
    if not a or not b:
        return "MISSING", 0.0
    return ("MATCH", 1.0) if a == b else ("MISMATCH", 0.95)


_COMPARATORS = {
    "name": compare_name,
    "amount": compare_amount,
    "date": compare_date,
    "contract": compare_contract,
}

_COMMENTS = {
    "MATCH": "Conforme",
    "MISMATCH": "Écart critique",
    "UNCERTAIN": "À vérifier (proche mais non identique)",
    "MISSING": "Donnée absente d'un des deux côtés",
}


def compare_field(champ: str, doc_val: Any, ref_val: Any, kind: str) -> dict:
    statut, confiance = _COMPARATORS[kind](doc_val, ref_val)
    return {
        "champ": champ,
        "valeur_document": doc_val if doc_val not in (None, "") else None,
        "valeur_reference": ref_val if ref_val not in (None, "") else None,
        "statut": statut,
        "confiance": confiance,
        "commentaire": _COMMENTS[statut],
    }


# Champs audités : (champ canonique, type de comparaison)
_AUDIT_FIELDS = [
    ("plafond_hospitalisation", "amount"),
    ("date_effet", "date"),
    ("numero_contrat", "contract"),
]


def audit(audit_input: dict) -> dict:
    """Compare document vs référence et produit un audit_result typé.

    audit_input : {"document": {...}, "reference": {...}}. Ne lit aucune donnée
    externe : tout est fourni dans l'argument.
    """
    document = (audit_input or {}).get("document", {}) or {}
    reference = (audit_input or {}).get("reference", {}) or {}

    name_field = compare_field(
        "nom_client", document.get("nom_client"), reference.get("nom_client"), "name"
    )
    fields = [name_field]
    for champ, kind in _AUDIT_FIELDS:
        fields.append(compare_field(champ, document.get(champ), reference.get(champ), kind))

    name_status = name_field["statut"]
    other = fields[1:]
    if name_status in ("MISSING", "MISMATCH"):
        verdict = "CLIENT_NON_TROUVE"
    elif any(f["statut"] == "UNCERTAIN" for f in fields):
        verdict = "INCERTAIN"
    elif any(f["statut"] == "MISMATCH" for f in other):
        verdict = "ECART"
    else:
        verdict = "CONFORME"

    motif = document.get("motif_operation")
    return {
        "client_document": document.get("nom_client"),
        "meilleur_match_reference": reference.get("nom_client"),
        "score_correspondance_nom": round(name_field["confiance"] * 100, 1),
        "motif_operation": motif or "NON_DETERMINE",
        "motif_source": "ocr" if motif else "absent",
        "verdict": verdict,
        "fields": fields,
    }
