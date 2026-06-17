"""Harnais LIVE d'extraction audit — LLM (Ollama ≥ 7B) vs heuristique.

Objectif (livrable 2) : prouver, sur un VRAI modèle, la valeur de l'extraction
LLM des champs canoniques d'audit face à la baseline heuristique « clé : valeur ».

On réutilise la BRIQUE DE PRODUCTION `onix-actions` :
  * `actions.app.llm.extract_fields_llm`            — extraction LLM (Ollama) ;
  * `actions.app.ocr._kv_pairs_from_text`           — heuristique de production ;
  * `actions.app.audit_engine.extract_canonical_fields` — aliasing canonique.

Le texte de test est volontairement **désordonné** (prose, libellés noyés, ordre
non canonique) : c'est précisément le cas où la baseline heuristique
« libellé : valeur par ligne » décroche et où un LLM ≥ 7B apporte un gain.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# Rendre la brique de production `actions/` importable depuis tests/rag.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Champs canoniques audités.
CANONICAL_FIELDS = (
    "nom_client",
    "plafond_hospitalisation",
    "date_effet",
    "numero_contrat",
    "motif_operation",
)


# ── Jeux de test : texte DÉSORDONNÉ + vérité terrain par champ ───────────────
# La vérité terrain liste, pour chaque champ, les sous-chaînes acceptables
# (le LLM peut recopier « 1 200 € » ou « 1200 euros » ; on tolère).
SAMPLES: List[Dict] = [
    {
        "id": "EX01",
        "text": (
            "Compte-rendu de visite. Nous avons rencontré la société NOVATEX "
            "INDUSTRIE pour son contrat collectif. Le numéro de police est "
            "le POL-2024-7788 et la prise d'effet remonte au 12/03/2024. "
            "Concernant l'hospitalisation, le plafond négocié s'élève à 1 500 € "
            "par an. L'opération en cours est un avenant de mise à jour des "
            "garanties."
        ),
        "truth": {
            "nom_client": ["novatex"],
            "numero_contrat": ["POL-2024-7788", "pol20247788", "pol-2024-7788"],
            "date_effet": ["12/03/2024", "12 03 2024"],
            "plafond_hospitalisation": ["1 500", "1500"],
            "motif_operation": ["avenant"],
        },
    },
    {
        "id": "EX02",
        "text": (
            "Bonjour, suite à notre échange je confirme les éléments du dossier. "
            "Il s'agit d'ZENITH ASSURANCES SA. La date d'effet du contrat "
            "n° ZN-55-2023 est fixée au 1er janvier 2023. Le plafond "
            "d'hospitalisation prévu dans l'offre est de 2000 euros. "
            "Motif : renouvellement annuel."
        ),
        "truth": {
            "nom_client": ["zenith"],
            "numero_contrat": ["ZN-55-2023", "zn552023", "zn-55-2023"],
            "date_effet": ["1er janvier 2023", "1 janvier 2023", "janvier 2023"],
            "plafond_hospitalisation": ["2000", "2 000"],
            "motif_operation": ["renouvellement"],
        },
    },
    {
        "id": "EX03",
        "text": (
            "Note interne — dossier prioritaire. Le client (ATLAS LOGISTIQUE) "
            "souhaite revoir sa couverture. Référence contrat : ATL/2025/014. "
            "Effet au 05-06-2025. Plafond hospi : 800€. "
            "Il s'agit d'une résiliation suivie d'une nouvelle souscription."
        ),
        "truth": {
            "nom_client": ["atlas"],
            "numero_contrat": ["ATL/2025/014", "atl2025014", "atl/2025/014"],
            "date_effet": ["05-06-2025", "05 06 2025"],
            "plafond_hospitalisation": ["800"],
            "motif_operation": ["résiliation", "resiliation", "souscription"],
        },
    },
]


def _norm(s) -> str:
    return str(s or "").lower().replace(" ", " ").strip()


def _field_ok(value, accepted: List[str]) -> bool:
    v = _norm(value)
    if not v:
        return False
    return any(_norm(a) in v or v in _norm(a) for a in accepted)


def score_extraction(extracted: Dict[str, str], truth: Dict[str, List[str]]) -> Tuple[int, int]:
    """Renvoie (champs_corrects, champs_attendus) pour un échantillon."""
    correct = 0
    total = len(truth)
    for field, accepted in truth.items():
        if _field_ok(extracted.get(field), accepted):
            correct += 1
    return correct, total


def heuristic_extract(text: str) -> Dict[str, str]:
    """Baseline de PRODUCTION : `_kv_pairs_from_text` + aliasing canonique."""
    from actions.app.ocr import _kv_pairs_from_text
    from actions.app.audit_engine import extract_canonical_fields

    pseudo_ocr = {"fields": _kv_pairs_from_text(text), "tables": []}
    return extract_canonical_fields(pseudo_ocr)


def llm_extract(text: str) -> Dict[str, str]:
    """Extraction LLM de PRODUCTION via Ollama (lève si Ollama indisponible)."""
    from actions.app.llm import extract_fields_llm

    return extract_fields_llm(text)


def run_extraction_comparison(model_env_already_set: bool = True) -> Dict:
    """Exécute la comparaison sur tous les échantillons et renvoie un rapport.

    Le modèle Ollama utilisé est celui de `actions.app.llm.ollama_model()`
    (variable ONIX_LLM_MODEL) ; l'URL via ONIX_OLLAMA_URL. On force ici un
    plancher ≥ 7B côté appelant (cf. test) pour rester aligné avec la consigne.
    """
    rows = []
    h_correct = h_total = l_correct = l_total = 0
    for s in SAMPLES:
        heur = heuristic_extract(s["text"])
        hc, ht = score_extraction(heur, s["truth"])
        h_correct += hc
        h_total += ht
        try:
            llm = llm_extract(s["text"])
            lc, lt = score_extraction(llm, s["truth"])
            llm_err = None
        except Exception as e:  # pragma: no cover - dépend de l'env live
            llm, lc, lt, llm_err = {}, 0, len(s["truth"]), f"{type(e).__name__}: {e}"
        l_correct += lc
        l_total += lt
        rows.append({
            "id": s["id"],
            "heuristic_fields": heur,
            "heuristic_score": f"{hc}/{ht}",
            "llm_fields": llm,
            "llm_score": f"{lc}/{lt}",
            "llm_error": llm_err,
        })
    return {
        "rows": rows,
        "heuristic_total": f"{h_correct}/{h_total}",
        "llm_total": f"{l_correct}/{l_total}",
        "heuristic_rate": round(100 * h_correct / h_total, 1) if h_total else 0.0,
        "llm_rate": round(100 * l_correct / l_total, 1) if l_total else 0.0,
        "model": os.environ.get("ONIX_LLM_MODEL", "llama3.2:3b"),
    }
