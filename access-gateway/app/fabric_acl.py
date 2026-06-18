"""fabric_acl — décision d'autorisation de LECTURE sur un item Microsoft Fabric,
**fail-closed**, dérivée des permissions réelles (chemin RÉPONSE, FOSS).

Pendant Fabric de `graph_acl.py` (SharePoint). Question posée :
  « Le principal P (utilisateur OU SPN, plus ses groupes Entra) peut-il LIRE
    l'item I du workspace W ? »

PÉRIMÈTRE GOLD (lecture seule). Avant même de regarder les rôles, on REFUSE tout
item hors du **périmètre gold** (le lakehouse gold du workspace gold, cf.
`item_in_gold_scope` / `fabric_client.is_gold_path`) : onix ne lit que la couche
gold, et seulement en lecture. Un item hors-gold ⇒ refus systématique (fail-closed),
quel que soit le rôle attribué.

Deux sources de vérité, dans l'ordre, **OR-mergées** mais toutes deux fail-closed :
  (a) **roleAssignments du workspace** (RBAC de CONTRÔLE Fabric) — un rôle Fabric
      qui confère AU MOINS la lecture (Viewer/Contributor/Member/Admin) attribué :
        * directement au principal (id ↔ `principal.id`), OU
        * à un GROUPE Entra dont le principal est membre (`principal.group_ids`).
      Référence rôles : voir `_READ_ROLES` ci-dessous (liste EXPLICITE et
      documentée, comme `graph_acl._READ_ROLES`).
  (b) **principalAccess OneLake** (securityPolicy, PREVIEW) — accès EFFECTIF fin
      par principal. S'il est disponible ET accorde la lecture, autorise. S'il est
      INDISPONIBLE (404 preview / 403) ou en erreur, on NE l'utilise PAS pour
      accorder (on retombe sur (a)). Jamais une erreur n'accorde un accès.

Discipline FAIL-CLOSED (identique `graph_acl`) :
  * Toute erreur d'appel, format inattendu, ou information manquante ⇒ **refus**
    (on n'invente pas d'accès sur une donnée qu'on n'a pas pu vérifier).
  * La source (a) est requise pour un « oui » par défaut ; (b) ne fait
    qu'ÉLARGIR (jamais restreindre un oui de (a), jamais accorder seule si elle a
    échoué). C'est le choix le plus sûr et il est documenté ici.

Honnêteté assumée (comme tout le RBAC FOSS onix, cf. docs/RBAC.md §4.4) : c'est un
filtre de SORTIE (quelles identités peuvent VOIR un item/sa citation), pas un
contrôle au niveau du stockage. Le zéro-fuite strict à la RECHERCHE reste Fabric/
Onyx EE. Ce module rend le filtre automatique, il ne change PAS sa nature.

Réseau : `httpx` via `FabricClient` (injectable → tests offline). On ne journalise
JAMAIS jeton ni corps de réponse.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, Optional

from .config import Settings
from .fabric_client import FabricClient, FabricError, is_gold_path

logger = logging.getLogger("onix.gateway.fabric_acl")

# Rôles Fabric (workspace roleAssignments) qui confèrent AU MOINS la lecture.
# Un rôle hors de cet ensemble (ou inconnu) n'accorde AUCUNE visibilité. Les rôles
# Fabric officiels sont : Admin, Member, Contributor, Viewer — les quatre donnent
# au moins la lecture des items du workspace. (Comparaison casse-insensible.)
_READ_ROLES = frozenset({"admin", "member", "contributor", "viewer"})


def _norm(value: Any) -> str:
    """Normalise un identifiant pour comparaison casse-insensible (cohérent
    `graph_acl` : strip + lower). Valeurs non-str → chaîne vide."""
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def item_in_gold_scope(settings: Settings, workspace_id: str, item_id: str) -> bool:
    """`True` si (workspace_id, item_id) appartient au **périmètre GOLD**.

    Pendant ACL de `fabric_client.is_gold_path` au niveau ITEM (sans chemin) : la
    décision de lecture n'est accordée QUE pour le lakehouse gold du workspace gold.
    L'item peut être référencé par GUID (item_type vide) ou par nom — `is_gold_path`
    gère les deux. Fail-closed : gold non configuré ⇒ `False`."""
    return is_gold_path(settings, workspace_id, item_id, "", "")


def _role_grants_read(role: Any) -> bool:
    """`True` si le nom de rôle confère au moins la lecture."""
    return _norm(role) in _READ_ROLES


def _assignment_principal(assignment: Mapping[str, Any]) -> tuple[str, str]:
    """Extrait ``(principal_id, principal_type)`` d'un roleAssignment Fabric.

    Forme attendue (Fabric REST) :
      ``{"principal": {"id": "...", "type": "User"|"Group"|"ServicePrincipal"...},
         "role": "Viewer"}``.
    Champs manquants tolérés → chaînes vides (l'appelant les ignore)."""
    principal = assignment.get("principal")
    if not isinstance(principal, Mapping):
        return "", ""
    return _norm(principal.get("id")), _norm(principal.get("type"))


def _assignment_role(assignment: Mapping[str, Any]) -> Any:
    """Récupère le nom de rôle d'un roleAssignment, en tolérant les deux formes
    rencontrées : ``role`` (chaîne) OU ``roleName`` (alias)."""
    return assignment.get("role") or assignment.get("roleName")


def principal_has_read_role(
    assignments: Iterable[Mapping[str, Any]],
    principal_id: str,
    principal_group_ids: Iterable[str],
) -> bool:
    """Décision PURE (sans réseau) : un des `assignments` confère-t-il la lecture
    au principal (directement) OU à l'un de ses groupes ?

    Isolée et testable séparément (cœur de la logique RBAC de contrôle). Tout
    `assignment` malformé est ignoré (n'accorde rien) — fail-closed.
    """
    pid = _norm(principal_id)
    group_set = {_norm(g) for g in (principal_group_ids or []) if _norm(g)}
    for assignment in assignments:
        if not isinstance(assignment, Mapping):
            continue
        if not _role_grants_read(_assignment_role(assignment)):
            continue
        aid, _atype = _assignment_principal(assignment)
        if not aid:
            continue
        # Attribution directe au principal.
        if pid and aid == pid:
            return True
        # Attribution à un GROUPE dont le principal est membre. L'appartenance
        # (id ∈ group_set) fait foi : le `type` du principal n'est qu'un indice
        # (parfois absent), donc on ne s'en sert pas pour refuser un groupe avéré.
        if aid in group_set:
            return True
    return False


def _onelake_access_grants_read(access: Mapping[str, Any]) -> bool:
    """Interprète une réponse `principalAccess` OneLake (PREVIEW) → lecture ?

    Le schéma preview varie ; on cherche, de façon DÉFENSIVE, un signal explicite
    de lecture parmi les formes connues, et on REFUSE par défaut (fail-closed) si
    rien de clair n'est trouvé :
      * ``access.hasAccess`` booléen (forme simple), OU
      * une liste ``access.actions`` / ``access.accessActions`` contenant une
        action de lecture (``read`` / ``*`` / ``*.read``), OU
      * ``access.effectivePermissions`` listant un rôle de lecture.
    Toute forme inconnue ⇒ `False` (on n'accorde pas sur un signal ambigu).
    """
    if not isinstance(access, Mapping):
        return False
    # Forme booléenne directe.
    if access.get("hasAccess") is True:
        return True
    # Listes d'actions explicites.
    read_tokens = {"read", "*", "*.read", "microsoft.fabric/read"}
    for key in ("actions", "accessActions", "permittedActions"):
        actions = access.get(key)
        if isinstance(actions, list):
            for act in actions:
                if _norm(act) in read_tokens:
                    return True
    # Rôles effectifs.
    perms = access.get("effectivePermissions") or access.get("roles")
    if isinstance(perms, list):
        for p in perms:
            if _role_grants_read(p):
                return True
    return False


async def can_principal_read(
    principal_id: str,
    workspace_id: str,
    item_id: str,
    *,
    fabric: FabricClient,
    principal_group_ids: Optional[Iterable[str]] = None,
    use_onelake_effective_access: bool = True,
) -> bool:
    """Le principal peut-il LIRE l'item du workspace ? Renvoie un `bool`,
    **fail-closed** (tout doute / toute erreur ⇒ `False`).

    Algorithme (OR des sources, chacune sûre) :
      1. Pré-conditions : ids non vides, Fabric configuré — sinon `False`.
      2. **roleAssignments** du workspace : si un rôle de lecture est attribué au
         principal ou à l'un de ses groupes ⇒ `True`. Une erreur de lecture des
         assignments est traitée comme « pas d'accès via (a) » (on tente (b) si
         demandé, mais ne renvoie jamais True par erreur).
      3. **principalAccess OneLake** (si `use_onelake_effective_access`) : source
         d'élargissement. Disponible ET lecture accordée ⇒ `True`. Indisponible
         (404 preview / 403) ou erreur ⇒ ignorée (ne fait pas pencher vers True).
      4. Aucune source n'a accordé ⇒ `False`.

    Aucune exception ne « fuit » : on capte `FabricError` et on continue/refuse.
    """
    if not (principal_id and workspace_id and item_id):
        logger.debug("fabric_acl: id manquant (principal/workspace/item) → refus.")
        return False
    if not fabric.settings.fabric_configured:
        logger.debug("fabric_acl: Fabric non configuré → refus (fail-closed).")
        return False
    # Gold-only : on n'accorde JAMAIS la lecture d'un item hors du périmètre gold
    # (le lakehouse gold du workspace gold), même si un rôle l'autoriserait.
    if not item_in_gold_scope(fabric.settings, workspace_id, item_id):
        logger.debug("fabric_acl: item hors périmètre GOLD → refus (fail-closed).")
        return False

    groups = list(principal_group_ids or [])

    # (a) RBAC de contrôle : roleAssignments du workspace.
    try:
        assignments = await fabric.list_workspace_role_assignments(workspace_id)
        if principal_has_read_role(assignments, principal_id, groups):
            return True
    except FabricError as exc:
        # Jamais de secret/jeton ici — uniquement le type d'erreur.
        logger.warning(
            "fabric_acl: lecture roleAssignments échouée pour workspace '%s' (%s) "
            "— pas d'accès via le RBAC de contrôle (fail-closed).",
            workspace_id, exc,
        )

    # (b) Accès effectif OneLake (PREVIEW) — élargissement opportuniste, sûr.
    if use_onelake_effective_access:
        try:
            access = await fabric.get_principal_effective_access(
                workspace_id, item_id, principal_id
            )
            if _onelake_access_grants_read(access):
                return True
        except FabricError as exc:
            # Preview souvent indisponible (404) ou SPN non habilité (403) : on ne
            # journalise qu'en debug pour ne pas polluer (cas attendu).
            logger.debug(
                "fabric_acl: principalAccess OneLake indisponible/erreur pour "
                "item '%s' (%s) — source ignorée (fail-closed).",
                item_id, exc,
            )

    # Aucune source n'a accordé la lecture → refus.
    return False


async def authorized_items(
    principal_id: str,
    workspace_id: str,
    candidate_item_ids: Iterable[str],
    *,
    fabric: FabricClient,
    principal_group_ids: Optional[Iterable[str]] = None,
    use_onelake_effective_access: bool = True,
) -> set[str]:
    """Sous-ensemble de `candidate_item_ids` que le principal peut LIRE.

    Optimisation : les roleAssignments du workspace (source (a)) sont lus UNE
    SEULE fois et réutilisés pour tous les items du même workspace (ils sont
    portés au niveau workspace, pas item). La source (b) OneLake reste par-item.
    Fail-closed : un item dont la vérification échoue n'est PAS inclus.
    """
    result: set[str] = set()
    if not (principal_id and workspace_id):
        return result
    if not fabric.settings.fabric_configured:
        return result

    groups = list(principal_group_ids or [])

    # (a) une seule lecture des assignments → décision workspace-level.
    workspace_grants_read = False
    try:
        assignments = await fabric.list_workspace_role_assignments(workspace_id)
        workspace_grants_read = principal_has_read_role(assignments, principal_id, groups)
    except FabricError as exc:
        logger.warning(
            "fabric_acl: roleAssignments illisibles pour workspace '%s' (%s) "
            "— décision (a) = refus (fail-closed).",
            workspace_id, exc,
        )

    for item_id in candidate_item_ids:
        if not item_id:
            continue
        # Gold-only : un item hors périmètre gold n'est JAMAIS inclus.
        if not item_in_gold_scope(fabric.settings, workspace_id, item_id):
            continue
        if workspace_grants_read:
            result.add(item_id)
            continue
        # Sinon, tenter l'élargissement OneLake par item (sûr).
        if use_onelake_effective_access:
            try:
                access = await fabric.get_principal_effective_access(
                    workspace_id, item_id, principal_id
                )
                if _onelake_access_grants_read(access):
                    result.add(item_id)
            except FabricError:
                # Item non vérifiable → non inclus (fail-closed).
                continue
    return result
