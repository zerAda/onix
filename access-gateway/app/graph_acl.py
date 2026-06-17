"""graph_acl — ACL par-document **auto-dérivée** des permissions SharePoint réelles
via Microsoft Graph (chemin RÉPONSE, FOSS).

Pourquoi ce module :
  `doc_acl.py` fournit un `StaticDocACL` chargé d'un JSON figé (`doc_acl.json`).
  C'était la seule source d'ACL par-document en FOSS, et elle devait être
  maintenue **à la main**. Ce module ferme la dernière réserve « RBAC par
  document = EE » **autant que le FOSS le permet** : il interroge les
  permissions **par item** d'un drive SharePoint (qui ↔ groupe ↔ document) et en
  construit une ACL **vivante**, OR-mergeable avec le statique via
  `CompositeDocACL` (cf. `doc_acl.py`).

Honnêteté assumée (identique à `doc_acl.py`, à relire — `docs/RBAC.md` §4.4) :
  * Cela reste un **filtre de SORTIE**. On synchronise *quelles identités peuvent
    VOIR* un document (donc sa citation), pas *ce que le LLM a récupéré* à la
    génération. Le « zéro-fuite » strict à la RECHERCHE demeure Onyx EE
    (permission sync) ou des instances séparées par tier d'accès. Ce module rend
    le filtre de sortie **automatique** (plus de JSON manuel), il ne change PAS
    sa nature.

Le maillon dur — `doc_id ↔ item SharePoint` :
  Un `doc_id` Onyx doit être relié à un item SharePoint `(site_id, drive_id,
  item_id)` pour qu'on puisse lire ses permissions. Onyx stocke l'URL source /
  l'id de drive-item dans les **métadonnées** du document (connecteur SharePoint).
  Ce module **ne devine pas** ce lien : il consomme un **mapping explicite**
  `{onyx_doc_id: {site_id, drive_id, item_id}}` (cf. `scripts/sync-doc-acl.py` et
  `docs/connectors/SHAREPOINT.md` pour l'obtenir). Aucune magie.

Endpoint Graph (APPLICATION, app-only — réutilise l'auth de `graph_client.py`) :
  GET /v1.0/sites/{site-id}/drives/{drive-id}/items/{item-id}/permissions
  Permission applicative requise : **Sites.Read.All** (ou `Sites.Selected` +
  octroi par site), avec **admin consent**. `Files.Read.All` fonctionne aussi
  mais est plus large. On lit chaque `permission` :
    * `roles`           — rôles accordés (read/write/owner…) ; un item SANS rôle
                          de lecture effectif n'accorde rien.
    * `grantedToV2`     — identité unique bénéficiaire (forme courante v1.0) :
        - `.user.id`       → objectId utilisateur  → set USERS
        - `.group.id`      → objectId groupe Entra → set GROUPS
        - `.siteGroup.id`  → id de groupe SharePoint (membres SP) → set GROUPS
    * `grantedToIdentitiesV2` — variante LISTE (partages multiples) ; même parse.
    * `inheritedFrom`   — présent si la permission est **héritée** d'un parent ;
                          on l'inclut quand même (un héritage de lecture donne
                          bien l'accès). On ignore juste les permissions de
                          **lien** anonymes/organisation (pas d'identité).

Réseau : `httpx` uniquement (aucun SDK lourd). Le `graph` passé en paramètre est
un petit adaptateur (cf. `GraphSession`) qui encapsule client + token, exactement
comme `graph_client.fetch_transitive_group_ids`. **Injectable → tests offline.**
Secrets : le jeton vient de `graph_client.acquire_app_token` (env only) ; on ne
journalise JAMAIS ni jeton ni corps de réponse.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional

import httpx

from .config import Settings
from .doc_acl import DocACL, _Entry, _PrincipalLike
from .graph_client import GraphError, acquire_app_token

logger = logging.getLogger("onix.gateway.graph_acl")

# Rôles SharePoint/OneDrive qui confèrent au MOINS la lecture. Un rôle hors de
# cet ensemble (vide, ou inconnu) n'accorde aucune visibilité de citation.
_READ_ROLES = frozenset({"read", "write", "owner", "sp.full control", "sp.read", "sp.contribute"})


# ───────────────────────────────────────────────────────────────────────────
# GraphSession — petit adaptateur "client + token" injectable.
#   Réutilise EXACTEMENT le pattern de graph_client : un client httpx partagé +
#   un fournisseur de jeton app-only. Les tests passent un faux `get` / `token`.
# ───────────────────────────────────────────────────────────────────────────
TokenProvider = Callable[[], Awaitable[str]]


@dataclass
class GraphSession:
    """Encapsule l'accès Graph pour la lecture d'ACL : un client httpx, un
    fournisseur de jeton, et l'hôte Graph (souverain : Gov/China possibles).

    On garde l'objet **minimal et injectable** : un test fournit un client à
    transport moqué (`httpx.MockTransport`) + un `token_provider` constant, donc
    AUCUN appel réseau réel."""

    client: httpx.AsyncClient
    settings: Settings
    token_provider: Optional[TokenProvider] = None
    _cached_token: Optional[str] = None

    async def token(self) -> str:
        """Jeton app-only (mémoïsé sur la durée de vie de la session : un sync
        complet réutilise le même jeton plutôt que de le re-demander par item)."""
        if self.token_provider is not None:
            return await self.token_provider()
        if self._cached_token is None:
            self._cached_token = await acquire_app_token(self.settings, self.client)
        return self._cached_token

    async def get_json(self, url: str) -> dict[str, Any]:
        """GET authentifié → JSON. Lève `GraphError` sur statut non-200 (sans
        jamais journaliser le corps : il peut contenir des détails sensibles)."""
        headers = {
            "Authorization": f"Bearer {await self.token()}",
            "Accept": "application/json",
        }
        resp = await self.client.get(url, headers=headers)
        if resp.status_code != 200:
            raise GraphError(
                f"Échec lecture permissions Graph (HTTP {resp.status_code})."
            )
        return resp.json()


# ───────────────────────────────────────────────────────────────────────────
# Parsing d'une permission Graph → (users, groups).
# ───────────────────────────────────────────────────────────────────────────
def _principals_from_identity_set(identity_set: Any) -> tuple[set[str], set[str]]:
    """Extrait (users, groups) d'un *identitySet* Graph (`grantedToV2` ou un
    élément de `grantedToIdentitiesV2`). Champs manquants tolérés (→ sets vides).

    * `user.id`      → objectId utilisateur (USERS)
    * `group.id`     → objectId groupe Entra (GROUPS)
    * `siteGroup.id` → id de groupe SharePoint (GROUPS) — membres SP du site
    """
    users: set[str] = set()
    groups: set[str] = set()
    if not isinstance(identity_set, dict):
        return users, groups
    user = identity_set.get("user")
    if isinstance(user, dict):
        uid = user.get("id")
        if uid:
            users.add(str(uid).strip().lower())
    group = identity_set.get("group")
    if isinstance(group, dict):
        gid = group.get("id")
        if gid:
            groups.add(str(gid).strip().lower())
    site_group = identity_set.get("siteGroup")
    if isinstance(site_group, dict):
        sgid = site_group.get("id")
        if sgid:
            groups.add(str(sgid).strip().lower())
    return users, groups


def _permission_grants_read(perm: Mapping[str, Any]) -> bool:
    """`True` si la permission confère au moins la lecture. Une permission sans
    `roles` exploitable (lien anonyme p.ex.) n'accorde aucune visibilité ici."""
    roles = perm.get("roles")
    if not isinstance(roles, list):
        return False
    for r in roles:
        if isinstance(r, str) and r.strip().lower() in _READ_ROLES:
            return True
    return False


async def fetch_item_principals(
    graph: GraphSession,
    site_id: str,
    drive_id: str,
    item_id: str,
) -> tuple[set[str], set[str]]:
    """Renvoie ``(users, groups)`` autorisés en LECTURE sur l'item SharePoint.

    Appelle ``GET /sites/{site-id}/drives/{drive-id}/items/{item-id}/permissions``
    et agrège, sur **toutes** les permissions de lecture (directes OU héritées) :
      * les objectIds utilisateur (`grantedToV2.user.id`)        → ``users``
      * les objectIds groupe Entra (`grantedToV2.group.id`)      → ``groups``
      * les ids de groupe SharePoint (`grantedToV2.siteGroup.id`)→ ``groups``
    Gère aussi `grantedToIdentitiesV2` (forme LISTE des partages multiples) et
    suit la pagination `@odata.nextLink`. Identités lowercased (comparaison
    casse-insensible cohérente avec `StaticDocACL`).

    Lève `GraphError` sur erreur d'appel (statut non-200) — l'appelant
    (`build_graph_acl`) la capte et OMET l'item (deny sous default deny).
    """
    if not (site_id and drive_id and item_id):
        raise GraphError("fetch_item_principals: site_id/drive_id/item_id requis.")
    url: Optional[str] = (
        f"{graph.settings.graph_host}/v1.0/sites/{site_id}"
        f"/drives/{drive_id}/items/{item_id}/permissions"
    )
    users: set[str] = set()
    groups: set[str] = set()
    pages = 0
    while url:
        body = await graph.get_json(url)
        for perm in body.get("value", []):
            if not isinstance(perm, dict):
                continue
            if not _permission_grants_read(perm):
                continue
            # Forme unique (v1.0 courante).
            u, g = _principals_from_identity_set(perm.get("grantedToV2"))
            users |= u
            groups |= g
            # Forme liste (partages multiples).
            granted_list = perm.get("grantedToIdentitiesV2")
            if isinstance(granted_list, list):
                for ident in granted_list:
                    u, g = _principals_from_identity_set(ident)
                    users |= u
                    groups |= g
        url = body.get("@odata.nextLink")
        pages += 1
        if pages > 1000:  # garde-fou anti-boucle (cohérent graph_client)
            logger.warning("Pagination permissions anormalement longue : interruption.")
            break
    return users, groups


# ───────────────────────────────────────────────────────────────────────────
# GraphDocACL — ACL par-document en mémoire, thread-safe, avec TTL.
# ───────────────────────────────────────────────────────────────────────────
# Horloge injectable (tests) — par défaut time.monotonic (insensible aux sauts
# d'horloge système, cohérent avec identity._TTLCache).
Clock = Callable[[], float]


class GraphDocACL(DocACL):
    """ACL par-document construite à partir de Graph (``{doc_id: _Entry}``).

    * **Même sémantique de match que `StaticDocACL`** : casse-insensible sur
      groupes ; override par utilisateur (UPN OU oid) gagne sur le groupe.
    * **Thread-safe** : le dict d'entrées est remplacé atomiquement sous verrou
      (`refresh`), les lectures prennent un snapshot. Adapté à un rafraîchi par
      tâche de fond pendant que des requêtes lisent.
    * **TTL** : `is_stale()` indique si le contenu a dépassé `ttl_seconds`
      (l'orchestrateur décide de relancer un `build_graph_acl` → `refresh`). Une
      horloge est **injectable** pour les tests (`clock=`).
    * `default_policy` : un `doc_id` ABSENT de l'ACL Graph suit cette politique
      (``deny`` par défaut — cohérent passerelle). En `CompositeDocACL`, ce deny
      est sans effet si une AUTRE source autorise (OR-merge).
    """

    def __init__(
        self,
        entries: Optional[Mapping[str, _Entry]] = None,
        *,
        default_policy: str = "deny",
        ttl_seconds: int = 900,
        clock: Optional[Clock] = None,
    ) -> None:
        if default_policy not in ("deny", "allow"):
            raise ValueError(f"default_policy invalide: {default_policy!r}")
        self._default_policy = default_policy
        self._ttl = ttl_seconds
        self._clock: Clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._entries: dict[str, _Entry] = dict(entries or {})
        # `None` tant qu'aucun refresh n'a eu lieu → considéré périmé (force un
        # premier build). Sinon, instant (horloge) du dernier refresh.
        self._loaded_at: Optional[float] = None if entries is None else self._clock()

    @property
    def default_policy(self) -> str:
        return self._default_policy

    def refresh(self, entries: Mapping[str, _Entry]) -> None:
        """Remplace ATOMIQUEMENT le jeu d'entrées et réarme le TTL. Les lecteurs
        concurrents voient soit l'ancien, soit le nouveau dict — jamais un état
        partiel (on substitue la référence sous verrou)."""
        new = dict(entries)
        with self._lock:
            self._entries = new
            self._loaded_at = self._clock()

    def is_stale(self) -> bool:
        """`True` si jamais chargé, ou si le TTL est dépassé. `ttl<=0` ⇒ jamais
        périmé (rafraîchi désactivé : ACL gérée à la main / au démarrage)."""
        with self._lock:
            if self._loaded_at is None:
                return True
            if self._ttl <= 0:
                return False
            return (self._clock() - self._loaded_at) >= self._ttl

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def is_authorized(self, doc_id: str, principal: _PrincipalLike) -> bool:
        if not doc_id:
            return False
        with self._lock:
            entry = self._entries.get(str(doc_id).strip())
        if entry is None:
            return self._default_policy == "allow"
        # 1) Override par utilisateur (UPN ou user_id/oid) — gagne sur le groupe.
        upn = (getattr(principal, "upn", None) or "").strip().lower()
        uid = (getattr(principal, "user_id", "") or "").strip().lower()
        if upn and upn in entry.users:
            return True
        if uid and uid in entry.users:
            return True
        # 2) Membre d'un groupe (Entra OU SharePoint) autorisé ?
        for gid in getattr(principal, "group_ids", []) or []:
            if (gid or "").strip().lower() in entry.groups:
                return True
        return False


# ───────────────────────────────────────────────────────────────────────────
# build_graph_acl — itère le mapping doc→item et construit l'ACL.
# ───────────────────────────────────────────────────────────────────────────
def _normalize_mapping_entry(spec: Any) -> Optional[tuple[str, str, str]]:
    """Valide une entrée de mapping et renvoie ``(site_id, drive_id, item_id)``
    ou ``None`` si la forme est invalide (entrée ignorée par l'appelant)."""
    if not isinstance(spec, dict):
        return None
    site_id = str(spec.get("site_id", "")).strip()
    drive_id = str(spec.get("drive_id", "")).strip()
    item_id = str(spec.get("item_id", "")).strip()
    if not (site_id and drive_id and item_id):
        return None
    return site_id, drive_id, item_id


async def build_graph_acl(
    graph: GraphSession,
    mapping: Mapping[str, Any],
    *,
    default_policy: str = "deny",
    ttl_seconds: int = 900,
    clock: Optional[Clock] = None,
) -> GraphDocACL:
    """Construit un `GraphDocACL` depuis un mapping ``{doc_id: {site_id, drive_id,
    item_id}}``.

    Pour CHAQUE document du mapping, on lit les permissions de l'item et on en
    fait une `_Entry(groups, users)`. Discipline **fail-CLOSED par item** : si la
    lecture d'un item ÉCHOUE (GraphError, forme invalide), le document est
    **OMIS** de l'ACL Graph → sous `default_policy=deny`, il est **refusé** (on
    n'invente pas d'accès sur une donnée qu'on n'a pas pu vérifier). L'erreur est
    journalisée (sans secret) pour investigation, mais NE FAIT PAS échouer le
    sync global (un item cassé ne doit pas priver les autres d'ACL).

    Renvoie un `GraphDocACL` prêt à l'emploi (déjà « chargé » → non périmé).
    """
    entries: dict[str, _Entry] = {}
    ok = 0
    failed = 0
    for doc_id, spec in mapping.items():
        if not isinstance(doc_id, str) or not doc_id.strip() or doc_id.startswith("_"):
            # Clés de métadonnées (_version, _comment…) ignorées.
            continue
        triple = _normalize_mapping_entry(spec)
        if triple is None:
            failed += 1
            logger.warning(
                "graph_acl: entrée de mapping invalide pour '%s' (site_id/drive_id/"
                "item_id requis) — document OMIS (deny sous default deny).",
                doc_id,
            )
            continue
        site_id, drive_id, item_id = triple
        try:
            users, groups = await fetch_item_principals(graph, site_id, drive_id, item_id)
        except GraphError as exc:
            failed += 1
            # Jamais de secret/jeton ici — uniquement le doc_id et le type d'erreur.
            logger.warning(
                "graph_acl: lecture permissions échouée pour '%s' (%s) — document "
                "OMIS (deny sous default deny).",
                doc_id, exc,
            )
            continue
        entries[doc_id.strip()] = _Entry(
            groups=frozenset(groups), users=frozenset(users)
        )
        ok += 1
    logger.info(
        "graph_acl: %d document(s) synchronisé(s), %d en échec/omis (sur %d).",
        ok, failed, ok + failed,
    )
    return GraphDocACL(
        entries=entries,
        default_policy=default_policy,
        ttl_seconds=ttl_seconds,
        clock=clock,
    )


def load_mapping(path: str) -> dict[str, Any]:
    """Charge un mapping ``{doc_id: {site_id, drive_id, item_id}}`` depuis un
    JSON. Fichier absent / illisible → `GraphError` (fail-loud côté outillage :
    un sync sans mapping est une erreur d'exploitation, pas un silence)."""
    import json
    import os

    if not path or not os.path.exists(path):
        raise GraphError(f"graph_acl: mapping introuvable: {path!r}.")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise GraphError(f"graph_acl: mapping illisible ({type(exc).__name__}).") from exc
    if not isinstance(raw, dict):
        raise GraphError("graph_acl: la racine du mapping doit être un objet JSON.")
    return raw


def entries_to_acl_obj(acl: GraphDocACL) -> dict[str, dict[str, list[str]]]:
    """Sérialise un `GraphDocACL` au format `doc_acl.json` (objet
    ``{doc_id: {"groups": [...], "users": [...]}}``) — pour que le CLI
    `sync-doc-acl.py` écrive un fichier lisible **sans changement de code** par
    `StaticDocACL.from_file`. Listes triées (sortie déterministe / diff propre)."""
    out: dict[str, dict[str, list[str]]] = {}
    with acl._lock:  # noqa: SLF001 — sérialisation interne maîtrisée
        items = list(acl._entries.items())
    for doc_id, entry in sorted(items):
        out[doc_id] = {
            "groups": sorted(entry.groups),
            "users": sorted(entry.users),
        }
    return out
