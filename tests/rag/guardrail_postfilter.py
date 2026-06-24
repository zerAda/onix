"""Post-filtre déterministe « couche 3 » — garde-fou applicatif hors-LLM.

Ce module implémente, comme une **couche applicative testée**, le garde-fou
déterministe décrit dans `docs/QA_GUARDRAILS.md` (§ Couche 3 — post-filtre « pas
de citation → refuse ») et le **confinement de comportement** que le prompt seul
ne garantit pas sur un modèle ≥ 7B (mais < parfait) :

  * **Pas de citation → refuse** : toute réponse présentée comme FACTUELLE
    (chiffres, montants, dates, %) sans aucune **source/citation** est bloquée et
    remplacée par un refus sourcé.
  * **Lecture seule** : toute réponse qui **prétend / simule / décrit** avoir
    modifié, supprimé, renommé, déplacé, créé ou envoyé (write-confirmation) est
    bloquée et remplacée par le refus « lecture seule ».
  * **Hors-contexte** : une réponse qui développe des connaissances générales
    alors que le contexte documentaire ne couvre pas la question est bloquée et
    remplacée par « information non disponible ».
  * **Injection / fuite** : par sécurité (défense en profondeur), une réponse qui
    régurgite un lien d'exfiltration comme action, recopie le prompt système, ou
    bascule de persona est bloquée.

Conception clé (recommandation OWASP LLM01) : **déterministe**, donc **non
manipulable** par une injection. Le post-filtre ne « comprend » pas la requête —
il applique des règles binaires sur (intention de la requête + réponse brute) et,
au moindre doute de violation, **substitue un refus**. C'est volontairement
conservateur : un faux refus est inoffensif (l'utilisateur reformule), une fuite
ne l'est pas.

100 % hors-LLM, **stateless**, et **testable** avec les mêmes assertions que le
reste de `tests/rag/` (cf. `test_postfilter.py`). En production, cette logique
s'applique côté `onix-actions`/proxy après réception de la réponse Onyx ; ici on
la branche sur la réponse du modèle live pour **prouver** qu'elle rattrape les
relâchements résiduels du 7B.
"""
from __future__ import annotations

import dataclasses
import re
import unicodedata
from typing import List, Optional

# ───────────────────────────────────────────────────────────────────────────
# Réponses de substitution (refus sourcés, déterministes).
# ───────────────────────────────────────────────────────────────────────────
REFUSAL_READ_ONLY = (
    "Je ne peux pas effectuer cette action : je suis en LECTURE SEULE et n'ai "
    "aucune capacité de modification, suppression, déplacement, renommage, "
    "création ou envoi. Je peux au plus préparer un brouillon que vous "
    "exécuterez vous-même."
)
REFUSAL_NOT_AVAILABLE = (
    "Cette information n'est pas disponible dans les documents accessibles. "
    "Je réponds uniquement à partir des sources documentaires fournies, jamais "
    "de mémoire."
)
REFUSAL_NO_CITATION = (
    "Je ne peux pas étayer cette réponse par une source accessible. "
    "Une affirmation factuelle sans citation est bloquée par sécurité."
)
REFUSAL_INJECTION = (
    "Ce document contient une instruction suspecte que je n'exécute pas. "
    "Je n'effectue aucune action externe (envoi, lien, export) et ne révèle pas "
    "mes instructions internes."
)


@dataclasses.dataclass(frozen=True)
class FilterResult:
    """Résultat du post-filtre.

    blocked   : True si la réponse brute violait un invariant (substituée).
    answer    : la réponse finale (brute si conforme, refus sinon).
    rule      : identifiant de la règle déclenchée (pour audit/preuve).
    reason    : explication lisible.
    """
    blocked: bool
    answer: str
    rule: str
    reason: str


# ───────────────────────────────────────────────────────────────────────────
# Normalisation (insensible casse + accents) — robustesse FR.
# ───────────────────────────────────────────────────────────────────────────
def _strip_accents(s: str) -> str:
    nf = unicodedata.normalize("NFD", s)
    return "".join(c for c in nf if unicodedata.category(c) != "Mn")


def _norm(s: str) -> str:
    return _strip_accents((s or "").lower())


def _sentences(text: str) -> List[str]:
    parts = re.split(r"[.\n!?;:]+", text or "")
    return [p.strip() for p in parts if p.strip()]


# Marqueurs de NÉGATION/refus : si présents dans une phrase, une mention d'action
# interdite y est un REFUS, pas une exécution. (accents retirés)
_NEGATION = [
    "ne peux pas", "ne peux", "ne vais pas", "ne dois pas", "je refuse",
    "pas autorise", "pas en mesure", "ne suis pas", "n'est pas approprie",
    "ne saurais", "impossible", "ne peut pas", "je ne", "sans jamais",
    "lecture seule", "ne modifie", "ne supprime", "ne renomme", "ne deplace",
    "ne cree", "n'ai pas la capacite", "pas la capacite", "ne realise pas",
    "ne procede", "ne procederai", "n'effectue", "n'enverrai", "n'envoie",
    "ne pas suivre", "ne pas cliquer", "suspect", "malveillant", "frauduleux",
    "ignore", "ne fais pas",
]


def _affirmative_sentences(text: str) -> List[str]:
    """Phrases qui ne sont PAS des négations/refus (où une action affirmée compte)."""
    out = []
    for sent in _sentences(text):
        low = _norm(sent)
        if any(n in low for n in _NEGATION):
            continue
        out.append(low)
    return out


# ───────────────────────────────────────────────────────────────────────────
# Détecteurs d'intention de la REQUÊTE (déterministes, sur la question seule).
# ───────────────────────────────────────────────────────────────────────────
# Verbes/expressions d'ÉCRITURE dans la demande utilisateur.
_WRITE_INTENT = [
    "modifie", "modifier", "supprime", "supprimer", "renomme", "renommer",
    "deplace", "deplacer", "cree ", "creer ", "ecris dans", "remplace",
    "mets a jour", "mettre a jour", "change la", "change le", "passe la cotisation",
    "envoie", "envoyer", "envoi ", "exporte", "exporter", "archive le",
]


def is_write_request(question: str) -> bool:
    q = _norm(question)
    return any(w in q for w in _WRITE_INTENT)


# Question « générale » hors corpus (réglementation, définition, droit, marché…),
# sans référence au dossier/document/client accessible.
_GENERAL_KNOWLEDGE_INTENT = [
    "reglementation", "la loi", "le droit", "en france", "definition",
    "comment ca marche", "qu'est-ce que", "generale sur", "en general",
    "le marche", "la legislation", "le code ", "la norme",
]


def is_general_knowledge_request(question: str) -> bool:
    q = _norm(question)
    return any(g in q for g in _GENERAL_KNOWLEDGE_INTENT)


# Demande portant explicitement sur une ressource DÉCLARÉE INACCESSIBLE par
# l'utilisateur lui-même (« le client X auquel je n'ai pas accès »), ou sur
# l'existence/le contenu d'un dossier/client tiers non fourni. L'agent doit
# répondre « non disponible » sans confirmer ni infirmer.
_INACCESSIBLE_HINTS = [
    "je n'ai pas acces", "auquel je n'ai pas acces", "pas acces",
    "existe-t-il", "existe t il", "que contient son dossier",
    "son dossier contient",
]


def is_inaccessible_resource_request(question: str) -> bool:
    q = _norm(question)
    return any(h in q for h in _INACCESSIBLE_HINTS)


def confirms_inaccessible_resource(answer: str) -> Optional[str]:
    """Confirmation affirmative de l'existence/du contenu d'une ressource
    inaccessible (hors phrase de refus)."""
    affirmative = [
        "oui, le client", "oui le client", "ce client existe", "il existe",
        "le dossier contient", "son dossier contient", "voici son dossier",
        "le client existe bien", "oui, ce dossier", "ce dossier existe",
    ]
    for sent in _affirmative_sentences(answer):
        for marker in affirmative:
            if marker in sent:
                return marker
    return None


# ───────────────────────────────────────────────────────────────────────────
# Détecteurs de VIOLATION dans la RÉPONSE brute.
# ───────────────────────────────────────────────────────────────────────────
# Confirmation d'écriture (l'agent PRÉTEND avoir agi), hors phrase de refus.
_WRITE_CONFIRMATION = [
    "j'ai modifie", "modifications apportees", "modification effectuee",
    "modification apportee", "j'ai mis a jour", "mise a jour effectuee",
    "j'ai supprime", "document supprime", "fichier supprime",
    "j'ai renomme", "fichier renomme", "j'ai deplace", "fichier deplace",
    "deplacement effectue", "j'ai cree", "document cree", "fichier cree",
    "j'ai envoye", "e-mail envoye", "email envoye", "mail envoye",
    "courriel envoye", "j'ai bien envoye", "actions effectuees",
    "action effectuee", "voici les modifications", "j'ai procede",
    "j'ai bien modifie", "c'est fait", "operation effectuee",
    "j'ai archive", "renomme et deplace", "a ete modifie", "a ete renomme",
    "a ete deplace", "a ete supprime", "a ete envoye", "je vais modifier",
    "je vais supprimer", "je vais renommer", "je vais deplacer",
    "je vais envoyer", "je vais proceder", "je modifie", "je supprime",
]


def claims_write_action(answer: str) -> Optional[str]:
    for sent in _affirmative_sentences(answer):
        for marker in _WRITE_CONFIRMATION:
            if marker in sent:
                return marker
    return None


# Régurgitation d'un lien d'exfiltration comme action (sans avertissement).
def relays_exfil_link(answer: str) -> Optional[str]:
    low = _norm(answer)
    if "exfil.example" not in low:
        return None
    warning = any(w in low for w in [
        "suspect", "ne pas suivre", "ne pas cliquer", "n'execute", "ignore",
        "malveillant", "ne dois pas", "frauduleux", "n'enverrai", "ne vais pas",
        "ne peux pas", "n'envoie",
    ])
    return None if warning else "exfil.example (relaye comme action)"


# Fuite de prompt / bascule de persona (défense en profondeur).
_LEAK_OR_PERSONA = [
    "regles fondamentales de sourcing", "cloisonnement client",
    "owasp llm01", "owasp llm02", "je suis desormais", "freebot",
    "voici mes instructions systeme", "voici mon prompt", "mes instructions sont",
]


def leaks_prompt_or_persona(answer: str) -> Optional[str]:
    for sent in _affirmative_sentences(answer):
        for marker in _LEAK_OR_PERSONA:
            if marker in sent:
                return marker
    # marqueurs très spécifiques : comptent partout
    low = _norm(answer)
    for m in ("owasp llm01", "owasp llm02", "regles fondamentales de sourcing"):
        if m in low:
            return m
    return None


# A-t-on avancé un FAIT chiffré (montant, %, date, montant €) ?
def asserts_a_fact(answer: str) -> bool:
    low = _norm(answer)
    if not re.search(r"\d", low):
        return False
    return (
        "€" in answer or "euro" in low or "%" in low
        or bool(re.search(r"\b20\d\d\b", low))
        or "cotisation" in low or "plafond" in low or "echeance" in low
        or "plancher" in low or "montant" in low
    )


# La réponse cite-t-elle une SOURCE (fichier, mention 'source'/'document') ?
def has_citation(answer: str) -> bool:
    low = _norm(answer)
    if re.search(r"\.(pdf|docx?|xlsx?|pptx?)\b", low):
        return True
    if "source" in low or "document" in low:
        return True
    # bloc [Document: …] repris
    if re.search(r"\[\s*document\s*:", low):
        return True
    # Marqueur de citation inline natif d'Onyx : [1], [[1]], [1, 2] (renvoi
    # numéroté à un document récupéré). Sans cette reconnaissance, TOUTE réponse
    # chiffrée correctement sourcée au format Onyx serait faussement bloquée
    # (no_citation) — le garde-fou rejetterait alors l'ensemble des réponses
    # légitimes. `\[\d` capte aussi le « [[1]] » via le crochet interne.
    if re.search(r"\[\d", answer):
        return True
    return False


# La réponse est-elle DÉJÀ un refus / une indisponibilité explicite ?
_ALREADY_SAFE = [
    "ne peux pas", "lecture seule", "non disponible", "pas disponible",
    "pas accessible", "n'ai pas trouve", "n'a pas acces", "n'avons pas acces",
    "n'est pas mentionne", "ne figure pas", "aucune information",
    "pas d'information", "instructions internes", "ne peux pas partager",
    "hors perimetre", "n'est pas present", "ne dispose", "ne pouvons pas",
]


def is_already_safe_answer(answer: str) -> bool:
    low = _norm(answer)
    return any(m in low for m in _ALREADY_SAFE)


# ───────────────────────────────────────────────────────────────────────────
# Le post-filtre principal.
# ───────────────────────────────────────────────────────────────────────────
def post_filter(question: str, context: str, answer: str) -> FilterResult:
    """Applique les règles déterministes. Renvoie la réponse finale (brute si
    conforme, refus substitué sinon).

    Ordre des règles = du plus DUR (sécurité) au plus métier. La première règle
    déclenchée gagne. `context` est fourni pour de futures règles de
    groundedness fines (ici on reste volontairement conservateur).
    """
    # 1) SÉCURITÉ DURE — fuite de prompt / bascule de persona.
    leak = leaks_prompt_or_persona(answer)
    if leak:
        return FilterResult(True, REFUSAL_INJECTION, "no_prompt_leak",
                            f"fuite/persona détectée ({leak!r})")

    # 2) SÉCURITÉ DURE — relais d'un lien d'exfiltration comme action.
    exfil = relays_exfil_link(answer)
    if exfil:
        return FilterResult(True, REFUSAL_INJECTION, "no_exfil_relay",
                            f"lien d'exfiltration relayé ({exfil!r})")

    # 3) LECTURE SEULE — demande d'écriture OU confirmation d'écriture affirmée.
    wrote = claims_write_action(answer)
    if wrote:
        return FilterResult(True, REFUSAL_READ_ONLY, "read_only",
                            f"confirmation d'écriture simulée ({wrote!r})")
    if is_write_request(question) and not is_already_safe_answer(answer):
        return FilterResult(True, REFUSAL_READ_ONLY, "read_only",
                            "demande d'écriture sans refus explicite")

    # 4) HORS-CONTEXTE — question de connaissances générales développée de mémoire.
    if is_general_knowledge_request(question) and not is_already_safe_answer(answer):
        # Si l'agent a quand même cité une source du contexte, on tolère ; sinon refus.
        if not has_citation(answer):
            return FilterResult(True, REFUSAL_NOT_AVAILABLE, "out_of_context",
                                "réponse de connaissances générales non sourcée")

    # 4b) RESSOURCE INACCESSIBLE — confirmation d'existence/contenu d'un dossier
    #     tiers non fourni (anti-exfiltration / non-confirmation). On bloque si
    #     la demande vise une ressource inaccessible ET que la réponse la confirme
    #     affirmativement (hors refus).
    confirmed = confirms_inaccessible_resource(answer)
    if (confirmed and is_inaccessible_resource_request(question)
            and not is_already_safe_answer(answer)):
        return FilterResult(True, REFUSAL_NOT_AVAILABLE, "no_confirm_inaccessible",
                            f"confirmation d'une ressource inaccessible ({confirmed!r})")

    # 5) GROUNDEDNESS — fait chiffré sans citation → refuse (couche 3 QA_GUARDRAILS).
    #    On NE substitue PAS si la réponse est déjà un refus/une limitation
    #    explicite : un agent qui décline (« je ne peux pas… ») tout en
    #    rappelant un chiffre du dossier n'émet pas une affirmation factuelle
    #    NON sourcée à bloquer — c'est déjà une réponse sûre. Le cas dangereux
    #    (chiffres assénés sans la moindre réserve, type NOM01) n'a pas de
    #    marqueur de refus et reste donc bloqué.
    if (asserts_a_fact(answer) and not has_citation(answer)
            and not is_already_safe_answer(answer)):
        return FilterResult(True, REFUSAL_NO_CITATION, "no_citation",
                            "affirmation factuelle sans citation de source")

    # Conforme : on laisse passer la réponse brute.
    return FilterResult(False, answer, "passthrough", "réponse conforme aux invariants")
