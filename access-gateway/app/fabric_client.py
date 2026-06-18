"""fabric_client — client Microsoft Fabric / OneLake / Power BI minimal (app-only).

Pourquoi ce module :
  La passerelle onix fait déjà le RBAC SharePoint par-document via Microsoft Graph
  (`graph_client.py` / `graph_acl.py`). Ce module ouvre la MÊME approche pour
  **Microsoft Fabric** : énumérer les workspaces / items, lire les attributions de
  rôles (RBAC de contrôle), lister/lire les fichiers OneLake (couche données ADLS
  Gen2) et interroger l'accès EFFECTIF d'un principal (securityPolicy OneLake,
  PREVIEW), ainsi que les datasets Power BI. C'est la brique « lecture » sur
  laquelle `fabric_acl.py` construit une décision d'autorisation **fail-closed**.

Trois AUDIENCES de jeton (client credentials) — chacune sa surface :
  * Contrôle Fabric       : ``https://api.fabric.microsoft.com/.default``
  * Données OneLake (ADLS): ``https://storage.azure.com/.default``
  * Power BI / datasets   : ``https://analysis.windows.net/powerbi/api/.default``
On acquiert le jeton PAR audience (le SPN doit avoir le scope adéquat). Le
fournisseur de jeton est **injectable** (comme `graph_client.acquire_app_token`)
→ tests 100 % offline (aucun appel réseau réel).

Contraintes réelles (gérées proprement → erreurs typées, jamais de 500 muet) :
  * Le SPN doit être autorisé au niveau tenant (« Service principals can use
    Fabric APIs ») ET ajouté à un rôle du workspace, sinon 401/403.
  * OneLake securityPolicy/principalAccess est en **PREVIEW** : indisponible (404)
    sur certains tenants → l'appelant ACL doit dégrader sans accorder d'accès.

Pagination — chaque API a SA forme (respectée exactement) :
  * Fabric REST (contrôle) : ``continuationToken`` / ``continuationUri`` dans le
    corps JSON.
  * OData (Power BI legacy) : ``@odata.nextLink``.
  * ADLS Gen2 / OneLake DFS : en-tête de réponse ``x-ms-continuation`` (rejoué en
    query ``continuation=``).

Sécurité : hôtes CONSTANTS (issus des Settings, pas d'URL utilisateur arbitraire),
TLS vérifié par défaut (httpx), on ne journalise JAMAIS le jeton ni le corps de
réponse (peut contenir des données sensibles). stdlib + httpx uniquement.

**LECTURE SEULE PAR CONCEPTION (read-only by design).** Ce module n'émet QUE des
requêtes GET : il n'écrit, ne crée, ne modifie et ne supprime JAMAIS rien sur
Fabric / OneLake / Power BI. Aucune méthode POST/PUT/PATCH/DELETE n'existe ni ne
doit être ajoutée. De plus, l'accès OneLake (données) est restreint au **périmètre
GOLD** (un lakehouse précis, sous-arbre `Tables` gold) : voir `is_gold_path` —
tout chemin hors-gold est REFUSÉ (fail-closed).
"""
from __future__ import annotations

import json
import logging
import re
# Import subprocess : utilisé UNIQUEMENT pour `az` avec une liste d'arguments FIXE
# (jamais shell=True, jamais de chaîne interpolée). Cf. acquire_token_via_azcli.
import subprocess  # nosec B404
from typing import Any, Awaitable, Callable, Optional, Union

import httpx

from .config import Settings

logger = logging.getLogger("onix.gateway.fabric")

# Audiences (ressources OAuth2) — le scope est ``<audience>/.default``.
AUDIENCE_FABRIC = "https://api.fabric.microsoft.com"
AUDIENCE_STORAGE = "https://storage.azure.com"
AUDIENCE_POWERBI = "https://analysis.windows.net/powerbi/api"
# Audience Microsoft Graph (utile au provider az pour la résolution de groupes).
AUDIENCE_GRAPH = "https://graph.microsoft.com"

# Timeout (s) du sous-processus `az` (acquisition de jeton). Borné pour ne jamais
# bloquer indéfiniment si la CLI attend une interaction.
_AZCLI_TIMEOUT = 30.0

# Garde-fou anti-boucle de pagination (cohérent graph_client.py).
_MAX_PAGES = 1000

# Timeout par défaut des appels (cohérent graph_client.py : 15s).
_DEFAULT_TIMEOUT = httpx.Timeout(15.0)

# Détection d'un GUID (forme d'adressage OneLake par identifiant). En forme GUID,
# la convention OneLake est `/{wsGUID}/{itemGUID}/...` SANS suffixe `.itemtype`
# (le suffixe `.type` n'est valide qu'en adressage par NOM). Mélanger les deux
# (`{GUID}.Lakehouse`) provoque un HTTP 400. Cf. docs OneLake « URI syntax ».
_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _is_guid(value: str) -> bool:
    """Vrai si `value` est un GUID (adressage OneLake par identifiant)."""
    return bool(_GUID_RE.match((value or "").strip()))

# Type d'un fournisseur de jeton injectable. Reçoit l'audience demandée et
# renvoie un access_token brut. (Variante « consciente de l'audience » du
# TokenProvider de graph_client : un même SPN obtient des jetons différents
# selon la ressource.)
TokenProvider = Callable[[str], Awaitable[str]]


class FabricError(RuntimeError):
    """Erreur d'appel Fabric/OneLake/Power BI (jeton ou requête)."""


async def acquire_token(
    settings: Settings,
    client: httpx.AsyncClient,
    audience: str,
) -> str:
    """Acquiert un jeton app-only (client credentials) pour ``audience``.

    Réutilise les identifiants Entra de la passerelle (tenant/client/secret), comme
    `graph_client.acquire_app_token`, mais le **scope dépend de l'audience** : un
    appel Fabric, OneLake et Power BI ne demandent PAS la même ressource. Lève
    `FabricError` si Fabric n'est pas configuré ou si l'acquisition échoue.

    Le corps de réponse n'est JAMAIS journalisé (peut contenir des détails
    sensibles) ; on ne remonte que le code HTTP.
    """
    if not settings.fabric_configured:
        raise FabricError(
            "Microsoft Fabric non configuré (tenant/client/secret Entra manquants)."
        )
    if not audience:
        raise FabricError("acquire_token: audience requise.")
    token_url = (
        f"{settings.fabric_authority}/{settings.fabric_tenant_id}/oauth2/v2.0/token"
    )
    data = {
        "client_id": settings.fabric_client_id,
        "client_secret": settings.fabric_client_secret,
        "scope": f"{audience.rstrip('/')}/.default",
        "grant_type": "client_credentials",
    }
    resp = await client.post(token_url, data=data)
    if resp.status_code != 200:
        # Ne jamais logguer le corps (peut contenir des détails sensibles).
        raise FabricError(
            f"Échec d'acquisition du jeton Fabric (HTTP {resp.status_code}, "
            f"audience {audience})."
        )
    token = resp.json().get("access_token")
    if not token:
        raise FabricError("Réponse de jeton Fabric sans access_token.")
    return token


def acquire_token_via_azcli(
    resource: str,
    *,
    tenant: Optional[str] = None,
    runner: Optional[Callable[[list[str]], str]] = None,
) -> str:
    """Acquiert un jeton d'accès via **Azure CLI** (`az`) — zéro secret en repo.

    Exécute ``az account get-access-token --resource {resource} --output json`` et
    renvoie l'``accessToken``. L'identité provient de ``az login`` (utilisateur ou
    identité managée) : AUCUN client_secret n'est requis ni stocké. Les ressources
    par audience sont : ``https://api.fabric.microsoft.com``,
    ``https://storage.azure.com``, ``https://analysis.windows.net/powerbi/api``,
    ``https://graph.microsoft.com``.

    Sécurité (bandit B602/B603) : on appelle ``subprocess`` avec une **liste
    d'arguments FIXE** (jamais ``shell=True``, jamais une chaîne) — `resource` et
    `tenant` ne sont que des éléments de la liste, pas interprétés par un shell. Le
    jeton n'est JAMAIS journalisé ni inclus dans un message d'erreur ; en cas
    d'échec on ne remonte que stderr tronqué et nettoyé.

    `runner` est injectable (tests offline : on simule la sortie de `az` sans
    exécuter de processus réel).
    """
    if not resource:
        raise FabricError("acquire_token_via_azcli: resource requise.")
    # Liste d'arguments STRICTEMENT fixe (pas de shell, pas d'interpolation).
    args = [
        "az",
        "account",
        "get-access-token",
        "--resource",
        resource,
        "--output",
        "json",
    ]
    if tenant:
        args += ["--tenant", tenant]

    if runner is not None:
        raw = runner(args)
    else:
        try:
            # args est une liste STRICTEMENT fixe, shell=False → pas d'injection.
            completed = subprocess.run(  # nosec B603
                args,
                capture_output=True,
                text=True,
                timeout=_AZCLI_TIMEOUT,
                check=False,
                shell=False,
            )
        except FileNotFoundError as exc:  # `az` absent du PATH
            raise FabricError(
                "Azure CLI (`az`) introuvable : exécutez `az login` sur un poste "
                "où la CLI est installée."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise FabricError("Azure CLI (`az`) : délai dépassé.") from exc
        if completed.returncode != 0:
            # stderr peut contenir un message d'auth, JAMAIS le jeton (échec) ;
            # on tronque pour ne rien étaler d'inutile.
            err = (completed.stderr or "").strip().splitlines()
            detail = err[-1][:200] if err else f"code {completed.returncode}"
            raise FabricError(
                f"Échec `az account get-access-token` (résolvez via `az login`) : {detail}"
            )
        raw = completed.stdout

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError) as exc:
        # Ne JAMAIS inclure `raw` (contient le jeton) dans l'erreur.
        raise FabricError("Sortie `az` illisible (JSON attendu).") from exc
    token = payload.get("accessToken") if isinstance(payload, dict) else None
    if not token:
        raise FabricError("Sortie `az` sans accessToken.")
    return token


def make_azcli_token_provider(
    settings: Settings,
    *,
    runner: Optional[Callable[[list[str]], str]] = None,
) -> TokenProvider:
    """Construit un `TokenProvider` injectable qui acquiert ses jetons via `az`.

    Le provider reçoit une **audience** (ressource OAuth2) et renvoie le jeton
    correspondant via `acquire_token_via_azcli`. Le tenant est passé à `az` s'il
    est connu (Settings) — sinon `az` utilise le tenant courant de la session
    (`az account show`). Pratique pour câbler l'auth az dans `FabricClient`
    (e2e LIVE) sans secret.
    """
    tenant = settings.fabric_tenant_id or None

    async def _provider(audience: str) -> str:
        # `acquire_token_via_azcli` est synchrone (subprocess) — pas d'await réel.
        return acquire_token_via_azcli(audience, tenant=tenant, runner=runner)

    return _provider


def is_gold_path(
    settings: Settings,
    workspace: str,
    item: str,
    item_type: str,
    path: str = "",
) -> bool:
    """Valide qu'un accès OneLake reste dans le **périmètre GOLD** (lecture seule).

    Renvoie ``True`` UNIQUEMENT si TOUTES ces conditions sont réunies (fail-closed :
    le moindre doute ⇒ ``False``) :
      1. le gold est configuré (`settings.fabric_gold_configured`) ;
      2. `workspace` correspond au workspace gold (id OU nom, casse-insensible) ;
      3. `item` correspond au lakehouse gold (id OU nom) ;
      4. `item_type` est le type du lakehouse gold (défaut "Lakehouse") — ou vide
         lorsqu'on adresse par GUID (convention OneLake ``/{wsGUID}/{itemGUID}/...``) ;
      5. `path` (s'il est fourni) est SOUS le préfixe des tables gold
         (`fabric_gold_tables_prefix`, ex. "Tables" ou "Tables/gold"). Tout chemin
         hors de ce sous-arbre (ex. "Files/...") est REFUSÉ.

    C'est la garde unique appelée par `onelake_list_paths`/`onelake_read_file` :
    aucune lecture hors-gold n'est possible.
    """
    if not settings.fabric_gold_configured:
        return False

    def _n(v: Any) -> str:
        return v.strip().lower() if isinstance(v, str) else ""

    # (2) Workspace : doit matcher l'id OU le nom gold (ceux qui sont renseignés).
    ws = _n(workspace)
    allowed_ws = {_n(settings.fabric_gold_workspace_id), _n(settings.fabric_gold_workspace_name)}
    allowed_ws.discard("")
    if not ws or ws not in allowed_ws:
        return False

    # (3) Item (lakehouse) : id OU nom gold.
    it = _n(item)
    allowed_item = {_n(settings.fabric_gold_lakehouse_id), _n(settings.fabric_gold_lakehouse_name)}
    allowed_item.discard("")
    if not it or it not in allowed_item:
        return False

    # (4) Type d'item : le type gold, OU vide (adressage par GUID, sans suffixe).
    typ = _n(item_type)
    if typ and typ != _n(settings.fabric_gold_lakehouse_type):
        return False

    # (5) Chemin : sous le préfixe des tables gold. On compare segment par segment
    # (un préfixe "Tables" ne doit PAS autoriser "TablesAutre", ni "Files").
    if path:
        prefix = settings.fabric_gold_tables_prefix.strip("/")
        norm_path = path.strip("/")
        prefix_segs = [s for s in prefix.lower().split("/") if s]
        path_segs = [s for s in norm_path.lower().split("/") if s]
        if len(path_segs) < len(prefix_segs):
            return False
        if path_segs[: len(prefix_segs)] != prefix_segs:
            return False

    return True


class FabricClient:
    """Client async Fabric/OneLake/Power BI.

    Un seul `httpx.AsyncClient` partagé (réutilisé entre appels). Le fournisseur de
    jeton est **injectable** : par défaut `acquire_token` (réel, env-only) ; dans
    les tests on passe un `token_provider` constant → AUCUN réseau réel. Les jetons
    sont mémoïsés PAR AUDIENCE sur la durée de vie du client (un balayage complet
    réutilise le même jeton plutôt que d'en redemander un par item).

    Les hôtes (Fabric / OneLake / Power BI) viennent des Settings : ce sont des
    constantes d'exploitation, pas des entrées utilisateur → pas de SSRF.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: Optional[httpx.AsyncClient] = None,
        token_provider: Optional[TokenProvider] = None,
        timeout: Optional[httpx.Timeout] = None,
    ) -> None:
        self.settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout or _DEFAULT_TIMEOUT)
        self._token_provider = token_provider
        # Cache de jeton par audience (clé = audience, valeur = access_token).
        self._tokens: dict[str, str] = {}

    async def aclose(self) -> None:
        """Ferme le client httpx s'il nous appartient (sinon no-op)."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "FabricClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # --- Jeton -------------------------------------------------------------- #
    async def _token(self, audience: str) -> str:
        """Jeton app-only pour ``audience`` (mémoïsé). Provider injectable."""
        if self._token_provider is not None:
            return await self._token_provider(audience)
        cached = self._tokens.get(audience)
        if cached is None:
            cached = await acquire_token(self.settings, self._client, audience)
            self._tokens[audience] = cached
        return cached

    # --- Helpers HTTP ------------------------------------------------------- #
    async def _get_json(self, url: str, audience: str, *, extra_headers: Optional[dict] = None) -> dict[str, Any]:
        """GET authentifié → JSON. Lève `FabricError` sur statut non-200 (sans
        jamais journaliser le corps)."""
        headers = {
            "Authorization": f"Bearer {await self._token(audience)}",
            "Accept": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        resp = await self._client.get(url, headers=headers)
        if resp.status_code != 200:
            raise FabricError(
                f"Échec appel Fabric (HTTP {resp.status_code})."
            )
        return resp.json()

    async def _paginate_fabric(self, url: str, audience: str, *, item_key: str = "value") -> list[Any]:
        """Itère une collection Fabric REST en suivant ``continuationToken`` /
        ``continuationUri`` (forme de pagination du contrôle Fabric).

        On préfère ``continuationUri`` (URL absolue déjà prête) si présent ; sinon
        on rejoue l'URL initiale avec ``continuationToken`` en query.
        """
        out: list[Any] = []
        next_url: Optional[str] = url
        pages = 0
        while next_url:
            body = await self._get_json(next_url, audience)
            items = body.get(item_key)
            if isinstance(items, list):
                out.extend(items)
            token = body.get("continuationToken")
            cont_uri = body.get("continuationUri")
            if cont_uri:
                next_url = str(cont_uri)
            elif token:
                # Rejoue l'URL d'origine en ajoutant le token (la sépare proprement).
                sep = "&" if "?" in url else "?"
                next_url = f"{url}{sep}continuationToken={token}"
            else:
                next_url = None
            pages += 1
            if pages > _MAX_PAGES:  # garde-fou anti-boucle
                logger.warning("Pagination Fabric anormalement longue : interruption.")
                break
        return out

    async def _paginate_odata(self, url: str, audience: str) -> list[Any]:
        """Itère une collection OData (Power BI legacy) via ``@odata.nextLink``."""
        out: list[Any] = []
        next_url: Optional[str] = url
        pages = 0
        while next_url:
            body = await self._get_json(next_url, audience)
            items = body.get("value")
            if isinstance(items, list):
                out.extend(items)
            next_url = body.get("@odata.nextLink")
            pages += 1
            if pages > _MAX_PAGES:
                logger.warning("Pagination OData anormalement longue : interruption.")
                break
        return out

    # --- Fabric REST (contrôle) -------------------------------------------- #
    async def list_workspaces(self) -> list[dict[str, Any]]:
        """``GET /v1/workspaces`` — workspaces visibles par le SPN.

        Renvoie la liste brute des objets workspace (id, displayName, type…).
        Pagination Fabric (continuationToken). Lève `FabricError` sur 401/403
        (SPN non autorisé) — l'appelant ACL traite l'échec en fail-closed.
        """
        url = f"{self.settings.fabric_api_host}/v1/workspaces"
        return await self._paginate_fabric(url, AUDIENCE_FABRIC)

    async def list_items(self, workspace_id: str) -> list[dict[str, Any]]:
        """``GET /v1/workspaces/{id}/items`` — items (lakehouses, datasets…) du
        workspace. Pagination Fabric."""
        if not workspace_id:
            raise FabricError("list_items: workspace_id requis.")
        url = f"{self.settings.fabric_api_host}/v1/workspaces/{workspace_id}/items"
        return await self._paginate_fabric(url, AUDIENCE_FABRIC)

    async def list_workspace_role_assignments(self, workspace_id: str) -> list[dict[str, Any]]:
        """``GET /v1/workspaces/{id}/roleAssignments`` — attributions de rôles aux
        principals (utilisateurs / groupes / SPN) sur le workspace.

        C'est la source RBAC de CONTRÔLE : qui a quel rôle (Admin/Member/
        Contributor/Viewer). `fabric_acl.py` la consomme pour décider de la
        lecture. Pagination Fabric. Lève `FabricError` sur erreur (fail-closed)."""
        if not workspace_id:
            raise FabricError("list_workspace_role_assignments: workspace_id requis.")
        url = (
            f"{self.settings.fabric_api_host}/v1/workspaces/{workspace_id}"
            "/roleAssignments"
        )
        return await self._paginate_fabric(url, AUDIENCE_FABRIC)

    # --- OneLake (données, ADLS Gen2 DFS) ---------------------------------- #
    async def onelake_list_paths(
        self,
        workspace: str,
        item: str,
        item_type: str,
        subpath: str = "",
    ) -> list[dict[str, Any]]:
        """Liste les chemins OneLake d'un item — **restreint au périmètre GOLD**.

        ``GET https://onelake.dfs.fabric.microsoft.com/{workspace}?resource=
        filesystem&recursive=true&directory={item}.{itemtype}/{subpath}``

        `workspace`/`item` acceptent le NOM ou le GUID (l'API OneLake gère les
        deux ; avec des GUID, la convention est ``/{wsGUID}/{itemGUID}/...`` — voir
        `onelake_read_file`). Audience = stockage (`storage.azure.com`). Pagination
        ADLS via l'en-tête de réponse ``x-ms-continuation`` (rejoué en query).

        **Gold-only (fail-closed).** Si `subpath` est vide, on liste le sous-arbre
        des tables gold (`fabric_gold_tables_prefix`). Tout `workspace`/`item`/
        `subpath` hors du périmètre gold (cf. `is_gold_path`) ⇒ `FabricError` AVANT
        tout appel réseau.

        Renvoie la liste des entrées ``paths`` (objets {name, isDirectory,
        contentLength…}). Lève `FabricError` sur erreur (fail-closed côté ACL).
        """
        if not (workspace and item and item_type):
            raise FabricError("onelake_list_paths: workspace/item/item_type requis.")
        # Sans sous-chemin explicite, on cible la racine des tables gold.
        if not subpath:
            subpath = self.settings.fabric_gold_tables_prefix
        # Garde GOLD : refuse tout hors-périmètre AVANT le réseau (fail-closed).
        if not is_gold_path(self.settings, workspace, item, item_type, subpath):
            raise FabricError(
                "onelake_list_paths: accès hors périmètre GOLD refusé (lecture seule, "
                "tables gold uniquement)."
            )
        # Le filesystem est le workspace ; le directory cible l'item + sous-chemin.
        # Adressage par GUID → PAS de suffixe `.itemtype` (sinon HTTP 400) ; par NOM
        # → suffixe `.itemtype`. Cf. _is_guid / convention OneLake.
        item_ref = item if _is_guid(item) else f"{item}.{item_type}"
        directory = item_ref
        if subpath:
            directory = f"{directory}/{subpath.lstrip('/')}"
        base = f"{self.settings.onelake_host}/{workspace}"
        params = {
            "resource": "filesystem",
            "recursive": "true",
            "directory": directory,
        }
        paths: list[dict[str, Any]] = []
        continuation: Optional[str] = None
        pages = 0
        while True:
            q = dict(params)
            if continuation:
                q["continuation"] = continuation
            headers = {
                "Authorization": f"Bearer {await self._token(AUDIENCE_STORAGE)}",
                "Accept": "application/json",
            }
            resp = await self._client.get(base, params=q, headers=headers)
            if resp.status_code != 200:
                raise FabricError(
                    f"Échec listing OneLake (HTTP {resp.status_code})."
                )
            body = resp.json()
            entries = body.get("paths")
            if isinstance(entries, list):
                paths.extend(entries)
            # ADLS Gen2 : la continuation est dans l'en-tête de RÉPONSE.
            continuation = resp.headers.get("x-ms-continuation") or None
            pages += 1
            if not continuation or pages > _MAX_PAGES:
                if pages > _MAX_PAGES:
                    logger.warning("Pagination OneLake anormalement longue : interruption.")
                break
        return paths

    async def onelake_read_file(
        self,
        workspace: str,
        item: str,
        item_type: str,
        path: str,
    ) -> bytes:
        """Lit le contenu BRUT d'un fichier OneLake — **restreint au périmètre GOLD**.

        ``GET https://onelake.dfs.fabric.microsoft.com/{workspace}/{item}.{itemtype}
        /{path}`` (supporte aussi les GUID : ``/{wsGUID}/{itemGUID}/{path}`` —
        passe alors ``item_type=""``). Audience = stockage. Renvoie les octets ;
        lève `FabricError` sur erreur. On NE journalise PAS le contenu.

        **Gold-only (fail-closed).** Tout `workspace`/`item`/`path` hors du
        périmètre gold (cf. `is_gold_path` : le `path` DOIT être sous le préfixe des
        tables gold) ⇒ `FabricError` AVANT tout appel réseau.
        """
        if not (workspace and item and path):
            raise FabricError("onelake_read_file: workspace/item/path requis.")
        # Garde GOLD : refuse tout hors-périmètre AVANT le réseau (fail-closed).
        if not is_gold_path(self.settings, workspace, item, item_type, path):
            raise FabricError(
                "onelake_read_file: accès hors périmètre GOLD refusé (lecture seule, "
                "tables gold uniquement)."
            )
        # Adressage par GUID → pas de suffixe ".type" (même si item_type est fourni) ;
        # par NOM → suffixe ".itemtype". Cf. _is_guid / convention OneLake.
        item_ref = item if _is_guid(item) else (f"{item}.{item_type}" if item_type else item)
        url = (
            f"{self.settings.onelake_host}/{workspace}/{item_ref}/{path.lstrip('/')}"
        )
        headers = {"Authorization": f"Bearer {await self._token(AUDIENCE_STORAGE)}"}
        resp = await self._client.get(url, headers=headers)
        if resp.status_code != 200:
            raise FabricError(
                f"Échec lecture fichier OneLake (HTTP {resp.status_code})."
            )
        return resp.content

    async def get_principal_effective_access(
        self,
        workspace_id: str,
        artifact_id: str,
        principal_id: str,
    ) -> dict[str, Any]:
        """Accès EFFECTIF d'un principal sur un artefact OneLake (RBAC fin, PREVIEW).

        ``GET https://onelake.dfs.fabric.microsoft.com/v1.0/workspaces/{workspaceId}
        /artifacts/{artifactId}/securityPolicy/principalAccess`` filtré sur le
        principal. Renvoie le corps JSON décrivant les accès (actions/paths
        autorisés). Audience = stockage.

        ATTENTION : endpoint **PREVIEW** — peut renvoyer 404 (non disponible sur le
        tenant) ou 403 (SPN non habilité). On lève `FabricError` dans ces cas ;
        `fabric_acl.py` traite l'absence de cette source SANS accorder d'accès
        (fail-closed : la décision retombe alors sur les roleAssignments).
        """
        if not (workspace_id and artifact_id and principal_id):
            raise FabricError(
                "get_principal_effective_access: workspace_id/artifact_id/principal_id requis."
            )
        url = (
            f"{self.settings.onelake_host}/v1.0/workspaces/{workspace_id}"
            f"/artifacts/{artifact_id}/securityPolicy/principalAccess"
        )
        headers = {
            "Authorization": f"Bearer {await self._token(AUDIENCE_STORAGE)}",
            "Accept": "application/json",
        }
        # Le principal cible est passé en query (filtre côté service).
        resp = await self._client.get(
            url, params={"principalId": principal_id}, headers=headers
        )
        if resp.status_code != 200:
            raise FabricError(
                f"Échec accès effectif OneLake (HTTP {resp.status_code})."
            )
        return resp.json()

    # --- Power BI REST ----------------------------------------------------- #
    async def list_powerbi_datasets(
        self, workspace_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Liste les datasets Power BI.

        * ``workspace_id=None`` → ``GET /v1.0/myorg/datasets`` (datasets du « My
          workspace » du SPN — généralement vide pour un SPN, conservé pour
          parité d'API).
        * ``workspace_id`` fourni → ``GET /v1.0/myorg/groups/{id}/datasets``.

        Audience = Power BI (`analysis.windows.net/powerbi/api`). Pagination OData
        (`@odata.nextLink`). Lève `FabricError` sur erreur.
        """
        if workspace_id:
            url = (
                f"{self.settings.powerbi_host}/v1.0/myorg/groups/{workspace_id}"
                "/datasets"
            )
        else:
            url = f"{self.settings.powerbi_host}/v1.0/myorg/datasets"
        return await self._paginate_odata(url, AUDIENCE_POWERBI)
