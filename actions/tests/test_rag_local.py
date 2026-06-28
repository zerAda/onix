# -*- coding: utf-8 -*-
"""Tests du **RAG non-agentique** (`app.rag_local`) — 100 % offline (générateur injecté)."""
from __future__ import annotations

from app.rag_local import answer, build_rag_prompt, retrieve

DOCS = [
    {"id": "beta", "content": "Fiche client CLIENT BETA dossier BETA-201, cotisation 12 500 EUR par an, prevoyance collective."},
    {"id": "gamma", "content": "Fiche client CLIENT GAMMA dossier GAMMA-301, cotisation 8 900 EUR par an, sante collective."},
]


def test_retrieve_choisit_le_bon_dossier():
    assert retrieve("Quelle est la cotisation du dossier CLIENT BETA ?", DOCS)[0]["id"] == "beta"
    assert retrieve("Risque couvert pour le client GAMMA ?", DOCS)[0]["id"] == "gamma"


def test_retrieve_aucun_recouvrement_renvoie_vide():
    assert retrieve("question sans aucun rapport xyzzy", DOCS) == []
    assert retrieve("", DOCS) == []


def test_build_rag_prompt_contient_le_contexte_et_la_question():
    p = build_rag_prompt("cotisation BETA ?", ["contexte beta 12500"])
    assert "contexte beta 12500" in p and "cotisation BETA ?" in p and "CONTEXTE" in p


def test_answer_grounded_avec_generateur_injecte():
    gen = lambda prompt: "La cotisation du dossier BETA-201 est de 12 500 EUR/an."
    r = answer("Cotisation du dossier CLIENT BETA ?", DOCS, generator=gen)
    assert r["grounded"] is True
    assert r["sources"] == ["beta"]
    assert "12 500" in r["answer"]


def test_answer_failclosed_question_vide():
    r = answer("   ", DOCS, generator=lambda p: "ne doit pas etre appele")
    assert r["grounded"] is False and r["answer"] == "" and r["reason"] == "question vide"


def test_answer_failclosed_aucune_source():
    r = answer("sujet totalement etranger qwxz", DOCS, generator=lambda p: "x")
    assert r["grounded"] is False and r["sources"] == []


def test_answer_failclosed_generateur_en_erreur():
    def boom(prompt):
        raise RuntimeError("Ollama injoignable")

    r = answer("Cotisation CLIENT BETA ?", DOCS, generator=boom)
    assert r["grounded"] is False and r["reason"] == "generation KO"
    assert r["sources"] == ["beta"]  # la source reste tracée


def test_retrieve_replie_les_accents_fr():
    """Récupération robuste aux accents : une question SANS accents retrouve un document
    AVEC accents (et inversement) — fréquent en saisie/OCR de contrats français."""
    docs = [{"id": "D1", "content": "Conditions de résiliation et d'échéance du contrat santé."}]
    assert [h["id"] for h in retrieve("resiliation echeance", docs)] == ["D1"]
    # Sens inverse : question accentuée, document dé-accentué.
    docs2 = [{"id": "D2", "content": "Modalites de resiliation anticipee du contrat."}]
    assert [h["id"] for h in retrieve("résiliation anticipée", docs2)] == ["D2"]


def test_answer_grounded_malgre_accents_divergents():
    """Bout-en-bout : une question dé-accentuée reste grounded (source trouvée) malgré
    un document fortement accentué."""
    docs = [{"id": "D7", "content": "La prévoyance couvre l'incapacité et l'invalidité."}]
    r = answer("prevoyance incapacite", docs, generator=lambda p: "Réponse sourcée [1].")
    assert r["grounded"] is True and r["sources"] == ["D7"]
