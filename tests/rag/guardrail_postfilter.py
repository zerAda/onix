"""Ré-export du garde-fou déterministe — la source unique vit côté production
(`actions/app/guardrail_core.py`). Conservé ici pour les tests RAG existants
(`from guardrail_postfilter import ...`)."""
from app.guardrail_core import *  # noqa: F401,F403
from app.guardrail_core import (  # ré-exports nommés explicites (pour les tests)
    FilterResult, REFUSAL_READ_ONLY, REFUSAL_NOT_AVAILABLE, REFUSAL_NO_CITATION,
    REFUSAL_INJECTION, post_filter, has_citation, leaks_prompt_or_persona,
    claims_write_action, relays_exfil_link, asserts_a_fact, is_write_request,
    confirms_inaccessible_resource, is_inaccessible_resource_request,
    is_general_knowledge_request, is_already_safe_answer,
)
