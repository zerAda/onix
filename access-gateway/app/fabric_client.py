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
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional, Union

import httpx

from .config import Settings

logger = logging.getLogger("onix.gateway.fabric")

# Audiences (ressources OAuth2) — le scope est ``<audience>/.default``.
AUDIENCE_FABRIC = "https://api.fabric.microsoft.com"
AUDIENCE_STORAGE = "https://storage.azure.com"
AUDIENCE_POWERBI = "https://analysis.windows.net/powerbi/api"

# Garde-fou anti-boucle de pagination (cohérent graph_client.py).
_MAX_PAGES = 1000

# Timeout par défaut des appels (cohérent graph_client.py : 15s).
_DEFAULT_TIMEOUT = httpx.Timeout(15.0)

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
        subpath: str = "Files",
    ) -> list[dict[str, Any]]:
        """Liste les chemins OneLake d'un item (lakehouse/warehouse…).

        ``GET https://onelake.dfs.fabric.microsoft.com/{workspace}?resource=
        filesystem&recursive=true&directory={item}.{itemtype}/{subpath}``

        `workspace`/`item` acceptent le NOM ou le GUID (l'API OneLake gère les
        deux ; avec des GUID, la convention est ``/{wsGUID}/{itemGUID}/...`` — voir
        `onelake_read_file`). Audience = stockage (`storage.azure.com`). Pagination
        ADLS via l'en-tête de réponse ``x-ms-continuation`` (rejoué en query).

        Renvoie la liste des entrées ``paths`` (objets {name, isDirectory,
        contentLength…}). Lève `FabricError` sur erreur (fail-closed côté ACL).
        """
        if not (workspace and item and item_type):
            raise FabricError("onelake_list_paths: workspace/item/item_type requis.")
        # Le filesystem est le workspace ; le directory cible l'item + sous-chemin.
        directory = f"{item}.{item_type}"
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
        """Lit le contenu BRUT d'un fichier OneLake.

        ``GET https://onelake.dfs.fabric.microsoft.com/{workspace}/{item}.{itemtype}
        /{path}`` (supporte aussi les GUID : ``/{wsGUID}/{itemGUID}/{path}`` —
        passe alors ``item_type=""``). Audience = stockage. Renvoie les octets ;
        lève `FabricError` sur erreur. On NE journalise PAS le contenu.
        """
        if not (workspace and item and path):
            raise FabricError("onelake_read_file: workspace/item/path requis.")
        # Avec un GUID d'item, item_type est vide → pas de suffixe ".type".
        item_ref = f"{item}.{item_type}" if item_type else item
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
