"""rag_local — **RAG NON-AGENTIQUE** souverain : récupération + génération locale.

Contourne la boucle agentique d'Onyx 4.1.1 — **cassée avec les modèles locaux**
(diagnostic #12 : Onyx passe les outils par le prompt, le modèle les recopie en
texte au lieu d'émettre un `tool_calls` ; même l'étape réponse post-récupération
revient vide). Ici on **ne dépend PAS du tool-calling** : on récupère le(s)
document(s) pertinent(s), puis on génère une réponse **grounded** par appel direct
au modèle (Ollama `/api/generate`). C'est la bonne architecture pour un modèle
local : ``retrieve → stuff context → generate``.

Le **lecteur de documents** et le **générateur** sont **injectables** → tests 100 %
offline. Pur et **fail-closed** : aucune source pertinente ⇒ refus explicite (on
n'invente JAMAIS), génération en erreur ⇒ `grounded=False` (jamais de fuite).
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Sequence

# Mots « significatifs » (≥4 lettres, accents FR inclus) pour le score de pertinence.
_WORD = re.compile(r"[a-zàâäéèêëïîôöùûüç]{4,}")


def _keywords(text: Any) -> set:
    return set(_WORD.findall(str(text or "").lower()))


def retrieve(
    question: str, documents: Sequence[Dict[str, Any]], *, top_k: int = 1
) -> List[Dict[str, Any]]:
    """Renvoie les ``top_k`` documents dont le **contenu recouvre le plus** la
    question (score = nombre de mots-clés partagés ; 0 = écarté). ``documents`` :
    liste de ``{"id": ..., "content": ...}``. Déterministe, sans I/O."""
    q = _keywords(question)
    if not q:
        return []
    scored = []
    for d in documents or []:
        if not isinstance(d, dict):
            continue
        score = len(q & _keywords(d.get("content", "")))
        if score > 0:
            scored.append((score, d))
    # Tri stable par score décroissant (préserve l'ordre d'entrée à score égal).
    scored.sort(key=lambda s: s[0], reverse=True)
    return [d for _, d in scored[: max(1, top_k)]]


def build_rag_prompt(question: str, contexts: Sequence[str]) -> str:
    """Construit le prompt **grounded** : répondre à partir du SEUL contexte."""
    ctx = "\n\n".join(str(c) for c in contexts if c)
    return (
        "Tu es l'assistant client GEREP, souverain et local. Réponds en français, "
        "de façon concise, UNIQUEMENT à partir du CONTEXTE ci-dessous. Si le contexte "
        "ne contient pas la réponse, dis-le explicitement. Cite le numéro de dossier.\n\n"
        "CONTEXTE:\n" + ctx + "\n\nQUESTION: " + str(question) + "\n\nRÉPONSE:"
    )


# Générateur : prompt -> texte de réponse (par défaut : Ollama ; injectable en test).
Generator = Callable[[str], str]


def answer(
    question: str,
    documents: Sequence[Dict[str, Any]],
    *,
    generator: Generator,
    top_k: int = 1,
) -> Dict[str, Any]:
    """RAG non-agentique : **récupère puis génère**. Renvoie
    ``{"answer", "sources", "grounded", "reason"?}``.

    **Fail-closed** : question vide / aucune source pertinente ⇒ refus explicite
    (``grounded=False``, pas d'invention) ; générateur en erreur ⇒ ``grounded=False``
    sans fuite d'exception."""
    if not str(question or "").strip():
        return {"answer": "", "sources": [], "grounded": False, "reason": "question vide"}
    hits = retrieve(question, documents, top_k=top_k)
    if not hits:
        return {
            "answer": "Aucun document pertinent trouvé pour cette question.",
            "sources": [],
            "grounded": False,
            "reason": "aucune source",
        }
    prompt = build_rag_prompt(question, [h.get("content", "") for h in hits])
    sources = [h.get("id") for h in hits]
    try:
        text = generator(prompt)
    except Exception:
        return {"answer": "", "sources": sources, "grounded": False, "reason": "generation KO"}
    return {"answer": str(text or "").strip(), "sources": sources, "grounded": True}
