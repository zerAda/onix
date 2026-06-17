"""llm — Assistance LLM locale via Ollama (onix-actions).

« En mieux » qu'AC360 : option d'extraction des champs canoniques depuis un texte
brut par un LLM LOCAL (Ollama, http://ollama:11434), sans aucun appel cloud.

Dégrade proprement : si Ollama est injoignable ou répond mal, on lève une erreur
claire que l'endpoint /audit convertit en repli (extraction heuristique). Le mode
réellement utilisé (« llm » / « heuristic ») est journalisé.

Durcissements :
  * timeouts EXPLICITES (connexion + lecture) → pas de blocage indéfini si Ollama
    est lent ou ne répond plus ;
  * parsing JSON ROBUSTE aux réponses bruitées (fences ```json, prose autour,
    objet imbriqué le plus à l'extérieur) ;
  * repli HEURISTIQUE propre côté endpoint si Ollama est indisponible/raté.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger("onix.actions.llm")


@dataclass(frozen=True)
class LLMUsage:
    """Comptage de tokens d'un appel LLM — GROUND TRUTH quand `measured=True`.

    Ollama renvoie déjà les VRAIS comptes dans la réponse de `/api/generate`
    (cf. https://github.com/ollama/ollama/blob/main/docs/api.md) :
      * `prompt_eval_count` -> tokens d'ENTRÉE réellement évalués ;
      * `eval_count`        -> tokens de SORTIE réellement générés ;
      * `prompt_eval_duration` / `eval_duration` / `total_duration` -> durées en
        NANOSECONDES.
    On capture ces champs au lieu d'estimer (chars/4). `measured=True` signale au
    FinOps que le chiffre est mesuré (et non estimé).

    `eval_tokens_per_second` est dérivé de `eval_count` / `eval_duration` (signal
    de perf réel) ; None si la durée est absente ou nulle.
    """

    input_tokens: int
    output_tokens: int
    measured: bool
    total_duration_ns: Optional[int] = None
    prompt_eval_duration_ns: Optional[int] = None
    eval_duration_ns: Optional[int] = None
    eval_tokens_per_second: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "measured": self.measured,
            "total_duration_ns": self.total_duration_ns,
            "prompt_eval_duration_ns": self.prompt_eval_duration_ns,
            "eval_duration_ns": self.eval_duration_ns,
            "eval_tokens_per_second": self.eval_tokens_per_second,
        }


def estimate_tokens(text: str) -> int:
    """Estimation HEURISTIQUE (~chars/4) du nombre de tokens d'un texte.

    Utilisée UNIQUEMENT pour le repli (pas d'appel LLM, donc aucun comptage réel
    disponible). Les événements correspondants sont marqués `measured=False`."""
    return max(0, len((text or "")) // 4)


def usage_from_ollama(data: Dict[str, Any]) -> Optional[LLMUsage]:
    """Construit un `LLMUsage` MESURÉ depuis une réponse `/api/generate`.

    Retourne None si les champs de comptage sont absents (réponse partielle /
    ancienne version d'Ollama) -> l'appelant retombera sur l'estimation."""
    if not isinstance(data, dict):
        return None
    pin = data.get("prompt_eval_count")
    pout = data.get("eval_count")
    if not isinstance(pin, int) or not isinstance(pout, int):
        return None
    eval_dur = data.get("eval_duration")
    eval_dur = eval_dur if isinstance(eval_dur, int) else None
    tps: Optional[float] = None
    if eval_dur and eval_dur > 0:
        # eval_count tokens en eval_duration ns -> tokens/s.
        tps = round(pout / (eval_dur / 1_000_000_000), 2)
    total_dur = data.get("total_duration")
    prompt_dur = data.get("prompt_eval_duration")
    return LLMUsage(
        input_tokens=pin,
        output_tokens=pout,
        measured=True,
        total_duration_ns=total_dur if isinstance(total_dur, int) else None,
        prompt_eval_duration_ns=prompt_dur if isinstance(prompt_dur, int) else None,
        eval_duration_ns=eval_dur,
        eval_tokens_per_second=tps,
    )

CANONICAL_FIELDS = (
    "nom_client",
    "plafond_hospitalisation",
    "date_effet",
    "numero_contrat",
    "motif_operation",
)

# Prompt few-shot : un exemple concret améliore NETTEMENT l'extraction par les
# petits modèles locaux (un modèle ~1B renvoie sinon le schéma à vide). L'exemple
# est volontairement générique (aucun lien avec les données réelles).
_PROMPT = (
    "Tu es un extracteur de données. À partir du TEXTE, renvoie UNIQUEMENT un objet "
    "JSON valide (aucun texte autour) avec EXACTEMENT ces clés : "
    + ", ".join(CANONICAL_FIELDS)
    + ". Mets null si une information est absente. Ne réécris pas les valeurs, "
    "recopie-les telles quelles.\n\n"
    "Exemple:\n"
    "TEXTE: La societe BETA SARL, plafond 500 euros, contrat ABC-123 du 5 mars 2023\n"
    'JSON: {{"nom_client": "BETA SARL", "plafond_hospitalisation": "500 euros", '
    '"date_effet": "5 mars 2023", "numero_contrat": "ABC-123", "motif_operation": null}}\n\n'
    "Maintenant fais de meme:\n"
    "TEXTE:\n{texte}\n\nJSON:"
)

# Timeouts par défaut (surchargés par env). Une connexion courte distingue
# « Ollama absent » d'« Ollama lent à générer » (lecture plus longue).
_DEFAULT_CONNECT_TIMEOUT = 5.0
_DEFAULT_READ_TIMEOUT = 60.0


def ollama_base_url() -> str:
    return os.environ.get("ONIX_OLLAMA_URL", "http://ollama:11434").rstrip("/")


def ollama_model() -> str:
    return os.environ.get("ONIX_LLM_MODEL", "llama3.2:3b")


def _connect_timeout() -> float:
    try:
        return float(os.environ.get("ONIX_LLM_CONNECT_TIMEOUT", _DEFAULT_CONNECT_TIMEOUT))
    except (TypeError, ValueError):
        return _DEFAULT_CONNECT_TIMEOUT


def _read_timeout() -> float:
    try:
        return float(os.environ.get("ONIX_LLM_TIMEOUT", _DEFAULT_READ_TIMEOUT))
    except (TypeError, ValueError):
        return _DEFAULT_READ_TIMEOUT


def _balanced_json_object(raw: str) -> Optional[str]:
    """Extrait la 1re sous-chaîne `{...}` à accolades ÉQUILIBRÉES (gère les objets
    imbriqués et la prose autour, contrairement à un regex glouton naïf)."""
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return None


def _extract_json(raw: str) -> Optional[dict]:
    """Parsing tolérant aux réponses LLM bruitées.

    Stratégie en cascade :
      1. JSON direct (après suppression d'éventuels fences markdown) ;
      2. objet à accolades équilibrées repéré dans le texte ;
      3. fallback : regex glouton.
    Ne renvoie qu'un dict (jamais une liste/scalaire éventuels)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    # Retire d'éventuels fences ```json ... ```
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    for candidate in (cleaned, raw):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    # Objet à accolades équilibrées (robuste à la prose / l'objet imbriqué).
    block = _balanced_json_object(cleaned) or _balanced_json_object(raw)
    if block:
        try:
            parsed = json.loads(block)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    # Dernier recours : regex glouton (best-effort).
    m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


def _filter_canonical(parsed: dict) -> Dict[str, Any]:
    """Ne garde que les clés canoniques connues, valeurs non vides."""
    return {k: parsed.get(k) for k in CANONICAL_FIELDS if parsed.get(k) not in (None, "")}


def extract_fields_llm_with_usage(
    text: str, *, timeout: Optional[float] = None
) -> Tuple[Dict[str, Any], LLMUsage]:
    """Comme `extract_fields_llm` mais renvoie AUSSI les VRAIS comptes de tokens.

    Retourne `(champs, usage)` où `usage` est un `LLMUsage` MESURÉ (issu de
    `prompt_eval_count` / `eval_count` d'Ollama) quand ces champs sont présents.
    Si Ollama répond sans les comptes (cas rare / vieille version), on retombe sur
    une estimation chars/4 marquée `measured=False` — le résultat d'extraction
    reste valable, seule la métrique de coût est dégradée.

    Lève (RuntimeError / ValueError) aux MÊMES conditions que l'API historique :
    l'appelant gère le repli heuristique. La compatibilité ascendante est assurée
    par `extract_fields_llm` (renvoie uniquement les champs)."""
    if not (text or "").strip():
        raise ValueError("Texte vide.")
    model = ollama_model()
    url = f"{ollama_base_url()}/api/generate"
    # NB : on n'impose PAS `format: json` côté Ollama. Sur les petits modèles
    # locaux (~1B), la grammaire JSON stricte d'Ollama dégrade fortement
    # l'extraction (le modèle renvoie un objet à vide). On laisse le modèle
    # générer librement (souvent du JSON entouré de prose / fences markdown) et
    # on s'appuie sur `_extract_json` (parsing robuste) pour récupérer l'objet.
    payload = {
        "model": model,
        "prompt": _PROMPT.format(texte=text[:8000]),
        "stream": False,
        "options": {"temperature": 0},
    }
    read_timeout = timeout if timeout is not None else _read_timeout()
    timeouts = httpx.Timeout(connect=_connect_timeout(), read=read_timeout, write=10.0, pool=5.0)
    try:
        resp = httpx.post(url, json=payload, timeout=timeouts)
        resp.raise_for_status()
    except Exception as e:  # réseau / Ollama absent / timeout / HTTP non-2xx
        logger.warning("Ollama indisponible (modèle=%s): %s", model, type(e).__name__)
        raise RuntimeError(f"Ollama indisponible: {e}") from e

    try:
        data = resp.json()
    except Exception as e:
        logger.warning("Réponse Ollama non-JSON: %s", type(e).__name__)
        raise RuntimeError("Réponse Ollama non-JSON.") from e

    parsed = _extract_json(data.get("response", ""))
    if not isinstance(parsed, dict):
        logger.warning("Réponse LLM non exploitable (JSON invalide).")
        raise RuntimeError("Réponse LLM non exploitable (JSON invalide).")
    fields = _filter_canonical(parsed)
    # GROUND TRUTH : on capture les comptes RÉELS renvoyés par Ollama. Si absents
    # (réponse partielle), on dégrade vers une estimation chars/4 (measured=False).
    usage = usage_from_ollama(data)
    if usage is None:
        response_text = data.get("response", "") or ""
        usage = LLMUsage(
            input_tokens=estimate_tokens(_PROMPT.format(texte=text[:8000])),
            output_tokens=estimate_tokens(response_text),
            measured=False,
        )
        logger.info(
            "Comptes de tokens Ollama absents -> estimation (modèle=%s).", model
        )
    logger.info(
        "Extraction LLM réussie (modèle=%s, champs=%d, in=%d, out=%d, measured=%s)",
        model, len(fields), usage.input_tokens, usage.output_tokens, usage.measured,
    )
    return fields, usage


def extract_fields_llm(text: str, *, timeout: Optional[float] = None) -> Dict[str, Any]:
    """Extrait les champs canoniques d'un texte via Ollama. Lève en cas d'échec.

    Wrapper de COMPATIBILITÉ ASCENDANTE : renvoie uniquement les champs (comme
    historiquement). Pour récupérer aussi les VRAIS comptes de tokens, utiliser
    `extract_fields_llm_with_usage`.

    `timeout` (optionnel) surcharge le timeout de LECTURE ; la connexion garde un
    timeout court dédié pour détecter rapidement un Ollama absent."""
    fields, _usage = extract_fields_llm_with_usage(text, timeout=timeout)
    return fields
