"""LLM-juge **souverain** (Ollama LOCAL) pour les métriques RAGAS, en français.

Ce module ne calcule **pas** les agrégats (cf. `metrics.py`) : il s'occupe des
**prompts de jugement** et de la **décomposition** d'une réponse/d'un contexte en
verdicts atomiques via le LLM, puis renvoie ces verdicts sous forme structurée.

Principe directeur : **robustesse**. Un LLM 7B local renvoie souvent du JSON
entouré de texte, de *code fences* ```json …```, ou légèrement mal formé. On
**extrait** l'objet JSON de façon tolérante (`extract_json`) et, en dernier
recours, on **dégrade** vers une heuristique de secours documentée plutôt que de
crasher le runner. Chaque réponse LLM illisible est **comptée** (`errors`) et
**remontée**, jamais avalée silencieusement.

Le juge accepte un **callable injectable** ::

    llm(system: str, user: str) -> str

Le défaut (`default_llm`) est un mince wrapper sur ``live_harness.chat`` (client
stdlib OpenAI-compatible d'Ollama, température 0 pour le déterminisme). En test,
on injecte un faux juge scripté → **aucun réseau, aucun Ollama**.

Définitions des métriques (implémentées ici + agrégées dans `metrics.py`) :

* **faithfulness** ∈ [0,1] : on décompose la réponse à noter en **affirmations
  atomiques** (claims), puis on juge chacune *étayée* / *non étayée* par le
  contexte récupéré. Score = (#étayées) / (#claims). Une réponse sans aucune
  affirmation vérifiable (refus honnête « non disponible ») vaut **1.0** (rien
  d'hallucinable) — choix documenté, cohérent avec un assistant prudent.
* **context_precision** ∈ [0,1] : on juge chaque **chunk** de contexte récupéré
  *pertinent* / *non pertinent* pour répondre à la question. Score = (#pertinents)
  / (#chunks). Sans chunk, score = 0.0 (aucun signal de retrieval utile).
* **answer_relevancy** ∈ [0,1] : on juge **directement** (note 0–4 ramenée à
  [0,1]) à quel point la réponse adresse la question, indépendamment de sa
  véracité. Choix « note directe » plutôt que « similarité de questions
  régénérées » : un seul appel LLM, pas d'embeddings → plus léger et déterministe
  sur petit modèle local. Compromis documenté dans le README.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

# ── tests/rag importable en nom plat (comme run_live.py) ───────────────────
_HERE = Path(__file__).resolve().parent
_RAG_DIR = _HERE.parent
if str(_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(_RAG_DIR))


# Type du callable injectable : (system, user) -> texte brut du modèle.
LLM = Callable[[str, str], str]


def default_llm(system: str, user: str) -> str:
    """Juge par défaut = ``live_harness.chat`` à température 0 (déterminisme).

    Import **paresseux** : on n'importe `live_harness` (et donc on ne touche au
    réseau/Ollama) que lorsque le juge réel est effectivement appelé. Les tests
    qui injectent un faux juge n'exécutent jamais ce chemin.
    """
    import live_harness as lh  # import paresseux, hors chemin de test

    return lh.chat(system, user, temperature=0.0)


# ───────────────────────────────────────────────────────────────────────────
# Extraction JSON robuste (tolère fences / texte parasite / objet imbriqué).
# ───────────────────────────────────────────────────────────────────────────
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Optional[dict]:
    """Extrait le **premier objet JSON** d'une sortie LLM bruitée, ou ``None``.

    Stratégie en cascade (de la plus fiable à la plus permissive) :

    1. parse direct (`json.loads`) — cas idéal ;
    2. contenu d'un *code fence* ```json … ``` ;
    3. balayage du texte pour le **premier objet** ``{ … }`` équilibré (gère les
       accolades imbriquées et les accolades à l'intérieur de chaînes).

    Ne lève jamais : renvoie ``None`` si rien d'exploitable n'est trouvé.
    """
    if not text or not text.strip():
        return None

    raw = text.strip()

    # 1) Parse direct.
    obj = _try_load_dict(raw)
    if obj is not None:
        return obj

    # 2) Contenu d'un code fence.
    for m in _FENCE_RE.finditer(raw):
        obj = _try_load_dict(m.group(1).strip())
        if obj is not None:
            return obj

    # 3) Premier objet { … } équilibré dans le texte.
    candidate = _first_balanced_object(raw)
    if candidate is not None:
        obj = _try_load_dict(candidate)
        if obj is not None:
            return obj

    return None


def _try_load_dict(s: str) -> Optional[dict]:
    try:
        val = json.loads(s)
    except (ValueError, TypeError):
        return None
    return val if isinstance(val, dict) else None


def _first_balanced_object(s: str) -> Optional[str]:
    """Renvoie la sous-chaîne du premier objet ``{ … }`` à accolades équilibrées,
    en ignorant les accolades situées dans des chaînes JSON (avec échappements)."""
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
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
                return s[start:i + 1]
    return None


def _as_bool(value: object) -> Optional[bool]:
    """Coercition tolérante d'un verdict en booléen (le LLM varie : true/'oui'/1…)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value >= 1
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "oui", "yes", "1", "étayée", "etayee", "pertinent",
                 "pertinente", "supported", "relevant"}:
            return True
        if v in {"false", "non", "no", "0", "non étayée", "non etayee",
                 "non pertinent", "non pertinente", "unsupported", "irrelevant"}:
            return False
    return None


# ───────────────────────────────────────────────────────────────────────────
# Structures de verdicts (consommées par metrics.py).
# ───────────────────────────────────────────────────────────────────────────
@dataclass
class ClaimVerdict:
    """Verdict atomique de fidélité pour UNE affirmation de la réponse."""
    claim: str
    supported: bool
    reason: str = ""


@dataclass
class ChunkVerdict:
    """Verdict de pertinence pour UN chunk de contexte récupéré."""
    index: int
    relevant: bool
    reason: str = ""


@dataclass
class ItemJudgement:
    """Résultat brut du jugement LLM d'un item (avant agrégation en scores)."""
    claims: List[ClaimVerdict] = field(default_factory=list)
    chunks: List[ChunkVerdict] = field(default_factory=list)
    relevancy_score_0_4: Optional[int] = None  # note brute 0–4 (avant /4)
    errors: List[str] = field(default_factory=list)  # réponses LLM illisibles


# ───────────────────────────────────────────────────────────────────────────
# PROMPTS DE JUGEMENT (français, stricts, JSON-only).
# ───────────────────────────────────────────────────────────────────────────
_SYS_JUDGE = (
    "Tu es un évaluateur RAG rigoureux et neutre. Tu réponds UNIQUEMENT par un "
    "objet JSON valide, sans aucun texte autour, sans bloc de code, sans "
    "explication hors du JSON. Tu te bases STRICTEMENT sur ce qui t'est fourni."
)

_FAITHFULNESS_USER = """\
Décompose la RÉPONSE en affirmations atomiques (chaque fait vérifiable = une \
affirmation), puis indique pour CHACUNE si elle est ÉTAYÉE par le CONTEXTE \
fourni (et uniquement lui). Une affirmation est étayée si le contexte permet de \
la conclure ; sinon elle est non étayée (y compris si elle est plausible mais \
absente du contexte).

CONTEXTE :
{context}

RÉPONSE À ÉVALUER :
{answer}

Réponds par cet objet JSON EXACTEMENT (rien d'autre) :
{{"claims": [{{"claim": "<texte de l'affirmation>", "supported": true|false, \
"reason": "<justification courte>"}}]}}
Si la réponse ne contient aucune affirmation factuelle vérifiable (ex. un refus \
ou « information non disponible »), renvoie une liste vide : {{"claims": []}}.
"""

_CONTEXT_PRECISION_USER = """\
Pour CHAQUE chunk de contexte numéroté, indique s'il est PERTINENT pour répondre \
à la QUESTION (utile, sur le sujet) ou NON PERTINENT (hors-sujet, inutile pour \
cette question précise).

QUESTION :
{question}

CHUNKS DE CONTEXTE (numérotés) :
{chunks}

Réponds par cet objet JSON EXACTEMENT (rien d'autre) :
{{"chunks": [{{"index": <entier à partir de 0>, "relevant": true|false, \
"reason": "<justification courte>"}}]}}
Inclus un objet par chunk, dans l'ordre.
"""

_ANSWER_RELEVANCY_USER = """\
Évalue à quel point la RÉPONSE adresse DIRECTEMENT la QUESTION posée, \
INDÉPENDAMMENT de sa véracité (tu ne juges PAS si c'est vrai, seulement si ça \
répond à la question). Barème :
- 0 : totalement hors-sujet ou évasif ;
- 1 : effleure le sujet mais ne répond pas ;
- 2 : réponse partielle / indirecte ;
- 3 : répond largement à la question ;
- 4 : répond pleinement et directement à la question.

QUESTION :
{question}

RÉPONSE :
{answer}

Réponds par cet objet JSON EXACTEMENT (rien d'autre) :
{{"score": <entier de 0 à 4>, "reason": "<justification courte>"}}
"""


def _numbered_chunks(contexts: List[str]) -> str:
    return "\n".join(f"[{i}] {c}" for i, c in enumerate(contexts))


# ───────────────────────────────────────────────────────────────────────────
# Appels de jugement (un par métrique). Robustes : jamais d'exception propagée.
# ───────────────────────────────────────────────────────────────────────────
def judge_faithfulness(answer: str, contexts: List[str], llm: LLM,
                       errors: List[str]) -> List[ClaimVerdict]:
    """Décompose `answer` en claims et juge chacun étayé/non par `contexts`."""
    context_blob = "\n\n".join(contexts) if contexts else "(aucun contexte fourni)"
    user = _FAITHFULNESS_USER.format(context=context_blob, answer=answer)
    data = _call_and_parse(llm, _SYS_JUDGE, user, errors, where="faithfulness")
    if data is None:
        return []
    out: List[ClaimVerdict] = []
    for c in data.get("claims", []) or []:
        if not isinstance(c, dict):
            continue
        supported = _as_bool(c.get("supported"))
        if supported is None:
            # Verdict illisible sur un claim : on le compte comme NON étayé
            # (conservateur : un doute de fidélité pèse contre la réponse).
            supported = False
            errors.append("faithfulness: verdict de claim illisible (compté non étayé)")
        out.append(ClaimVerdict(
            claim=str(c.get("claim", "")).strip(),
            supported=supported,
            reason=str(c.get("reason", "")).strip(),
        ))
    return out


def judge_context_precision(question: str, contexts: List[str], llm: LLM,
                            errors: List[str]) -> List[ChunkVerdict]:
    """Juge la pertinence de chaque chunk de `contexts` vis-à-vis de `question`."""
    if not contexts:
        return []
    user = _CONTEXT_PRECISION_USER.format(
        question=question, chunks=_numbered_chunks(contexts))
    data = _call_and_parse(llm, _SYS_JUDGE, user, errors, where="context_precision")
    if data is None:
        return []
    out: List[ChunkVerdict] = []
    by_index = {}
    for ch in data.get("chunks", []) or []:
        if not isinstance(ch, dict):
            continue
        try:
            idx = int(ch.get("index"))
        except (TypeError, ValueError):
            continue
        relevant = _as_bool(ch.get("relevant"))
        if relevant is None:
            relevant = False
            errors.append("context_precision: verdict de chunk illisible (compté non pertinent)")
        by_index[idx] = ChunkVerdict(
            index=idx, relevant=relevant,
            reason=str(ch.get("reason", "")).strip())
    # Garantit un verdict par chunk attendu (manquant → non pertinent, signalé).
    for i in range(len(contexts)):
        if i in by_index:
            out.append(by_index[i])
        else:
            errors.append(f"context_precision: chunk #{i} non jugé (compté non pertinent)")
            out.append(ChunkVerdict(index=i, relevant=False,
                                    reason="non jugé par le LLM"))
    return out


def judge_answer_relevancy(question: str, answer: str, llm: LLM,
                           errors: List[str]) -> Optional[int]:
    """Note 0–4 (brute) de la pertinence de `answer` vis-à-vis de `question`."""
    user = _ANSWER_RELEVANCY_USER.format(question=question, answer=answer)
    data = _call_and_parse(llm, _SYS_JUDGE, user, errors, where="answer_relevancy")
    if data is None:
        return None
    raw = data.get("score")
    try:
        score = int(raw)
    except (TypeError, ValueError):
        errors.append(f"answer_relevancy: score illisible ({raw!r})")
        return None
    # Borne dans [0,4] (un LLM peut déborder).
    return max(0, min(4, score))


def _call_and_parse(llm: LLM, system: str, user: str, errors: List[str],
                    *, where: str) -> Optional[dict]:
    """Appelle le juge, extrait le JSON ; toute erreur est **comptée**, jamais levée."""
    try:
        raw = llm(system, user)
    except Exception as e:  # réseau / modèle absent / juge en panne
        errors.append(f"{where}: appel LLM échoué — {type(e).__name__}: {e}")
        return None
    data = extract_json(raw)
    if data is None:
        snippet = (raw or "")[:120].replace("\n", " ")
        errors.append(f"{where}: JSON introuvable dans la réponse LLM ({snippet!r})")
        return None
    return data


def judge_item(question: str, answer: str, contexts: List[str],
               llm: LLM) -> ItemJudgement:
    """Juge un item complet (3 métriques) et renvoie les verdicts bruts.

    Ne lève jamais : un appel LLM raté sur une métrique laisse les autres
    s'exécuter ; les erreurs sont accumulées dans ``ItemJudgement.errors``.
    """
    errors: List[str] = []
    claims = judge_faithfulness(answer, contexts, llm, errors)
    chunks = judge_context_precision(question, contexts, llm, errors)
    relevancy = judge_answer_relevancy(question, answer, llm, errors)
    return ItemJudgement(claims=claims, chunks=chunks,
                         relevancy_score_0_4=relevancy, errors=errors)
