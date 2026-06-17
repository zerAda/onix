"""cost_tracker — FinOps (onix-actions).

Porte cost_tracker d'AC360 : rate card paramétrable (`ONIX_RATE_CARD`, JSON),
estimation par centre de coût, budget (`ONIX_BUDGET_EUR`) et alerte de seuil
(`ONIX_BUDGET_WARN_PCT`, défaut 80 %). Identifiants hashés SHA-256.

Les centres de coût sont génériques (local-first) : un déploiement Ollama coûte
0 € par défaut, mais le client peut valoriser l'électricité, l'amortissement GPU,
le stockage, etc. via la rate card.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .admin_state import hash_id

COST_CENTERS = (
    "llm_message",
    "llm_token_input",
    "llm_token_output",
    "ocr_page",
    "audit_request",
    "fiche_generation",
    "notification",
    "storage",
    "compute_hour",
)
DEFAULT_RATE_CARD: Dict[str, float] = {cc: 0.0 for cc in COST_CENTERS}

_RATE_CARD_ENV = "ONIX_RATE_CARD"
_BUDGET_ENV = "ONIX_BUDGET_EUR"
_BUDGET_WARN_PCT_ENV = "ONIX_BUDGET_WARN_PCT"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_rate_card() -> Dict[str, float]:
    card = dict(DEFAULT_RATE_CARD)
    raw = os.environ.get(_RATE_CARD_ENV)
    if raw:
        try:
            override = json.loads(raw)
            for cc, val in override.items():
                if cc in card and isinstance(val, (int, float)) and val >= 0:
                    card[cc] = float(val)
        except (ValueError, TypeError):
            pass
    return card


def get_unit_cost(cost_center: str) -> float:
    return load_rate_card().get(cost_center, 0.0)


def estimate_cost(
    cost_center: str,
    quantity: float,
    *,
    unit: str = "request",
    environment: Optional[str] = None,
    client_id: Optional[str] = None,
    use_case: Optional[str] = None,
    event_id: Optional[str] = None,
    timestamp_utc: Optional[str] = None,
    measured: bool = False,
) -> Dict[str, Any]:
    """Valorise une `quantity` pour un centre de coût.

    Deux dimensions DISTINCTES de fiabilité :
      * `cost_source` (PARAMETRABLE / A_VALIDER) qualifie le TARIF unitaire : une
        rate card a-t-elle été fournie ?
      * `measured` (bool) qualifie la QUANTITÉ : pour les centres de coût tokens
        (`llm_token_input` / `llm_token_output`), True = comptes RÉELS d'Ollama
        (`prompt_eval_count` / `eval_count`), False = estimation chars/4. C'est ce
        flag que le FinOps utilise pour distinguer mesuré d'estimé.
    """
    if cost_center not in COST_CENTERS:
        raise ValueError(f"cost_center inconnu : {cost_center}")
    if quantity < 0:
        raise ValueError("quantity ne peut pas être négative")

    unit_cost = get_unit_cost(cost_center)
    amount = round(unit_cost * float(quantity), 6)
    source = "PARAMETRABLE" if unit_cost > 0 else "A_VALIDER"

    return {
        "event_id": event_id or str(uuid.uuid4()),
        "timestamp_utc": timestamp_utc or _now_iso(),
        "environment": environment or os.environ.get("ONIX_ENVIRONMENT", "dev"),
        "cost_center": cost_center,
        "quantity": float(quantity),
        "unit": unit,
        "unit_cost_eur": unit_cost,
        "estimated_cost_eur": amount,
        "cost_source": source,
        "measured": bool(measured),
        "client_id_hash": hash_id(client_id) if client_id else None,
        "use_case": use_case,
    }


def _budget_eur() -> Optional[float]:
    raw = os.environ.get(_BUDGET_ENV)
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _warn_pct() -> float:
    raw = os.environ.get(_BUDGET_WARN_PCT_ENV, "80")
    try:
        return float(raw)
    except ValueError:
        return 80.0


def check_budget(
    spent_eur: float,
    budget_eur: Optional[float] = None,
    warn_pct: Optional[float] = None,
) -> Dict[str, Any]:
    budget = budget_eur if budget_eur is not None else _budget_eur()
    pct_threshold = warn_pct if warn_pct is not None else _warn_pct()

    if budget is None or budget <= 0:
        return {
            "level": "unknown",
            "spent_eur": round(spent_eur, 4),
            "budget_eur": None,
            "ratio_pct": None,
        }

    ratio = (spent_eur / budget) * 100.0
    if ratio >= 100.0:
        level = "exceeded"
    elif ratio >= pct_threshold:
        level = "warning"
    else:
        level = "ok"
    return {
        "level": level,
        "spent_eur": round(spent_eur, 4),
        "budget_eur": round(budget, 4),
        "ratio_pct": round(ratio, 2),
        "warn_pct": pct_threshold,
    }
