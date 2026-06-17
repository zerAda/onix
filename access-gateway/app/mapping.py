"""mapping — traduit des groupes Entra en Document Sets Onyx autorisés.

Le mapping est chargé d'un fichier JSON (monté en lecture seule). Deux formes
acceptées :

1) **Simple** — clé = identifiant de groupe (objectId GUID *ou* displayName),
   valeur = liste de noms de Document Sets Onyx :

       {
         "11111111-1111-1111-1111-111111111111": ["clients-nord"],
         "Commerciaux-Sud": ["clients-sud"]
       }

2) **Structurée** — permet métadonnées + politique :

       {
         "version": 1,
         "default_document_sets": [],          // sets accordés à tout user authentifié
         "groups": {
           "<guid-ou-nom>": {"document_sets": ["clients-nord"], "label": "Commerciaux Nord"}
         }
       }

Principe : **deny-by-default**. Un groupe inconnu n'accorde aucun set. L'union des
sets des groupes de l'utilisateur (plus `default_document_sets`) constitue son
périmètre autorisé. La comparaison des clés de groupe est **insensible à la casse**
(les GUID le sont déjà ; les displayName le deviennent par robustesse).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GroupMap:
    """Mapping immuable groupe -> Document Sets, plus sets par défaut."""

    by_group: dict[str, tuple[str, ...]] = field(default_factory=dict)
    default_document_sets: tuple[str, ...] = ()

    def authorized_document_sets(self, group_ids: list[str]) -> list[str]:
        """Union (triée, dédupliquée) des Document Sets autorisés pour ces groupes.

        `group_ids` peut mêler GUID et displayName : on teste les deux formes,
        en casse insensible. Aucun groupe correspondant => seuls les sets par
        défaut sont renvoyés (souvent vide => deny-by-default en aval).
        """
        granted: set[str] = set(self.default_document_sets)
        for gid in group_ids:
            key = (gid or "").strip().lower()
            if key in self.by_group:
                granted.update(self.by_group[key])
        return sorted(granted)


def _normalize(raw: dict) -> GroupMap:
    # Forme structurée ?
    if isinstance(raw.get("groups"), dict):
        groups_src = raw["groups"]
        defaults = raw.get("default_document_sets", []) or []
    else:
        groups_src = raw
        defaults = []

    by_group: dict[str, tuple[str, ...]] = {}
    for key, value in groups_src.items():
        if key in {"version", "default_document_sets", "groups"}:
            continue
        if isinstance(value, dict):
            sets = value.get("document_sets", []) or []
        else:
            sets = value or []
        if not isinstance(sets, list):
            raise ValueError(f"Mapping invalide pour le groupe '{key}': liste attendue.")
        # Dédup en conservant des chaînes propres.
        clean = tuple(dict.fromkeys(str(s).strip() for s in sets if str(s).strip()))
        by_group[str(key).strip().lower()] = clean

    if not isinstance(defaults, list):
        raise ValueError("default_document_sets doit être une liste.")
    default_sets = tuple(dict.fromkeys(str(s).strip() for s in defaults if str(s).strip()))
    return GroupMap(by_group=by_group, default_document_sets=default_sets)


def load_group_map(path: str) -> GroupMap:
    """Charge et valide le mapping depuis `path`. Fichier absent => mapping vide
    (donc deny-by-default total, sauf default_document_sets)."""
    if not path or not os.path.exists(path):
        return GroupMap()
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ValueError("Le mapping JSON doit être un objet.")
    return _normalize(raw)


def load_group_map_from_obj(raw: dict) -> GroupMap:
    """Variante en mémoire (tests / injection)."""
    return _normalize(raw)
