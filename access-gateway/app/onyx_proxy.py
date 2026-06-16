"""onyx_proxy — réécrit et relaie les requêtes de recherche/chat vers Onyx en
**forçant le filtre Document Set** au périmètre autorisé de l'utilisateur.

L'API Onyx `/chat/send-message` accepte `retrieval_options.filters.document_set`
(liste de NOMS de Document Sets). Cette fonction :

1. Intersecte tout `document_set` demandé par le client avec les sets autorisés ;
   si le client n'en demande aucun, on impose la liste autorisée complète.
2. **Refuse** (403) si l'intersection est vide alors qu'une politique deny-by-
   default est active (utilisateur sans périmètre => aucune recherche possible).
3. Empêche toute fuite : un client ne peut PAS élargir son périmètre en injectant
   un `document_set` non autorisé (il est filtré), ni le contourner via
   `search_doc_ids` (ce champ est neutralisé s'il sort du périmètre — voir doc).

C'est ici que se matérialise le *trimming par utilisateur* en FOSS, à la
granularité Document Set (≈ groupe d'accès), PAS par document.
"""
from __future__ import annotations

import copy
from typing import Any


class AccessDenied(Exception):
    """L'utilisateur n'a aucun Document Set autorisé pour cette requête."""


def _ensure_filters(payload: dict[str, Any]) -> dict[str, Any]:
    ro = payload.setdefault("retrieval_options", {})
    if not isinstance(ro, dict):
        ro = {}
        payload["retrieval_options"] = ro
    filters = ro.setdefault("filters", {})
    if not isinstance(filters, dict):
        filters = {}
        ro["filters"] = filters
    return filters


def enforce_document_sets(
    payload: dict[str, Any],
    authorized_sets: list[str],
    *,
    deny_if_empty: bool = True,
) -> dict[str, Any]:
    """Renvoie une COPIE du payload avec `document_set` borné au périmètre autorisé.

    - `authorized_sets` vide + `deny_if_empty` => AccessDenied.
    - Si le client a précisé des document_set : on garde l'INTERSECTION.
    - Sinon : on impose la totalité des `authorized_sets`.
    - `search_doc_ids` est retiré (un id de doc hors périmètre contournerait le
      filtre ; en FOSS on ne sait pas vérifier l'ACL par document — on neutralise).
    """
    authorized = list(dict.fromkeys(s for s in authorized_sets if s))
    if not authorized:
        if deny_if_empty:
            raise AccessDenied("Aucun Document Set autorisé pour cet utilisateur.")
        # Politique permissive (déconseillée) : on laisse passer sans filtre.
        return copy.deepcopy(payload)

    out = copy.deepcopy(payload)
    filters = _ensure_filters(out)

    requested = filters.get("document_set")
    if isinstance(requested, list) and requested:
        allowed = set(authorized)
        effective = [s for s in requested if s in allowed]
        if not effective:
            # Le client a demandé uniquement des sets non autorisés.
            raise AccessDenied("Document Set demandé hors du périmètre autorisé.")
    else:
        effective = authorized

    filters["document_set"] = effective

    # Neutralise un éventuel accès direct par id de document (non vérifiable en FOSS).
    out.pop("search_doc_ids", None)
    return out


def upstream_headers(api_key: str, incoming: dict[str, str] | None = None) -> dict[str, str]:
    """Construit les en-têtes vers Onyx. La clé d'API Onyx (le cas échéant) est
    injectée côté serveur ; on ne propage JAMAIS l'en-tête d'identité brut en amont
    pour éviter toute confusion de privilèges."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


# ───────────────────────────────────────────────────────────────────────────
# Chemin RÉPONSE — extraction du texte de l'assistant pour le post-filtre.
#
# Onyx renvoie la réponse de l'assistant sous plusieurs formes selon la version /
# le mode (objet `ChatMessageDetail`, agrégat de paquets `answer_piece`, etc.).
# On extrait le texte de manière DÉFENSIVE, sans casser le reste du payload :
#   * `message`            — champ canonique d'une réponse `/chat/send-message` ;
#   * `answer`             — alias rencontré dans certains relais/agrégats ;
#   * `answer_piece` x N   — concaténation si la réponse est une liste de paquets.
# On expose aussi la QUESTION posée (depuis le payload requête relayé) et un
# CONTEXTE textuel reconstruit des documents cités, pour alimenter le post-filtre.
# ───────────────────────────────────────────────────────────────────────────
# Champs candidats portant le texte de l'assistant, par ordre de priorité.
_ANSWER_FIELDS = ("message", "answer", "answer_text", "llm_answer")


def extract_answer(onyx_response: Any) -> tuple[str, str | None]:
    """Renvoie ``(texte_de_l_assistant, nom_du_champ_source)``.

    Si aucun champ texte n'est trouvable, renvoie ``("", None)`` — le post-filtre
    ne s'appliquera alors pas (on ne substitue jamais un refus à une réponse
    qu'on ne sait pas lire : ce serait un déni de service injustifié)."""
    if isinstance(onyx_response, dict):
        for field in _ANSWER_FIELDS:
            val = onyx_response.get(field)
            if isinstance(val, str) and val.strip():
                return val, field
        # Agrégat de paquets streaming { "packets": [ {answer_piece: "..."}, ... ] }
        packets = onyx_response.get("packets")
        if isinstance(packets, list):
            pieces = [
                p.get("answer_piece")
                for p in packets
                if isinstance(p, dict) and isinstance(p.get("answer_piece"), str)
            ]
            if pieces:
                return "".join(pieces), "packets"
    elif isinstance(onyx_response, list):
        # Réponse = suite de paquets NDJSON déjà décodés en liste.
        pieces = [
            p.get("answer_piece")
            for p in onyx_response
            if isinstance(p, dict) and isinstance(p.get("answer_piece"), str)
        ]
        if pieces:
            return "".join(pieces), "answer_piece[]"
    return "", None


def reconstruct_context(onyx_response: Any) -> str:
    """Reconstruit un CONTEXTE textuel (titres/blurbs des documents cités) à des
    fins de post-filtre. Best-effort : si Onyx ne renvoie pas de documents, on
    renvoie une chaîne vide (le post-filtre reste conservateur sans contexte)."""
    if not isinstance(onyx_response, dict):
        return ""
    docs = (
        onyx_response.get("top_documents")
        or onyx_response.get("context_docs")
        or onyx_response.get("final_context_docs")
        or []
    )
    if not isinstance(docs, list):
        return ""
    chunks: list[str] = []
    for d in docs:
        if not isinstance(d, dict):
            continue
        name = d.get("semantic_identifier") or d.get("document_id") or d.get("source")
        blurb = d.get("blurb") or d.get("content") or ""
        if name:
            chunks.append(f"[Document: {name}]\n{blurb}")
        elif blurb:
            chunks.append(str(blurb))
    return "\n".join(chunks)


def apply_filtered_answer(onyx_response: Any, field: str | None, new_answer: str) -> Any:
    """Réinjecte la réponse filtrée DANS le payload Onyx, sur le même champ d'où
    elle a été lue, en préservant le reste (citations, métadonnées). Si la
    réponse provient d'un agrégat de paquets, on remplace par un champ `message`
    canonique et on neutralise les pièces brutes (qui contiendraient le texte
    dangereux). Renvoie une COPIE (jamais de mutation en place)."""
    if field is None or not isinstance(onyx_response, dict):
        return onyx_response
    out = copy.deepcopy(onyx_response)
    if field in _ANSWER_FIELDS:
        out[field] = new_answer
    else:
        # Agrégat / paquets : on pose le texte sûr en `message` et on vide les pièces.
        out["message"] = new_answer
        out.pop("packets", None)
    return out
