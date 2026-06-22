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
import json
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

    # DEFENSE-IN-DEPTH (API-compat) : Onyx 4.1.x filtre via `internal_search_filters`
    # (BaseFilters de SendMessageRequest), et NON plus via `retrieval_options` (schéma
    # plus ancien). On pose donc le périmètre RBAC sur les DEUX champs : ajouter un
    # filtre ne peut que **resserrer** le périmètre (jamais l'élargir), c'est donc
    # strictement sûr quelle que soit la version d'API honorée par Onyx. La version
    # réellement honorée par 4.1.1 reste à confirmer live (cf. RUNTIME-EVIDENCE #12).
    isf = out.get("internal_search_filters")
    if not isinstance(isf, dict):
        isf = {}
        out["internal_search_filters"] = isf
    isf["document_set"] = effective

    # Neutralise un éventuel accès direct par id de document (non vérifiable en FOSS).
    out.pop("search_doc_ids", None)
    return out


def force_internal_search(
    payload: dict[str, Any],
    *,
    enabled: bool = True,
    tool_id: int = 1,
) -> dict[str, Any]:
    """Force la RECHERCHE DOCUMENTAIRE (RAG **non-agentique**) côté Onyx.

    Onyx 4.x est agentique : c'est le LLM qui *décide* d'appeler l'outil de
    recherche. Un modèle local faible (ex. qwen2.5:14b en CPU) échoue à ce choix
    — il hallucine un appel d'outil **en texte** au lieu d'invoquer
    `internal_search`, et la réponse n'est PAS sourcée (prouvé au runtime, cf.
    `.planning/RUNTIME-EVIDENCE.md` #12).

    En posant `forced_tool_id` + `allowed_tool_ids` sur l'outil de recherche,
    Onyx bascule en `tool_choice=REQUIRED` et **exécute lui-même** la
    récupération (`llm_loop.py`), puis le LLM répond à partir du contexte
    récupéré → réponse **grounded + citée** (prouvé live avec `gemma3:12b`).
    C'est le **stopgap CPU** : RAG fiable sans GPU ni agentique.

    Pré-requis (satisfait en prod) : au moins un connecteur réel existe, sinon
    `SearchTool.is_available()` est False côté Onyx et le forçage est ignoré.

    Mutate-and-return (cohérent avec `enforce_document_sets`). On NE force PAS si
    le client a déjà précisé `forced_tool_id`/`allowed_tool_ids` (respect d'un
    appel avancé). `enabled=False` => no-op (mode agentique natif, à utiliser
    quand un modèle à function-calling fiable est déployé)."""
    if not enabled:
        return payload
    if payload.get("forced_tool_id") is not None or payload.get("allowed_tool_ids"):
        return payload
    payload["forced_tool_id"] = tool_id
    payload["allowed_tool_ids"] = [tool_id]
    return payload


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


def unwrap_wrapped_answer(text: str) -> str:
    """Déballe une réponse JSON-enveloppée (stopgap RAG gemma3, cf. #12).

    Certains modèles (ex. gemma3) renvoient parfois, dans le champ texte d'Onyx,
    un OBJET JSON sérialisé du type ``{"id": "extracted_...", "result": "<vraie
    réponse>"}`` au lieu du texte brut. On extrait alors ``result``.

    DÉFENSIF / fail-safe : on ne déballe **que** si ``text`` parse comme un objet
    JSON dont la clé ``result`` est une chaîne. Dans **tous** les autres cas
    (texte non-JSON, JSON non-objet, objet sans ``result``, ``result`` non-str),
    on renvoie ``text`` INCHANGÉ — afin de ne jamais casser le cas normal ni
    altérer les citations ``[[1]]`` / le grounding portés par le texte légitime."""
    if not isinstance(text, str):
        return text
    stripped = text.strip()
    # Court-circuit : un objet JSON commence par '{'. Évite un parse inutile sur
    # du texte ordinaire (qui n'est jamais un objet JSON).
    if not stripped.startswith("{"):
        return text
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        return text
    if isinstance(parsed, dict):
        result = parsed.get("result")
        if isinstance(result, str):
            return result
    return text


def extract_answer(onyx_response: Any) -> tuple[str, str | None]:
    """Renvoie ``(texte_de_l_assistant, nom_du_champ_source)``.

    Si aucun champ texte n'est trouvable, renvoie ``("", None)`` — le post-filtre
    ne s'appliquera alors pas (on ne substitue jamais un refus à une réponse
    qu'on ne sait pas lire : ce serait un déni de service injustifié)."""
    if isinstance(onyx_response, dict):
        for field in _ANSWER_FIELDS:
            val = onyx_response.get(field)
            if isinstance(val, str) and val.strip():
                # Déballage défensif d'une réponse JSON-enveloppée (gemma3, #12)
                # AVANT que les garde-fous / le filtre ACL ne s'appliquent.
                return unwrap_wrapped_answer(val), field
        # Agrégat de paquets streaming { "packets": [ {answer_piece: "..."}, ... ] }
        packets = onyx_response.get("packets")
        if isinstance(packets, list):
            pieces = [
                p.get("answer_piece")
                for p in packets
                if isinstance(p, dict) and isinstance(p.get("answer_piece"), str)
            ]
            if pieces:
                return unwrap_wrapped_answer("".join(pieces)), "packets"
    elif isinstance(onyx_response, list):
        # Réponse = suite de paquets NDJSON déjà décodés en liste.
        pieces = [
            p.get("answer_piece")
            for p in onyx_response
            if isinstance(p, dict) and isinstance(p.get("answer_piece"), str)
        ]
        if pieces:
            return unwrap_wrapped_answer("".join(pieces)), "answer_piece[]"
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
