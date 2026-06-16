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
