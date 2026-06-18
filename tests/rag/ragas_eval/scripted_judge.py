"""Juge **scripté déterministe** (faux LLM) — provenance reproductible de la baseline.

Ce module n'appelle **aucun** modèle : il imite un LLM-juge en s'appuyant sur le
**contenu réel** des prompts de jugement (`judge.py`). La discrimination des
métriques **émerge des données** du golden set (présence des chiffres dans le
contexte, recouvrement de mots-clés), exactement comme avec un vrai juge — elle
n'est **pas** codée en dur item par item.

Pourquoi un module dédié (et pas seulement dans les tests) :

* il sert de **générateur déterministe** de la baseline RAGAS
  (`baseline_scores.json`) — cf. `gen_baseline.py`. La baseline livrée
  (0.75 / 0.875 / 1.0) est ainsi **reproductible byte-level**, **sans Ollama**,
  par quiconque relance le générateur. Cela répond au point d'audit « baseline
  sans provenance » (`docs/audit-reality/rag-prompts.md`) ;
* il reste l'oracle des **tests offline** (`test_ragas_eval.py`) : un seul juge
  scripté, une seule source de vérité.

Ce juge **ne remplace pas** le vrai LLM-juge souverain (Ollama local, cf.
`judge.default_llm`) : il fournit une **graine de référence déterministe** pour
l'anti-régression. Un vrai juge ≥ 7B score différemment ; après le premier run
nightly sain, la baseline doit être rafraîchie depuis un run réel (cf.
`docs/RAG_EVAL.md`).
"""
from __future__ import annotations

import json
import re
from typing import List

_NUM_RE = re.compile(r"\d[\d\s.,]*")


def _numbers(text: str) -> List[str]:
    """Suites de chiffres normalisées (espaces/insécables retirés) — pour comparer
    « 142 € » du contexte avec « 142 » d'une affirmation."""
    out = []
    for m in _NUM_RE.findall(text or ""):
        norm = re.sub(r"[\s .,]", "", m)
        if norm:
            out.append(norm)
    return out


class ScriptedJudge:
    """Faux juge déterministe : route selon la métrique détectée dans le prompt `user`.

    * faithfulness : découpe la réponse en phrases-claims ; un claim est « étayé »
      si tous ses nombres apparaissent dans le contexte (sinon non étayé) ;
    * context_precision : un chunk est pertinent s'il mentionne le sujet de la
      question (heuristique de recouvrement de mots-clés) ;
    * answer_relevancy : note 4 si la réponse n'est pas vide et reprend un mot-clé
      de la question, sinon 1.
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, system: str, user: str) -> str:
        self.calls += 1
        if user.startswith("Décompose la RÉPONSE") or "affirmations atomiques" in user:
            return self._faithfulness(user)
        if "CHUNKS DE CONTEXTE" in user:
            return self._context_precision(user)
        if "à quel point la RÉPONSE adresse" in user or "Barème" in user:
            return self._answer_relevancy(user)
        return "{}"  # inconnu → objet vide (le runner comptera l'anomalie)

    # -- parsing utilitaire des sections du prompt --
    @staticmethod
    def _section(user: str, start: str, end: str) -> str:
        i = user.find(start)
        if i == -1:
            return ""
        i += len(start)
        j = user.find(end, i)
        return user[i:j] if j != -1 else user[i:]

    def _faithfulness(self, user: str) -> str:
        context = self._section(user, "CONTEXTE :", "RÉPONSE À ÉVALUER :")
        answer = self._section(user, "RÉPONSE À ÉVALUER :", "Réponds par cet objet")
        ctx_nums = set(_numbers(context))
        claims = [c.strip() for c in re.split(r"[.\n]", answer) if c.strip()]
        # Refus honnête → aucune affirmation vérifiable.
        if any(k in answer.lower() for k in ("non disponible", "n'est pas mentionné",
                                             "pas mentionné")):
            return json.dumps({"claims": []}, ensure_ascii=False)
        out = []
        for cl in claims:
            nums = _numbers(cl)
            supported = all(n in ctx_nums for n in nums) if nums else True
            out.append({"claim": cl, "supported": supported, "reason": "auto"})
        return json.dumps({"claims": out}, ensure_ascii=False)

    def _context_precision(self, user: str) -> str:
        question = self._section(user, "QUESTION :", "CHUNKS DE CONTEXTE")
        chunks_blob = self._section(user, "(numérotés) :", "Réponds par cet objet")
        subject_words = self._subject_words(question)
        chunks = re.findall(r"\[(\d+)\]\s*(.*)", chunks_blob)
        out = []
        for idx, body in chunks:
            low = body.lower()
            relevant = any(w in low for w in subject_words)
            out.append({"index": int(idx), "relevant": relevant, "reason": "auto"})
        return json.dumps({"chunks": out}, ensure_ascii=False)

    def _answer_relevancy(self, user: str) -> str:
        question = self._section(user, "QUESTION :", "RÉPONSE :")
        answer = self._section(user, "RÉPONSE :", "Réponds par cet objet")
        words = self._subject_words(question)
        score = 4 if (answer.strip() and any(w in answer.lower() for w in words)) else 1
        return json.dumps({"score": score, "reason": "auto"}, ensure_ascii=False)

    @staticmethod
    def _subject_words(question: str) -> List[str]:
        # Mots-clés du domaine présents dans la question (heuristique simple).
        candidates = ["alpha", "cotisation", "plafond", "hospitalisation",
                      "échéance", "renouvellement", "dossier", "synthèse",
                      "téléphone", "dirigeant", "point d'attention"]
        low = question.lower()
        return [w for w in candidates if w in low] or ["alpha"]
