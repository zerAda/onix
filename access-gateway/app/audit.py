"""audit — journalisation des DÉCISIONS D'ACCÈS de la passerelle, identité hachée.

Pourquoi un module dédié :
  Le cloisonnement FOSS (groupe → Document Set) doit être **auditable** (RGPD :
  journal d'accès ; assurance : preuve de contrôle). On journalise CHAQUE décision
  (allow / deny) avec :
    * un identifiant d'acteur **pseudonymisé** (HMAC-SHA256 de l'oid/UPN, tronqué) —
      jamais l'UPN/oid en clair (minimisation : on peut corréler les événements
      d'un même utilisateur sans réexposer son identité dans les logs/SIEM) ;
    * la source des groupes (claims | graph | cache), le nombre de groupes ;
    * la décision (allow|deny), le motif, et les Document Sets EFFECTIVEMENT
      autorisés (jamais le contenu de la requête / le message utilisateur).

Le sel HMAC vient de `GATEWAY_AUDIT_SALT` (env). Absent → un sel aléatoire par
processus est généré (les hachages restent corrélables au sein d'un run, mais pas
entre redémarrages ; pour une corrélation longue durée, fixer le sel via le coffre).

Aucune donnée métier (message, réponse) n'est jamais journalisée ici.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from functools import lru_cache
from typing import Any, Optional

_audit_logger = logging.getLogger("onix.gateway.audit")


@lru_cache(maxsize=1)
def _salt() -> bytes:
    raw = os.environ.get("GATEWAY_AUDIT_SALT", "").strip()
    if raw:
        return raw.encode("utf-8")
    # Pas de sel fixe : sel éphémère par processus (corrélation intra-run seulement).
    return secrets.token_bytes(32)


def reset_salt_cache() -> None:
    """Pour les tests : force la relecture de GATEWAY_AUDIT_SALT."""
    _salt.cache_clear()


def pseudonymize(actor: Optional[str]) -> str:
    """HMAC-SHA256(sel, actor) tronqué (16 hex). `None`/vide → 'anonymous'.

    Tronqué à 64 bits : suffisant pour corréler des événements sans constituer un
    identifiant réversible, et insensible aux collisions à l'échelle d'un tenant.
    """
    if not actor:
        return "anonymous"
    digest = hmac.new(_salt(), actor.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:16]


def log_access_decision(
    *,
    actor: Optional[str],
    decision: str,
    reason: str,
    group_source: Optional[str] = None,
    group_count: Optional[int] = None,
    authorized_sets: Optional[list[str]] = None,
    effective_sets: Optional[list[str]] = None,
    endpoint: Optional[str] = None,
) -> dict[str, Any]:
    """Émet (et renvoie, pour les tests) un enregistrement d'audit structuré.

    `decision` ∈ {"allow", "deny"}. L'acteur est TOUJOURS pseudonymisé.
    """
    record: dict[str, Any] = {
        "event": "access_decision",
        "decision": decision,
        "reason": reason,
        "actor_hash": pseudonymize(actor),
        "endpoint": endpoint,
        "group_source": group_source,
        "group_count": group_count,
        "authorized_sets": sorted(authorized_sets) if authorized_sets is not None else None,
        "effective_sets": sorted(effective_sets) if effective_sets is not None else None,
    }
    # Compacte les clés nulles pour un log lisible.
    record = {k: v for k, v in record.items() if v is not None}
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    if decision == "deny":
        _audit_logger.warning("%s", line)
    else:
        _audit_logger.info("%s", line)
    return record


def log_doc_acl_decision(
    *,
    actor: Optional[str],
    doc_id: Optional[str],
    decision: str,
    reason: str,
    endpoint: Optional[str] = None,
    **extra: Any,
) -> dict[str, Any]:
    """Émet (et renvoie, pour les tests) un enregistrement d'audit du FILTRE
    ACL **par-document** appliqué sur la réponse Onyx.

    On journalise la décision (``drop``/``summary``/``error``), le document
    concerné (id en CLAIR — c'est un identifiant *technique*, pas une PII) et
    la raison. L'acteur est TOUJOURS pseudonymisé (HMAC-SHA256), exactement
    comme dans ``log_access_decision`` et ``log_guardrail_decision`` —
    cohérence d'audit (chaîne logique homogène).

    Convention :
      * ``decision="drop"``     — un doc précis retiré (1 log par drop).
      * ``decision="summary"``  — un résumé agrégé (un seul par requête).
      * ``decision="error"``    — bug interne du filtre (fail-OPEN), à corréler.

    Aucune donnée métier (texte de la réponse, message utilisateur) n'est
    journalisée — minimisation.
    """
    record: dict[str, Any] = {
        "event": "doc_acl_decision",
        "decision": decision,
        "reason": reason,
        "actor_hash": pseudonymize(actor),
        "doc_id": doc_id,
        "endpoint": endpoint,
    }
    record.update(extra)
    record = {k: v for k, v in record.items() if v is not None}
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    # `drop`/`error` = signal de sécurité (warning), `summary` = info.
    if decision in ("drop", "error"):
        _audit_logger.warning("%s", line)
    else:
        _audit_logger.info("%s", line)
    return record


def log_guardrail_decision(
    *,
    actor: Optional[str],
    blocked: bool,
    rule: str,
    reason: str,
    endpoint: Optional[str] = None,
) -> dict[str, Any]:
    """Émet (et renvoie, pour les tests) un enregistrement d'audit du POST-FILTRE
    garde-fous appliqué sur la réponse de l'assistant.

    On journalise la **décision** (bloqué/laissé passer) et la **règle**
    déclenchée — JAMAIS le contenu de la réponse ni la question (minimisation,
    cohérent avec `log_access_decision`). L'acteur est TOUJOURS pseudonymisé.
    """
    record: dict[str, Any] = {
        "event": "guardrail_decision",
        "blocked": blocked,
        "rule": rule,
        "reason": reason,
        "actor_hash": pseudonymize(actor),
        "endpoint": endpoint,
    }
    record = {k: v for k, v in record.items() if v is not None}
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    # Un blocage = signal de sécurité (warning) ; un passthrough = info.
    if blocked:
        _audit_logger.warning("%s", line)
    else:
        _audit_logger.info("%s", line)
    return record
