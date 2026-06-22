"""fabric_doc_acl — adaptateur **`DocACL`** au-dessus de `fabric_acl.py`, qui
branche la décision d'autorisation Fabric (fail-closed) sur le **filtre de
citations** (`doc_acl.filter_citations`). Chemin RÉPONSE, FOSS.

Pourquoi ce module (blocker M3) :
  `fabric_acl.py` sait répondre « le principal P peut-il LIRE l'item Fabric I du
  workspace W ? » (gold-only, roleAssignments + OneLake, fail-closed). Mais cette
  décision n'était JAMAIS branchée au filtre de citations : `main._build_doc_acl`
  ne câblait que `StaticDocACL` (JSON figé) et `GraphDocACL` (SharePoint). Un
  document **Fabric** hors du périmètre de l'appelant pouvait donc FUITER en
  citation (la garde existait, mais débranchée). Ce module ferme la brèche en
  exposant l'ACL Fabric sous l'interface synchrone `DocACL` attendue par le
  filtre, OR-mergeable avec les autres sources via `CompositeDocACL`.

Stratégie (identique au pattern `graph_acl.GraphDocACL`) :
  * Le filtre de citations est SYNCHRONE et par-document : `is_authorized(doc_id,
    principal)`. La décision Fabric est ASYNCHRONE et réseau. On résout donc, **au
    démarrage** (`build_fabric_acl`), pour CHAQUE doc du mapping
    ``{doc_id: {workspace_id, item_id, item_type}}`` :
      1. la garde GOLD (`item_in_gold_scope`) — un item hors gold est OMIS
         (donc deny-by-default au filtre, fail-closed) ;
      2. les **roleAssignments** du workspace → l'ensemble des principal ids et
         group ids qui détiennent un rôle de lecture (Viewer/Contributor/Member/
         Admin). On en fait une `_Entry(groups, users)`, exactement comme
         `StaticDocACL`/`GraphDocACL`.
  * `is_authorized` est alors une comparaison locale (pas de réseau dans le chemin
    requête) : l'appelant est autorisé s'il (ou l'un de ses groupes) figure dans
    l'`_Entry` du doc.

Discipline FAIL-CLOSED :
  * Fabric non configuré, mapping vide, item hors gold, roleAssignments illisibles,
    forme inattendue ⇒ le doc est **OMIS** de l'ACL → `is_authorized` renvoie
    `default_policy` (``deny`` par défaut). On n'invente JAMAIS d'accès.
  * Un `doc_id` ABSENT de l'ACL (non mappé, ou résolution échouée) ⇒ deny. En
    `CompositeDocACL`, ce deny est sans effet si une AUTRE source autorise
    (OR-merge) — la sémantique attendue (un doc SharePoint reste géré par Graph).

Honnêteté assumée (cf. `doc_acl.py`/`graph_acl.py`, docs/RBAC.md §4.4) : c'est un
filtre de SORTIE (quelles identités peuvent VOIR la citation), pas un contrôle au
niveau du stockage. Le zéro-fuite strict à la RECHERCHE reste Fabric/Onyx EE.

Réseau : via `FabricClient` (injectable → tests offline). Aucun secret journalisé.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Mapping, Optional

from .doc_acl import DocACL, _Entry, _PrincipalLike
from .fabric_acl import _READ_ROLES, _norm, item_in_gold_scope
from .fabric_client import FabricClient, FabricError

logger = logging.getLogger("onix.gateway.fabric_doc_acl")


# ───────────────────────────────────────────────────────────────────────────
# FabricDocACL — ACL par-document en mémoire (thread-safe), même sémantique de
# match que `StaticDocACL`/`GraphDocACL` (casse-insensible ; override user gagne).
# ───────────────────────────────────────────────────────────────────────────
class FabricDocACL(DocACL):
    """ACL par-document dérivée des permissions Microsoft **Fabric**.

    ``{doc_id: _Entry(groups, users)}`` où `groups`/`users` sont les principals
    Entra détenant un rôle de LECTURE sur le workspace de l'item (et l'item est
    dans le périmètre gold). Un `doc_id` absent suit `default_policy` (``deny``).
    Thread-safe : substitution atomique du dict d'entrées sous verrou (pour un
    éventuel rafraîchi par tâche de fond, comme `GraphDocACL`).
    """

    def __init__(
        self,
        entries: Optional[Mapping[str, _Entry]] = None,
        *,
        default_policy: str = "deny",
    ) -> None:
        if default_policy not in ("deny", "allow"):
            raise ValueError(f"default_policy invalide: {default_policy!r}")
        self._default_policy = default_policy
        self._lock = threading.RLock()
        self._entries: dict[str, _Entry] = dict(entries or {})

    @property
    def default_policy(self) -> str:
        return self._default_policy

    def refresh(self, entries: Mapping[str, _Entry]) -> None:
        """Remplace ATOMIQUEMENT le jeu d'entrées (lecteurs concurrents voient
        soit l'ancien, soit le nouveau dict — jamais un état partiel)."""
        new = dict(entries)
        with self._lock:
            self._entries = new

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def is_authorized(self, doc_id: str, principal: _PrincipalLike) -> bool:
        if not doc_id:
            return False
        with self._lock:
            entry = self._entries.get(str(doc_id).strip())
        if entry is None:
            # Doc non résolu (non mappé / gold-out / erreur amont) : deny-by-default.
            return self._default_policy == "allow"
        # 1) Override par utilisateur (UPN ou user_id/oid) — gagne sur le groupe.
        upn = (getattr(principal, "upn", None) or "").strip().lower()
        uid = (getattr(principal, "user_id", "") or "").strip().lower()
        if upn and upn in entry.users:
            return True
        if uid and uid in entry.users:
            return True
        # 2) Membre d'un groupe Entra détenant un rôle de lecture ?
        for gid in getattr(principal, "group_ids", []) or []:
            if (gid or "").strip().lower() in entry.groups:
                return True
        return False


# ───────────────────────────────────────────────────────────────────────────
# Construction depuis le mapping doc → item Fabric.
# ───────────────────────────────────────────────────────────────────────────
def _normalize_mapping_entry(spec: Any) -> Optional[tuple[str, str]]:
    """Valide une entrée de mapping Fabric et renvoie ``(workspace_id, item_id)``
    ou ``None`` si la forme est invalide (entrée ignorée → deny-by-default)."""
    if not isinstance(spec, dict):
        return None
    workspace_id = str(spec.get("workspace_id", "")).strip()
    item_id = str(spec.get("item_id", "")).strip()
    if not (workspace_id and item_id):
        return None
    return workspace_id, item_id


def _read_principals_from_assignments(
    assignments: Any,
) -> tuple[frozenset[str], frozenset[str]]:
    """Extrait des roleAssignments l'ensemble des principals détenant un rôle de
    LECTURE. On ne peut pas distinguer parfaitement user vs groupe (le `type`
    Fabric est un indice parfois absent) ; pour rester fail-closed **et** correct,
    on inscrit chaque principal id à la fois côté `users` ET côté `groups` : le
    match de `FabricDocACL.is_authorized` (oid OU appartenance de groupe) reste
    exact, et aucun accès n'est inventé (seuls les ids RÉELLEMENT assignés sont
    inscrits)."""
    ids: set[str] = set()
    if not isinstance(assignments, list):
        return frozenset(), frozenset()
    for a in assignments:
        if not isinstance(a, Mapping):
            continue
        role = a.get("role") or a.get("roleName")
        if _norm(role) not in _READ_ROLES:
            continue
        principal = a.get("principal")
        if not isinstance(principal, Mapping):
            continue
        pid = _norm(principal.get("id"))
        if pid:
            ids.add(pid)
    frozen = frozenset(ids)
    return frozen, frozen


async def build_fabric_acl(
    fabric: FabricClient,
    mapping: Mapping[str, Any],
    *,
    default_policy: str = "deny",
) -> FabricDocACL:
    """Construit un `FabricDocACL` depuis ``{doc_id: {workspace_id, item_id,
    item_type}}``.

    Pour CHAQUE doc : garde GOLD, puis lecture des roleAssignments du workspace →
    `_Entry`. Discipline **fail-CLOSED par doc** : Fabric non configuré, item hors
    gold, roleAssignments illisibles, forme invalide ⇒ doc **OMIS** (deny). Les
    roleAssignments d'un même workspace sont lus UNE seule fois et mémoïsés
    (perf + audit). Aucune exception ne remonte (un doc cassé ne prive pas les
    autres d'ACL). Renvoie une ACL vide si Fabric non configuré.
    """
    entries: dict[str, _Entry] = {}
    if not fabric.settings.fabric_configured:
        logger.info("fabric_doc_acl: Fabric non configuré → ACL vide (deny-by-default).")
        return FabricDocACL(entries={}, default_policy=default_policy)

    # Cache roleAssignments par workspace (lecture unique). Valeur :
    # (users, groups) OU None si la lecture a échoué (→ doc OMIS).
    ws_cache: dict[str, Optional[tuple[frozenset[str], frozenset[str]]]] = {}
    ok = 0
    failed = 0
    for doc_id, spec in mapping.items():
        if not isinstance(doc_id, str) or not doc_id.strip() or doc_id.startswith("_"):
            continue  # clés de métadonnées (_comment, _version…)
        pair = _normalize_mapping_entry(spec)
        if pair is None:
            failed += 1
            logger.warning(
                "fabric_doc_acl: entrée de mapping invalide pour '%s' "
                "(workspace_id/item_id requis) — doc OMIS (deny).",
                doc_id,
            )
            continue
        workspace_id, item_id = pair
        # Garde GOLD : un item hors périmètre gold n'est JAMAIS autorisé.
        if not item_in_gold_scope(fabric.settings, workspace_id, item_id):
            failed += 1
            logger.debug(
                "fabric_doc_acl: item hors périmètre GOLD pour '%s' — doc OMIS (deny).",
                doc_id,
            )
            continue
        # roleAssignments du workspace (mémoïsés).
        if workspace_id not in ws_cache:
            try:
                assignments = await fabric.list_workspace_role_assignments(workspace_id)
                ws_cache[workspace_id] = _read_principals_from_assignments(assignments)
            except FabricError as exc:
                # Jamais de secret/jeton ici — uniquement le type d'erreur.
                ws_cache[workspace_id] = None
                logger.warning(
                    "fabric_doc_acl: roleAssignments illisibles pour workspace '%s' "
                    "(%s) — docs de ce workspace OMIS (deny, fail-closed).",
                    workspace_id, exc,
                )
        resolved = ws_cache.get(workspace_id)
        if resolved is None:
            failed += 1
            continue
        users, groups = resolved
        entries[doc_id.strip()] = _Entry(groups=groups, users=users)
        ok += 1

    logger.info(
        "fabric_doc_acl: %d document(s) Fabric résolu(s), %d omis (deny) sur %d.",
        ok, failed, ok + failed,
    )
    return FabricDocACL(entries=entries, default_policy=default_policy)


def load_mapping(path: str) -> dict[str, Any]:
    """Charge le mapping ``{doc_id: {workspace_id, item_id, item_type}}`` depuis
    un JSON. Fichier absent / illisible → mapping VIDE (l'ACL sera vide → deny
    total : aucune citation Fabric autorisée, posture sûre). On NE lève PAS :
    cohérent avec `StaticDocACL.from_file` (la source Fabric est opt-in et ne doit
    pas faire tomber la passerelle si son mapping manque)."""
    import json
    import os

    if not path or not os.path.exists(path):
        logger.info(
            "fabric_doc_acl: mapping '%s' absent — ACL Fabric vide (deny total).", path
        )
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.error(
            "fabric_doc_acl: mapping '%s' illisible (%s) — ACL Fabric vide (deny total).",
            path, type(exc).__name__,
        )
        return {}
    if not isinstance(raw, dict):
        logger.error(
            "fabric_doc_acl: racine du mapping '%s' non-objet — ACL Fabric vide (deny).",
            path,
        )
        return {}
    return raw
