"""safe_logger — Redaction PII + journalisation durcie (onix-actions).

Porte la logique d'AC360 `scripts/safe_logger.py` (redaction des données
personnelles + anti-injection de logs) et la généricise pour le microservice
FastAPI. Deux usages :

  1. `redact(value)` : neutralise dans une chaîne (ou structure imbriquée) les
     motifs sensibles — JWT, IBAN, NIR (sécu. sociale FR), e-mails, clés API /
     Bearer, numéros de carte — AVANT toute journalisation OU persistance d'un
     champ libre. C'est la porte unique exigée par WS2.

  2. `install(logger_name="onix.actions")` : pose un `logging.Filter` qui passe
     CHAQUE enregistrement de log (`record.getMessage()` + args) dans `redact`,
     de sorte qu'AUCUN log `onix.actions.*` ne puisse fuiter une donnée
     personnelle, même par négligence d'un appel `logger.info(...)`.

Durcissements (OWASP ASVS V7 — Logging) :
  * **anti-CRLF / log forging** : les retours chariot (CR/LF) et caractères de
    contrôle d'une valeur loggée sont échappés → un attaquant ne peut pas
    injecter de fausses lignes de log (CWE-117) ;
  * **fail-safe** : la redaction ne lève JAMAIS ; en cas d'erreur inattendue,
    elle renvoie un marqueur neutre plutôt que la donnée brute ;
  * **irréversible** : on ne « masque » pas en gardant la donnée — on la
    remplace par une étiquette de catégorie (`[REDACTED_EMAIL]`, …). Pour les
    secrets de session/tokens, ASVS exige l'irréversibilité : c'est le cas ici.

Aucune dépendance externe : `re` + `logging` de la stdlib uniquement.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable, List, Optional, Pattern, Tuple

# ---------------------------------------------------------------------------
# Catalogue de motifs sensibles -> étiquette de remplacement.
#
# L'ORDRE compte : on applique les motifs les plus spécifiques / englobants
# d'abord (JWT avant « mot ressemblant à du base64 », IBAN avant nombres, …)
# pour éviter qu'un motif générique ne « casse » un motif structuré.
# ---------------------------------------------------------------------------

# JWT : trois segments base64url séparés par des points (en-tête.payload.signature).
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")

# Authorization: Bearer <token> et clés type X-API-Key inline.
_BEARER = re.compile(r"(?i)\b(bearer|apikey|api[_-]?key|x-api-key|x-admin-key)\b[\s:=]+[A-Za-z0-9._\-]{8,}")

# IBAN : 2 lettres pays + 2 chiffres clé + 11..30 alphanum (groupés ou non).
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Za-z0-9]){11,30}\b")

# NIR (numéro de sécurité sociale FR) : 13 chiffres + clé 2 chiffres,
# tolérant aux espaces (1 85 12 75 116 001 25). On exige un sexe (1/2) en tête
# pour limiter les faux positifs sur de longues séquences de chiffres.
_NIR = re.compile(r"\b[12][ ]?\d{2}[ ]?\d{2}[ ]?\d{2,3}[ ]?\d{2,3}[ ]?\d{3}[ ]?\d{2}\b")

# Carte bancaire : 13 à 19 chiffres, éventuellement par blocs de 4.
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")

# E-mail (RFC-lax, suffisant pour la redaction).
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# Téléphone : VOLONTAIREMENT conservateur pour ne PAS mutiler les dates ISO
# (2024-01-01), UUID, n° de contrat ou montants présents dans les logs (qui
# nuirait à l'investigation — ASVS V7). On n'accepte donc QUE :
#   * un format E.164 explicite (préfixe « + » : +33612345678, +33 6 12 34 56 78) ;
#   * OU un national FR commençant par 0 suivi de 9 chiffres, groupés par
#     espaces/points/tirets « réguliers » (06 12 34 56 78 / 06.12.34.56.78).
# Les séparateurs « - » ne sont admis QUE pour le national à 10 chiffres (pas
# pour des blocs de 4 façon AAAA-MM-JJ).
_PHONE = re.compile(
    r"(?<![\w+])(?:"
    r"\+\d{1,3}(?:[ .]?\d){8,12}"           # E.164 : + indicatif puis 8..12 chiffres
    r"|0\d(?:[ .\-]?\d{2}){4}"               # national FR : 0X puis 4 blocs de 2
    r")(?!\d)"
)

# Ordre d'application (spécifique -> générique).
_PATTERNS: Tuple[Tuple[Pattern[str], str], ...] = (
    (_JWT, "[REDACTED_JWT]"),
    (_BEARER, "[REDACTED_SECRET]"),
    (_IBAN, "[REDACTED_IBAN]"),
    (_NIR, "[REDACTED_NIR]"),
    (_CARD, "[REDACTED_CARD]"),
    (_EMAIL, "[REDACTED_EMAIL]"),
    (_PHONE, "[REDACTED_PHONE]"),
)

# Caractères de contrôle à neutraliser (anti log-forging). On garde la
# tabulation visible mais on échappe CR/LF et tout autre caractère de contrôle.
_CTRL = re.compile(r"[\r\n\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Longueur maximale d'un champ loggé (anti log-flooding). Configurable au besoin.
MAX_LOGGED_LEN = 2000


def _escape_controls(text: str) -> str:
    """Échappe CR/LF et caractères de contrôle pour empêcher l'injection de
    fausses lignes de journal (CWE-117). `\\n` devient le littéral `\\n`."""

    def _sub(m: "re.Match[str]") -> str:
        ch = m.group(0)
        if ch == "\n":
            return "\\n"
        if ch == "\r":
            return "\\r"
        if ch == "\t":
            return "\\t"
        return "\\x{:02x}".format(ord(ch))

    return _CTRL.sub(_sub, text)


def redact_text(text: str) -> str:
    """Redacte une CHAÎNE : neutralise les motifs PII puis échappe les
    caractères de contrôle. Ne lève jamais."""
    try:
        s = str(text)
        for pattern, label in _PATTERNS:
            s = pattern.sub(label, s)
        s = _escape_controls(s)
        if len(s) > MAX_LOGGED_LEN:
            s = s[:MAX_LOGGED_LEN] + "…[TRUNCATED]"
        return s
    except Exception:
        # Fail-safe : jamais la donnée brute en cas de pépin de redaction.
        return "[REDACTION_ERROR]"


def redact(value: Any, _depth: int = 0) -> Any:
    """Redacte une valeur arbitraire (chaîne, dict, liste, tuple, scalaire).

    - chaîne          -> `redact_text` ;
    - dict            -> redacte récursivement clés conservées / valeurs ;
    - list/tuple/set  -> redacte chaque élément ;
    - autre (int…)    -> renvoyé tel quel (pas de PII dans un bool/None).

    Profondeur bornée (anti récursion pathologique). Porte d'entrée UNIQUE pour
    « assainir un champ libre avant log OU persistance » exigée par WS2.
    """
    if _depth > 6:
        return "[REDACTION_DEPTH_LIMIT]"
    try:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return redact_text(value)
        if isinstance(value, dict):
            return {k: redact(v, _depth + 1) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            seq: List[Any] = [redact(v, _depth + 1) for v in value]
            return type(value)(seq) if isinstance(value, tuple) else seq
        if isinstance(value, set):
            return {redact(v, _depth + 1) for v in value}
        return redact_text(str(value))
    except Exception:
        return "[REDACTION_ERROR]"


class RedactingFilter(logging.Filter):
    """`logging.Filter` qui redacte le message final (après formatage des args)
    de chaque enregistrement émis par le logger ciblé.

    On redacte `record.msg` ET `record.args` séparément n'est pas robuste (un
    `%s` peut couper un motif) : on calcule donc le message COMPLET via
    `record.getMessage()`, on le redacte, et on le repose comme message littéral
    sans arguments. Anti-CRLF inclus."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 (API logging)
        try:
            message = record.getMessage()
        except Exception:
            message = str(getattr(record, "msg", ""))
        record.msg = redact_text(message)
        record.args = None
        return True


def install(logger_name: str = "onix.actions") -> logging.Filter:
    """Installe le filtre de redaction sur le logger `logger_name` (et donc, par
    propagation, ses enfants `onix.actions.*`). Idempotent : ne pose pas deux
    fois le filtre. Retourne le filtre installé (utile pour les tests)."""
    logger = logging.getLogger(logger_name)
    for existing in logger.filters:
        if isinstance(existing, RedactingFilter):
            return existing
    f = RedactingFilter()
    logger.addFilter(f)
    return f
