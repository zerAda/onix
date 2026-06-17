"""streaming — moteur SSE **identité-aware** devant Onyx (gain de latence perçue).

POURQUOI CE MODULE :
  Sur un LLM local (CPU), la génération complète d'une réponse RAG prend plusieurs
  secondes. En mode NON-streaming, l'utilisateur attend que TOUT soit généré avant
  de voir le premier mot. En **streaming token-par-token**, il voit la réponse se
  construire dès le premier paquet → la latence *perçue* est divisée par ~10.

  Le défi : on ne peut pas se contenter de relayer le flux brut d'Onyx tel quel,
  sinon on perd les **garanties de sécurité déterministes** déjà prouvées sur le
  chemin non-streaming (cf. `main.chat_send_message`) :
    * le post-filtre garde-fous (`guardrail.post_filter`) — couche 3, hors-LLM ;
    * le filtre ACL par-document (`doc_acl.filter_citations`) — RBAC fin FOSS.

  Ce module réconcilie les deux : il **forwarde les morceaux de réponse au fil de
  l'eau** (le gain de latence) tout en **interceptant** le flux pour appliquer les
  contrôles. Il est volontairement **découplé de FastAPI** (il consomme un simple
  ``AsyncIterator[bytes]`` de lignes amont et émet un ``AsyncIterator[bytes]`` de
  paquets client) pour rester testable hors-réseau. L'orchestrateur (`main.py`) le
  câble avec ``httpx.stream()`` + ``StreamingResponse`` (cf. exemple plus bas).

────────────────────────────────────────────────────────────────────────────────
SCHÉMA DES PAQUETS ONYX (amont) — format historique `/chat/send-message` :
  Onyx émet des paquets **JSON délimités par des sauts de ligne (NDJSON)**, un par
  ligne. Les formes pertinentes pour nous (cf. `onyx_proxy.extract_answer` qui lit
  déjà `answer_piece`, et le modèle Onyx legacy `danswer/chat/models.py`) :
    * morceau de réponse  : ``{"answer_piece": "<texte>"}``       (streaming token)
    * documents/contexte  : ``{"top_documents": [ {…}, … ]}``     (QADocsResponse)
    * citation            : ``{"citation_num": N, "document_id": "…"}`` (CitationInfo)
    * erreur amont        : ``{"error": "<message>"}``            (StreamingError)
  On reste TOLÉRANT : un paquet inconnu est relayé tel quel (on ne casse pas un
  flux dont on ne comprend pas tous les types — robustesse aux montées de version
  d'Onyx, p.ex. les paquets typés `message_delta`/`citation_info` récents).

CONTRAT CLIENT (paquets émis PAR la passerelle) — voir docs/STREAMING.md :
  On émet le MÊME NDJSON, augmenté de deux paquets de contrôle déterministes :
    * ``{"answer_piece": "…"}``                      — relais au fil de l'eau ;
    * ``{"top_documents":[…]}`` / ``{"citation_num":…}`` — APRÈS filtrage ACL ;
    * ``{"override": true, "answer": "<refus>", "rule": "<règle>"}``
          — **message FINAL faisant autorité** : il REMPLACE la réponse affichée.
            Émis quand un garde-fou DUR avorte le flux en cours, OU quand le
            post-filtre complet bloque a posteriori (groundedness molle).
    * ``{"done": true}``                             — fin de flux (terminal).
  Règle d'or du contrat : **le dernier message d'autorité gagne**. Un client
  conforme, en recevant un paquet ``override``, écarte le texte accumulé et
  affiche ``answer``. (Le gain de latence subsiste : 99 % des réponses passent
  sans override ; l'override est le filet de sécurité du cas résiduel.)

DISCIPLINE DE SÉCURITÉ :
  * **Garde DUR incrémental** : à chaque morceau, on re-teste le texte ACCUMULÉ
    avec les détecteurs DURS de `guardrail` (fuite de prompt, relais de lien
    d'exfiltration, écriture simulée). Au premier déclenchement → on AVORTE le
    flux immédiatement (on ne forwarde PAS le morceau fautif) et on émet un
    ``override`` de refus + ``done``. Un secret qui a commencé à fuiter ne doit
    pas finir de sortir.
  * **Garde MOU final** : la groundedness (fait chiffré sans citation) ne peut
    être tranchée qu'une fois la phrase complète ET les citations connues. On
    l'applique donc à la FIN, via le post-filtre COMPLET, comme un ``override``
    d'autorité (cf. trade-off honnête documenté).
  * **FAIL-CLOSED** : toute exception sur le chemin de contrôle (garde, filtre
    ACL) ⇒ on n'émet JAMAIS du contenu non vérifié : on coupe et on émet un
    refus sûr + ``done``, et on logue. Indisponibilité > fuite.

100 % stdlib (json) + types — aucune dépendance lourde ; n'importe PAS FastAPI.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Callable, Optional

from .guardrail import (
    REFUSAL_INJECTION,
    claims_write_action,
    leaks_prompt_or_persona,
    relays_exfil_link,
)
from .metrics import (
    inc_stream_aborted,
    inc_stream_overridden,
    inc_stream_requests,
)

_logger = logging.getLogger("onix.gateway")

# Refus générique si une erreur interne survient sur le chemin de contrôle
# (fail-closed). Tonalité cohérente avec les `REFUSAL_*` de `guardrail`.
REFUSAL_INTERNAL = (
    "Une vérification de sécurité a échoué : par précaution, la réponse a été "
    "interrompue. Veuillez reformuler votre demande."
)

# Champs candidats portant un morceau de réponse, par ordre de priorité. On
# reconnaît le format historique (`answer_piece`) ET, par tolérance, le contenu
# d'un delta typé récent (`content`/`text` sous une clé `message_delta`/`delta`).
_PIECE_FIELDS = ("answer_piece", "content", "text", "token")
# Champs portant une LISTE de documents (contexte) — alignés sur doc_acl._DOC_LIST_FIELDS.
_DOC_LIST_FIELDS = (
    "top_documents",
    "context_docs",
    "final_context_docs",
    "documents",
    "source_documents",
)
_CITATION_FIELD = "citations"


# ───────────────────────────────────────────────────────────────────────────
# Sérialisation des paquets émis vers le client (NDJSON, une ligne par paquet).
# ───────────────────────────────────────────────────────────────────────────
def _emit(obj: dict[str, Any]) -> bytes:
    """Sérialise un paquet en NDJSON (JSON compact + saut de ligne), en bytes."""
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _piece_text(packet: dict[str, Any]) -> Optional[str]:
    """Extrait le texte d'un paquet « morceau de réponse », ou None si le paquet
    ne porte pas de morceau (paquet de documents, citation, métadonnée…)."""
    for field in _PIECE_FIELDS:
        val = packet.get(field)
        if isinstance(val, str):
            return val
    return None


def _is_doc_or_citation_packet(packet: dict[str, Any]) -> bool:
    """True si le paquet transporte une liste de documents ou de citations
    (le paquet « final » sur lequel on applique le filtre ACL par-document)."""
    for field in _DOC_LIST_FIELDS:
        if isinstance(packet.get(field), list):
            return True
    if isinstance(packet.get(_CITATION_FIELD), list):
        return True
    return False


def _hard_violation(answer_accum: str) -> Optional[str]:
    """Applique les détecteurs DURS de `guardrail` sur le texte ACCUMULÉ.

    Renvoie l'identifiant de règle violée (str) au premier déclenchement, ou
    None si rien de dur n'est détecté. On NE teste PAS ici les règles MOLLES
    (groundedness, connaissances générales, lecture-seule sur la *demande*) :
    elles ne peuvent être tranchées proprement qu'en fin de flux (cf. trade-off).
    L'ordre suit `guardrail.post_filter` (du plus dur au moins dur).
    """
    if leaks_prompt_or_persona(answer_accum):
        return "no_prompt_leak"
    if relays_exfil_link(answer_accum):
        return "no_exfil_relay"
    if claims_write_action(answer_accum):
        return "read_only"
    return None


# ───────────────────────────────────────────────────────────────────────────
# Le moteur de streaming proprement dit.
# ───────────────────────────────────────────────────────────────────────────
async def proxy_stream(
    upstream_lines: AsyncIterator[bytes],
    *,
    question: str,
    principal: Any,
    acl: Any,
    settings: Any,
    post_filter: Callable[[str, str, str], Any],
    doc_acl_filter: Callable[..., tuple[Any, list]],
    extract_answer: Callable[[Any], tuple[str, Optional[str]]],
    apply_filtered_answer: Callable[[Any, Optional[str], str], Any],
    audit: Any = None,
) -> AsyncIterator[bytes]:
    """Relaie le flux Onyx au client en préservant les invariants de sécurité.

    Args:
        upstream_lines: itérateur asynchrone des LIGNES NDJSON émises par Onyx
            (typiquement ``response.aiter_lines()`` d'un ``httpx.stream``).
        question: la question utilisateur (alimente le post-filtre).
        principal: identité de l'appelant (cf. `identity.Principal`) — passée
            telle quelle au filtre ACL par-document.
        acl: implémentation `DocACL` (ou None si le filtre est inactif).
        settings: réglages (`config.Settings`) — `doc_acl_*`, `guardrail_enabled`.
        post_filter: `guardrail.post_filter` (injecté pour testabilité).
        doc_acl_filter: `doc_acl.filter_citations` (injecté).
        extract_answer / apply_filtered_answer: helpers `onyx_proxy` (injectés).
        audit: module d'audit optionnel (`app.audit`) — journalise les décisions.

    Yields:
        Des paquets NDJSON (bytes) destinés au client (cf. CONTRAT CLIENT supra).

    Garanties :
        * gain de latence : les ``answer_piece`` sont relayés au fil de l'eau ;
        * garde DUR incrémental : avorte le flux à la 1re violation dure ;
        * filtre ACL : appliqué au paquet documents/citations AVANT relais ;
        * garde MOU final : override d'autorité si le post-filtre complet bloque ;
        * fail-closed : toute erreur de contrôle ⇒ refus sûr (jamais de fuite).
    """
    inc_stream_requests()

    answer_accum = ""          # texte de réponse accumulé (pour les gardes)
    aborted = False            # un garde DUR a-t-il déjà coupé le flux ?
    doc_acl_done = False       # a-t-on déjà filtré un paquet documents/citations ?
    actor = getattr(principal, "user_id", None)
    guardrail_on = bool(getattr(settings, "guardrail_enabled", True))

    try:
        async for raw in upstream_lines:
            if aborted:
                # Flux déjà coupé par un garde dur : on draine l'amont sans rien
                # émettre de plus (le client a déjà reçu override + done).
                continue

            line = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
            line = line.strip()
            if not line:
                continue

            try:
                packet = json.loads(line)
            except ValueError:
                # Ligne non-JSON : on la relaie telle quelle (robustesse), elle ne
                # peut pas porter de morceau de réponse qu'on saurait analyser.
                yield (line + "\n").encode("utf-8")
                continue

            if not isinstance(packet, dict):
                # Forme inattendue (liste, scalaire) : relais brut, pas d'analyse.
                yield _emit({"raw": packet}) if not isinstance(packet, (str, int, float)) else (line + "\n").encode("utf-8")
                continue

            # ── Paquet d'ERREUR amont : on le relaie tel quel (le client gère). ──
            if isinstance(packet.get("error"), str):
                yield _emit(packet)
                continue

            # ── Paquet « morceau de réponse » : garde DUR incrémental puis relais. ──
            piece = _piece_text(packet)
            if piece is not None:
                tentative = answer_accum + piece
                try:
                    rule = _hard_violation(tentative) if guardrail_on else None
                except Exception as exc:  # noqa: BLE001 — fail-closed sur le garde
                    _logger.warning("stream garde dur en erreur: %s (%s)", exc, type(exc).__name__)
                    aborted = True
                    inc_stream_aborted("guard_error")
                    _log_guardrail(audit, actor, True, "stream_guard_error", str(type(exc).__name__))
                    yield _emit({"override": True, "answer": REFUSAL_INTERNAL, "rule": "stream_guard_error"})
                    yield _emit({"done": True})
                    continue

                if rule is not None:
                    # VIOLATION DURE : on n'émet PAS le morceau fautif. On coupe.
                    aborted = True
                    inc_stream_aborted(rule)
                    _log_guardrail(audit, actor, True, rule, "hard invariant tripped mid-stream")
                    yield _emit({"override": True, "answer": REFUSAL_INJECTION, "rule": rule})
                    yield _emit({"done": True})
                    continue

                # Conforme jusqu'ici : on accumule ET on forwarde (gain de latence).
                answer_accum = tentative
                yield _emit(packet)
                continue

            # ── Paquet documents/citations : filtre ACL par-document AVANT relais. ──
            if _is_doc_or_citation_packet(packet):
                try:
                    filtered, dropped = _apply_doc_acl(
                        packet, principal, acl, settings, audit,
                        doc_acl_filter, extract_answer, apply_filtered_answer,
                    )
                except Exception as exc:  # noqa: BLE001 — fail-closed sur le filtre ACL
                    _logger.warning("stream filtre ACL en erreur: %s (%s)", exc, type(exc).__name__)
                    aborted = True
                    inc_stream_aborted("doc_acl_error")
                    yield _emit({"override": True, "answer": REFUSAL_INTERNAL, "rule": "doc_acl_error"})
                    yield _emit({"done": True})
                    continue
                doc_acl_done = True
                # Si TOUTES les citations ont été retirées et que strip_uncited est
                # actif, le filtre a substitué le texte d'assistant : on en fait un
                # override d'autorité (la réponse affichée doit devenir le refus).
                refusal = _stripped_refusal(filtered, extract_answer)
                if refusal is not None:
                    inc_stream_overridden()
                    yield _emit({"override": True, "answer": refusal, "rule": "no_accessible_source"})
                    yield _emit(filtered)
                    yield _emit({"done": True})
                    aborted = True  # plus rien à émettre après un override final
                    continue
                yield _emit(filtered)
                continue

            # ── Paquet inconnu (métadonnée, heartbeat, branch…) : relais tel quel. ──
            yield _emit(packet)

        # ── FIN DE FLUX : garde MOU via le post-filtre COMPLET sur l'accumulé. ──
        if not aborted and guardrail_on and answer_accum.strip():
            try:
                verdict = post_filter(str(question or ""), "", answer_accum)
            except Exception as exc:  # noqa: BLE001 — fail-closed sur le post-filtre final
                _logger.warning("stream post-filtre final en erreur: %s (%s)", exc, type(exc).__name__)
                inc_stream_aborted("postfilter_error")
                _log_guardrail(audit, actor, True, "stream_postfilter_error", str(type(exc).__name__))
                yield _emit({"override": True, "answer": REFUSAL_INTERNAL, "rule": "stream_postfilter_error"})
                yield _emit({"done": True})
                return
            if getattr(verdict, "blocked", False):
                # Le garde DUR n'a rien vu en route, mais le post-filtre complet
                # bloque (typiquement groundedness molle) → override d'autorité.
                inc_stream_overridden()
                _log_guardrail(
                    audit, actor, True,
                    getattr(verdict, "rule", "blocked"),
                    getattr(verdict, "reason", "post_filter blocked at stream end"),
                )
                yield _emit({"override": True, "answer": verdict.answer, "rule": verdict.rule})
            else:
                _log_guardrail(
                    audit, actor, False,
                    getattr(verdict, "rule", "passthrough"),
                    getattr(verdict, "reason", "conforme"),
                )

    except Exception as exc:  # noqa: BLE001 — filet ultime : aucune fuite non vérifiée
        _logger.warning("stream erreur inattendue: %s (%s)", exc, type(exc).__name__)
        inc_stream_aborted("internal_error")
        yield _emit({"override": True, "answer": REFUSAL_INTERNAL, "rule": "internal_error"})
        yield _emit({"done": True})
        return

    # Terminal normal : un seul `done` (jamais après un override DUR qui a déjà
    # émis le sien — dans ce cas `aborted` est vrai et on ne repasse pas ici).
    if not aborted:
        yield _emit({"done": True})


# ───────────────────────────────────────────────────────────────────────────
# Helpers internes (testés indirectement via proxy_stream).
# ───────────────────────────────────────────────────────────────────────────
def _apply_doc_acl(
    packet: dict[str, Any],
    principal: Any,
    acl: Any,
    settings: Any,
    audit: Any,
    doc_acl_filter: Callable[..., tuple[Any, list]],
    extract_answer: Callable[[Any], tuple[str, Optional[str]]],
    apply_filtered_answer: Callable[[Any, Optional[str], str], Any],
) -> tuple[dict[str, Any], list]:
    """Applique `doc_acl.filter_citations` au paquet documents/citations.

    Si l'ACL est inactive (pas de fichier / désactivée), renvoie le paquet
    inchangé. On réutilise EXACTEMENT le même filtre que le chemin non-streaming
    (aucune logique d'autorisation dupliquée — une seule source de vérité)."""
    if acl is None or not getattr(settings, "doc_acl_enabled", False):
        return packet, []
    filtered, dropped = doc_acl_filter(
        packet,
        principal,
        acl,
        audit,
        strip_uncited=getattr(settings, "doc_acl_strip_uncited", True),
        extract_answer=extract_answer,
        apply_filtered_answer=apply_filtered_answer,
        enabled=getattr(settings, "doc_acl_enabled", True),
    )
    return filtered, dropped


def _stripped_refusal(
    filtered: Any,
    extract_answer: Callable[[Any], tuple[str, Optional[str]]],
) -> Optional[str]:
    """Renvoie le texte de refus si le filtre ACL a substitué le message par
    `REFUSAL_NO_ACCESSIBLE_SOURCE` (toutes les citations retirées), sinon None."""
    from .doc_acl import REFUSAL_NO_ACCESSIBLE_SOURCE

    if not isinstance(filtered, dict):
        return None
    text, field = extract_answer(filtered)
    if field is not None and text == REFUSAL_NO_ACCESSIBLE_SOURCE:
        return text
    return None


def _log_guardrail(
    audit: Any, actor: Optional[str], blocked: bool, rule: str, reason: str
) -> None:
    """Journalise une décision garde-fou (best-effort : un audit en erreur ne
    doit jamais impacter le flux)."""
    if audit is None:
        return
    try:
        audit.log_guardrail_decision(
            actor=actor, blocked=blocked, rule=rule, reason=reason,
            endpoint="chat/send-message[stream]",
        )
    except Exception:  # noqa: BLE001
        pass


# ───────────────────────────────────────────────────────────────────────────
# EXEMPLE D'INTÉGRATION (à coller dans `main.py` par l'orchestrateur — ce module
# n'importe PAS FastAPI pour rester pur/testable). ~15 lignes :
#
#     from fastapi.responses import StreamingResponse
#     from .streaming import proxy_stream
#
#     # … après enforce_document_sets / résolution principal, si stream demandé …
#     if settings.stream_enabled and payload.get("stream") is True:
#         url = f"{settings.onyx_base_url}/chat/send-message"
#         req = request.app.state.http.build_request(
#             "POST", url, json=safe_payload,
#             headers=upstream_headers(settings.onyx_api_key),
#         )
#         resp = await request.app.state.http.send(req, stream=True)
#
#         async def _gen():
#             try:
#                 async for chunk in proxy_stream(
#                     resp.aiter_lines(),
#                     question=question_text, principal=principal, acl=acl,
#                     settings=settings, post_filter=post_filter,
#                     doc_acl_filter=filter_citations, extract_answer=extract_answer,
#                     apply_filtered_answer=apply_filtered_answer, audit=_audit,
#                 ):
#                     yield chunk
#             finally:
#                 await resp.aclose()
#
#         return StreamingResponse(_gen(), media_type="application/x-ndjson")
#
# NB : le cache est CORRECTEMENT contourné pour les flux — `should_bypass`
# renvoie déjà "streaming" pour `stream=True` (cf. cache.py). On ne met JAMAIS
# un flux en cache (sémantique incompatible avec un body JSON intégral).
# ───────────────────────────────────────────────────────────────────────────
