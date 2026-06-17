"""Harnais d'évaluation qualité RAG façon **RAGAS**, *souverain* et *offline-CI-able*.

Ce paquet score la qualité des réponses d'un pipeline RAG sur un **jeu doré
français** (`golden_fr.json`) en utilisant un **LLM-juge sur l'Ollama LOCAL**
(aucun cloud, aucune dépendance lourde). Il calcule les trois métriques cœur de
la méthodologie RAGAS :

* **faithfulness** — proportion d'affirmations de la réponse étayées par le
  contexte récupéré (la réponse n'« hallucine » pas hors des sources) ;
* **context_precision** — proportion de chunks de contexte récupérés qui sont
  réellement pertinents pour répondre à la question (qualité du retrieval) ;
* **answer_relevancy** — à quel point la réponse adresse directement la question
  posée (pas de hors-sujet, pas d'évasion).

Conception :

* le **juge** (`judge.py`) accepte un **callable injectable** ``llm(system, user)
  -> str`` → mockable en test, **sans Ollama** ; le défaut est un mince wrapper
  sur ``live_harness.chat`` (réutilise le client stdlib OpenAI-compatible) ;
* le **runner** (`runner.py`) calcule les métriques par item + agrégées, applique
  un **gate** (seuils surchargables par env) et **sort en code non nul** si le
  gate échoue ;
* un **adaptateur optionnel** vers la vraie librairie ``ragas`` est proposé
  (`--backend ragas`) et **dégrade proprement** si elle n'est pas installée.

Les modules de `tests/rag/` s'importent en **nom plat** (ex. ``import
live_harness``). Ce paquet ajoute donc le dossier parent ``tests/rag`` au
``sys.path`` au besoin (cf. `runner.py` et `judge.py`).
"""
from __future__ import annotations

__all__ = ["judge", "runner", "metrics"]
