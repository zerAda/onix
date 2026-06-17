"""doc_acl — filtre **par document** appliqué sur la RÉPONSE d'Onyx.

Pourquoi un module dédié :
  Le cloisonnement FOSS d'onix vit normalement à la **granularité Document Set**
  (cf. `onyx_proxy.enforce_document_sets`) : un utilisateur ne déclenche une
  recherche que dans les sets autorisés par son/ses groupes Entra. Mais à
  l'INTÉRIEUR d'un Document Set, deux utilisateurs peuvent voir des citations
  vers des documents auxquels ils n'ont individuellement pas accès (l'index
  FOSS est partagé). En **EE/Cloud**, la *permission sync* règle cela à la
  recherche ; en **FOSS**, ce module ferme la dernière brèche **côté RÉPONSE**
  en retirant les citations vers les documents NON autorisés pour l'appelant.

Honnêteté assumée :
  * C'est un **filtre de SORTIE**, pas un filtre de récupération. Le LLM d'Onyx
    a potentiellement vu (et raisonné sur) du contenu d'un document non
    autorisé pendant la génération. On ne peut donc PAS prétendre au « zéro
    fuite » d'une EE. Le mode strict "zero-leak" exige soit Onyx EE
    (permission sync), soit une instance Onyx séparée par tier d'accès.
  * Ce module **ferme néanmoins** la fuite VISIBLE à l'utilisateur (citations
    rendues, snippets, métadonnées de documents inaccessibles) — ce qui est
    précisément ce que voit l'humain dans une réponse RAG. Documenté §4.4 de
    `docs/RBAC.md` (section « Per-Document Filter (FOSS) »).

Discipline d'erreur :
  * **Fail-CLOSED par document inconnu** (cohérent avec `GATEWAY_DENY_IF_NO_MATCH`)
    : un `doc_id` absent de l'ACL est traité par `default_policy` (`deny`).
  * **Fail-OPEN sur exception interne** (loader JSON cassé, accès attribut
    inattendu) : la réponse passe NON filtrée, un événement `doc_acl_error`
    est journalisé. Choix de disponibilité — on surface le bug sans bloquer le
    service. Ce sont deux concerns DIFFÉRENTS (autorisation d'un doc inconnu
    ≠ bug interne du filtre).

Aucune dépendance externe (stdlib only) — embarqué dans l'image gateway sans
alourdir `requirements.txt`.
"""
from __future__ import annotations

import copy
import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Protocol

logger = logging.getLogger("onix.gateway.doc_acl")


# ───────────────────────────────────────────────────────────────────────────
# Refus déterministe — utilisé si TOUTES les citations sont retirées (et qu'on
# a configuré la substitution). Tonalité cohérente avec `guardrail.REFUSAL_*`.
# ───────────────────────────────────────────────────────────────────────────
REFUSAL_NO_ACCESSIBLE_SOURCE = (
    "Aucune source accessible ne soutient cette réponse dans votre périmètre "
    "documentaire. Je ne réponds qu'à partir des documents auxquels vous avez "
    "personnellement accès."
)


# ───────────────────────────────────────────────────────────────────────────
# Interface DocACL — petit Protocol/ABC pour découpler les sources d'ACL.
# ───────────────────────────────────────────────────────────────────────────
class _PrincipalLike(Protocol):
    """Surface minimale attendue d'un `Principal` (cf. `identity.Principal`)."""

    user_id: str
    upn: Optional[str]
    group_ids: list[str]


class DocACL(ABC):
    """ACL par-document (interface)."""

    @abstractmethod
    def is_authorized(self, doc_id: str, principal: _PrincipalLike) -> bool:
        """`True` si l'identité est autorisée à VOIR la citation pour ce doc."""

    def authorized_ids(
        self, candidate_ids: Iterable[str], principal: _PrincipalLike
    ) -> set[str]:
        """Renvoie le sous-ensemble des `candidate_ids` autorisés. Implémentation
        par défaut basée sur `is_authorized` — surclassable pour optimiser."""
        return {d for d in candidate_ids if d and self.is_authorized(d, principal)}


# ───────────────────────────────────────────────────────────────────────────
# StaticDocACL — source de vérité simple, chargée d'un fichier JSON.
# Format attendu :
#   {
#     "doc_id_1": {"groups": ["G1","G2"], "users": ["alice@contoso.fr"]},
#     "doc_id_2": {"groups": ["G2"]}
#   }
# Un document SANS entrée se voit appliquer `default_policy` (deny|allow).
# ───────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _Entry:
    groups: frozenset[str]
    users: frozenset[str]


class StaticDocACL(DocACL):
    """ACL statique chargée d'un JSON. `default_policy` ∈ {"deny","allow"}.

    `deny` (défaut) est la posture cohérente avec la passerelle (deny-by-default)
    : un document non listé est invisible.
    """

    def __init__(
        self,
        entries: Optional[dict[str, _Entry]] = None,
        *,
        default_policy: str = "deny",
    ) -> None:
        if default_policy not in ("deny", "allow"):
            raise ValueError(f"default_policy invalide: {default_policy!r}")
        self._entries: dict[str, _Entry] = entries or {}
        self._default_policy: str = default_policy

    @classmethod
    def from_file(
        cls, path: str, *, default_policy: str = "deny"
    ) -> "StaticDocACL":
        """Charge depuis un fichier JSON. Fichier absent → ACL vide (donc
        deny-by-default total : aucune citation autorisée si default=deny)."""
        if not path or not os.path.exists(path):
            logger.info(
                "doc_acl: fichier '%s' absent — ACL vide (default_policy=%s).",
                path, default_policy,
            )
            return cls(entries={}, default_policy=default_policy)
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return cls.from_obj(raw, default_policy=default_policy)

    @classmethod
    def from_obj(
        cls, raw: Any, *, default_policy: str = "deny"
    ) -> "StaticDocACL":
        """Variante en mémoire (tests / injection). Normalise les clés en casse
        insensible côté groupe/user (cohérent avec `mapping.py`)."""
        if not isinstance(raw, dict):
            raise ValueError("doc_acl: l'objet racine doit être un objet JSON.")
        entries: dict[str, _Entry] = {}
        for doc_id, spec in raw.items():
            # On tolère les commentaires/clés de métadonnées préfixées par '_'.
            if not isinstance(doc_id, str) or doc_id.startswith("_"):
                continue
            if not isinstance(spec, dict):
                raise ValueError(
                    f"doc_acl: entrée '{doc_id}' doit être un objet "
                    "{'groups':[...], 'users':[...]}."
                )
            groups_raw = spec.get("groups") or []
            users_raw = spec.get("users") or []
            if not isinstance(groups_raw, list) or not isinstance(users_raw, list):
                raise ValueError(
                    f"doc_acl: 'groups'/'users' doivent être des listes pour '{doc_id}'."
                )
            groups = frozenset(
                str(g).strip().lower() for g in groups_raw if str(g).strip()
            )
            users = frozenset(
                str(u).strip().lower() for u in users_raw if str(u).strip()
            )
            entries[str(doc_id).strip()] = _Entry(groups=groups, users=users)
        return cls(entries=entries, default_policy=default_policy)

    @property
    def default_policy(self) -> str:
        return self._default_policy

    def is_authorized(self, doc_id: str, principal: _PrincipalLike) -> bool:
        if not doc_id:
            return False
        entry = self._entries.get(str(doc_id).strip())
        if entry is None:
            # Pas d'entrée pour ce document : politique par défaut.
            return self._default_policy == "allow"
        # 1) Override par utilisateur (UPN ou user_id) — gagne sur le groupe.
        upn = (getattr(principal, "upn", None) or "").strip().lower()
        uid = (getattr(principal, "user_id", "") or "").strip().lower()
        if upn and upn in entry.users:
            return True
        if uid and uid in entry.users:
            return True
        # 2) Membre d'un groupe autorisé ?
        for gid in getattr(principal, "group_ids", []) or []:
            if (gid or "").strip().lower() in entry.groups:
                return True
        return False


# ───────────────────────────────────────────────────────────────────────────
# CompositeDocACL — OR-merge de plusieurs sources. Permet d'enrichir
# ultérieurement avec un cache Graph (TODO : voir `DECISION_RBAC.md` §4).
# ───────────────────────────────────────────────────────────────────────────
class CompositeDocACL(DocACL):
    """ACL composite : autorise si AU MOINS une source l'autorise."""

    def __init__(self, sources: Iterable[DocACL]) -> None:
        self._sources: tuple[DocACL, ...] = tuple(sources)
        if not self._sources:
            raise ValueError("CompositeDocACL: au moins une source requise.")

    def is_authorized(self, doc_id: str, principal: _PrincipalLike) -> bool:
        return any(s.is_authorized(doc_id, principal) for s in self._sources)


# ───────────────────────────────────────────────────────────────────────────
# Filtre de citations — point d'entrée appelé par l'orchestrateur (`main.py`).
#
# On supporte les formes connues de réponse Onyx, observées dans le code
# (`onyx_proxy.reconstruct_context` et le connecteur amont) :
#   * `top_documents`     — liste de docs canoniques (chemin réponse principal)
#   * `context_docs`      — alias historique
#   * `final_context_docs`— variante de finalisation
#   * `documents`         — forme générique (certains endpoints)
#   * `source_documents`  — forme citations-only
#   * `citations`         — liste de pointeurs vers les docs cités dans le texte
# On NE crashe pas si une forme inattendue arrive : on filtre ce qu'on
# reconnaît et on laisse le reste passer.
# ───────────────────────────────────────────────────────────────────────────
_DOC_LIST_FIELDS = (
    "top_documents",
    "context_docs",
    "final_context_docs",
    "documents",
    "source_documents",
)
_CITATION_FIELD = "citations"
# Clés d'identifiant de doc, par ordre de priorité (la première trouvée gagne).
_ID_FIELDS = ("document_id", "id", "source_id", "doc_id")


def _doc_id(item: Any) -> Optional[str]:
    """Extrait l'identifiant d'un document/citation, ou None si introuvable."""
    if not isinstance(item, dict):
        return None
    for f in _ID_FIELDS:
        v = item.get(f)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int,)):  # certains backends renvoient des ints
            return str(v)
    return None


def _filter_doc_list(
    items: list[Any], allowed_ids: set[str]
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Renvoie ``(kept, dropped)``. ``dropped`` contient des dicts d'audit
    minimaux. Les items sans `doc_id` extractible sont laissés tels quels (on
    ne juge pas ce qu'on ne sait pas identifier — cohérent avec la posture
    « ne crashe pas sur shape inattendue »)."""
    kept: list[Any] = []
    dropped: list[dict[str, Any]] = []
    for it in items:
        did = _doc_id(it)
        if did is None:
            kept.append(it)
            continue
        if did in allowed_ids:
            kept.append(it)
        else:
            dropped.append({"doc_id": did, "reason": "not_authorized"})
    return kept, dropped


def _collect_candidate_ids(body: dict[str, Any]) -> set[str]:
    """Énumère tous les `doc_id` candidats dans le payload, toutes formes
    confondues. Sert à interroger l'ACL en bulk (perf + audit unique)."""
    ids: set[str] = set()
    for field in _DOC_LIST_FIELDS:
        seq = body.get(field)
        if isinstance(seq, list):
            for it in seq:
                d = _doc_id(it)
                if d:
                    ids.add(d)
    cit = body.get(_CITATION_FIELD)
    if isinstance(cit, list):
        for it in cit:
            d = _doc_id(it)
            if d:
                ids.add(d)
    return ids


def _strip_uncited_answer(
    body: dict[str, Any],
    extract_answer: Callable[[Any], tuple[str, Optional[str]]],
    apply_filtered_answer: Callable[[Any, Optional[str], str], Any],
) -> dict[str, Any]:
    """Substitue le texte de l'assistant par `REFUSAL_NO_ACCESSIBLE_SOURCE`
    lorsque TOUTES les citations ont été retirées et que le contenu original
    affirmait des faits (cf. §discipline d'erreur)."""
    _, field = extract_answer(body)
    if field is None:
        # Pas de texte d'assistant détectable — on n'invente pas un refus.
        return body
    return apply_filtered_answer(body, field, REFUSAL_NO_ACCESSIBLE_SOURCE)


def filter_citations(
    body: Any,
    principal: _PrincipalLike,
    acl: DocACL,
    audit: Any = None,
    *,
    strip_uncited: bool = True,
    extract_answer: Optional[Callable[[Any], tuple[str, Optional[str]]]] = None,
    apply_filtered_answer: Optional[Callable[[Any, Optional[str], str], Any]] = None,
    enabled: bool = True,
) -> tuple[Any, list[dict[str, Any]]]:
    """Filtre les citations/documents non autorisés de la réponse Onyx.

    Args:
        body: corps de la réponse Onyx (dict typique ; tolérant aux autres
            formes — passées telles quelles).
        principal: identité de l'appelant (cf. `identity.Principal`).
        acl: implémentation `DocACL`.
        audit: module/objet audit (typiquement `app.audit`) exposant
            `log_doc_acl_decision(...)`. Si None → pas de log (utile tests).
        strip_uncited: si toutes les citations sont retirées, substitue le
            texte de l'assistant par un refus « pas de source accessible ».
        extract_answer / apply_filtered_answer: injectés depuis `onyx_proxy`
            pour éviter une dépendance circulaire et faciliter le test.
        enabled: court-circuit no-op (settings `doc_acl_enabled=false`).

    Returns:
        ``(filtered_body, dropped_entries)`` — ``filtered_body`` est une COPIE
        (jamais de mutation en place) ; ``dropped_entries`` est une liste de
        ``{"doc_id": ..., "reason": ...}``.

    Erreurs :
        - Jamais d'exception remontée dans le chemin requête : sur erreur
          interne, on log `doc_acl_error` et on RENVOIE LE BODY INCHANGÉ
          (fail-OPEN sur bug interne, fail-CLOSED sur doc inconnu — concerns
          différents, cf. docstring du module).
    """
    if not enabled:
        return body, []
    if not isinstance(body, dict):
        # Forme inattendue (liste de paquets streaming p.ex.) — laissé tel quel.
        return body, []

    try:
        candidate_ids = _collect_candidate_ids(body)
        if not candidate_ids:
            return body, []
        allowed_ids = acl.authorized_ids(candidate_ids, principal)
        unauthorized = candidate_ids - allowed_ids
        if not unauthorized:
            return body, []

        out = copy.deepcopy(body)
        all_dropped: list[dict[str, Any]] = []
        # Filtre chaque liste de documents reconnue.
        for field in _DOC_LIST_FIELDS:
            seq = out.get(field)
            if isinstance(seq, list):
                kept, dropped = _filter_doc_list(seq, allowed_ids)
                if dropped:
                    out[field] = kept
                    for d in dropped:
                        d["field"] = field
                    all_dropped.extend(dropped)
        # Filtre la liste de citations.
        cit_seq = out.get(_CITATION_FIELD)
        if isinstance(cit_seq, list):
            kept, dropped = _filter_doc_list(cit_seq, allowed_ids)
            if dropped:
                out[_CITATION_FIELD] = kept
                for d in dropped:
                    d["field"] = _CITATION_FIELD
                all_dropped.extend(dropped)

        # Si après filtrage il ne reste AUCUNE citation/document mais qu'on
        # avait des candidats au départ ⇒ substitution éventuelle de la réponse.
        all_lists_empty = True
        for field in (*_DOC_LIST_FIELDS, _CITATION_FIELD):
            seq = out.get(field)
            if isinstance(seq, list) and len(seq) > 0:
                all_lists_empty = False
                break

        if (
            strip_uncited
            and all_lists_empty
            and extract_answer is not None
            and apply_filtered_answer is not None
        ):
            out = _strip_uncited_answer(out, extract_answer, apply_filtered_answer)

        # Audit : un log SUMMARY (économe), + un log par drop (corrélation fine).
        if audit is not None and all_dropped:
            actor = getattr(principal, "user_id", None)
            try:
                # Détails par document (un log par drop — utile en investigation).
                for d in all_dropped:
                    audit.log_doc_acl_decision(
                        actor=actor,
                        doc_id=d.get("doc_id"),
                        decision="drop",
                        reason=d.get("reason", "not_authorized"),
                        field=d.get("field"),
                        endpoint="chat/send-message",
                    )
                # Résumé (un seul log, comptes agrégés).
                audit.log_doc_acl_decision(
                    actor=actor,
                    doc_id=None,
                    decision="summary",
                    reason="bulk_filter",
                    candidates=len(candidate_ids),
                    allowed=len(allowed_ids),
                    dropped=len(all_dropped),
                    endpoint="chat/send-message",
                )
            except Exception:  # noqa: BLE001
                # Audit en erreur ne doit JAMAIS impacter la réponse.
                logger.warning("doc_acl: audit indisponible — drops non journalisés.")
        return out, all_dropped
    except Exception as exc:  # noqa: BLE001
        # Bug interne : fail-OPEN (disponibilité), mais on logue précisément.
        logger.warning("doc_acl_error: %s (%s)", exc, type(exc).__name__)
        if audit is not None:
            try:
                audit.log_doc_acl_decision(
                    actor=getattr(principal, "user_id", None),
                    doc_id=None,
                    decision="error",
                    reason=f"internal_{type(exc).__name__}",
                    endpoint="chat/send-message",
                )
            except Exception:  # noqa: BLE001
                pass
        return body, []
