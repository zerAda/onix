"""Ré-export du garde-fou déterministe — la source unique vit côté production
(`actions/app/guardrail_core.py`). Conservé ici pour les tests RAG et les scripts
(`from guardrail_postfilter import ...`), y compris HORS pytest (run_live.py)."""
import os as _os
import sys as _sys

_ACTIONS = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", "..", "actions"))
if _ACTIONS not in _sys.path:
    _sys.path.insert(0, _ACTIONS)  # rend `app.guardrail_core` importable hors pytest aussi

from app.guardrail_core import *  # noqa: E402,F401,F403
from app.guardrail_core import (  # noqa: E402  ré-exports nommés explicites
    FilterResult, REFUSAL_READ_ONLY, REFUSAL_NOT_AVAILABLE, REFUSAL_NO_CITATION,
    REFUSAL_INJECTION, post_filter, has_citation, leaks_prompt_or_persona,
    claims_write_action, relays_exfil_link, asserts_a_fact, is_write_request,
    confirms_inaccessible_resource, is_inaccessible_resource_request,
    is_general_knowledge_request, is_already_safe_answer,
)
