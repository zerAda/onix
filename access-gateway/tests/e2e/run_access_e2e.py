#!/usr/bin/env python3
"""run_access_e2e — PREUVE E2E **LIVE** de l'accès SharePoint + Microsoft Fabric
à travers les modules d'onix (connectivité + RBAC fail-closed).

Ce harnais s'exécute **contre un vrai tenant Entra** (live-only). Il NE contient
aucun secret : tout vient de variables d'environnement. Si un bloc n'a pas ses
variables, il est marqué SKIP (le reste tourne). Si AUCUN bloc n'a ses variables,
le script sort en code 2 (skip total — pas un échec).

Il RÉUTILISE les modules de service déployés (aucune réimplémentation) :
  * SharePoint (Microsoft Graph) : ``app.graph_client`` + ``app.graph_acl``
  * Fabric / OneLake / Power BI  : ``app.fabric_client`` + ``app.fabric_acl``
  * Réglages                     : ``app.config`` (Settings via env GATEWAY_*)

────────────────────────────────────────────────────────────────────────────
SCÉNARIOS
────────────────────────────────────────────────────────────────────────────
A) SharePoint (via Microsoft Graph, app-only / client credentials)
   A1. Connectivité : acquérir un jeton Graph + lister les items d'un drive de
       test  → prouve auth + lecture.
   A2. RBAC AUTORISÉ : un utilisateur autorisé est ACCORDÉ sur un item de test
       (preuve : groupes transitifs ∩ principals de l'item, via GraphDocACL).
   A3. RBAC REFUSÉ : un utilisateur non-autorisé est REFUSÉ (fail-closed).

B) Fabric (via fabric_client / fabric_acl, app-only par audience)
   B1. Connectivité contrôle : jeton Fabric → list_workspaces / list_items.
   B2. Connectivité OneLake  : jeton stockage → onelake_list_paths
       (+ lecture d'un fichier si ONIX_E2E_ONELAKE_PATH fourni).
   B3. Connectivité Power BI : jeton Power BI → list_powerbi_datasets (optionnel).
   B4. RBAC AUTORISÉ : can_principal_read(...) ACCORDE le principal autorisé.
   B5. RBAC REFUSÉ   : can_principal_read(...) REFUSE le principal non-autorisé.

────────────────────────────────────────────────────────────────────────────
VARIABLES D'ENVIRONNEMENT
────────────────────────────────────────────────────────────────────────────
Mode d'authentification (ONIX_E2E_AUTH) :
  azcli         (DÉFAUT si `az` présent) — jetons réels via Azure CLI
                (`az account get-access-token`). L'identité vient de `az login` ;
                ONIX_E2E_CLIENT_SECRET n'est PAS requis. Le tenant est requis
                (ONIX_E2E_TENANT_ID) OU déduit de `az account show`.
  clientsecret  — SPN en client credentials (exige ONIX_E2E_CLIENT_SECRET).

Communes (REQUISES pour tout bloc) :
  ONIX_E2E_TENANT_ID         GUID du tenant Entra (requis ; en azcli, déduit de
                             `az account show` s'il est absent)
  ONIX_E2E_CLIENT_ID         appId du SPN (mode clientsecret uniquement)
  ONIX_E2E_CLIENT_SECRET     secret du SPN (mode clientsecret uniquement ; JAMAIS journalisé)

SharePoint (bloc A — exécuté si TOUTES présentes) :
  ONIX_E2E_SP_SITE_ID        id du site SharePoint (host,siteGuid,webGuid)
  ONIX_E2E_SP_DRIVE_ID       id du drive (bibliothèque de documents)
  ONIX_E2E_SP_ITEM_ID        id de l'item (driveItem) servant à la preuve RBAC
  ONIX_E2E_SP_USER_OK        utilisateur ATTENDU autorisé (oid ou UPN)
  ONIX_E2E_SP_USER_DENIED    utilisateur ATTENDU refusé   (oid ou UPN)

Fabric (bloc B — exécuté si les REQUISES présentes ; GOLD-ONLY, lecture seule) :
  ONIX_E2E_FABRIC_WORKSPACE_ID     id du workspace GOLD              (requis)
  ONIX_E2E_FABRIC_ITEM_ID          id du lakehouse GOLD              (requis)
  ONIX_E2E_FABRIC_ITEM_TYPE        type d'item (ex. Lakehouse)       (requis)
  ONIX_E2E_FABRIC_PRINCIPAL_OK     principal ATTENDU autorisé (oid)  (requis)
  ONIX_E2E_FABRIC_PRINCIPAL_DENIED principal ATTENDU refusé   (oid)  (requis)
  ONIX_E2E_ONELAKE_PATH            chemin OneLake à lire — DOIT être sous le
                                   préfixe des tables gold (optionnel)
  ONIX_E2E_FABRIC_GOLD_TABLES_PREFIX  préfixe tables gold (optionnel ; défaut Tables)
  ONIX_E2E_PBI_WORKSPACE_ID        workspace Power BI à lister (optionnel)

Le bloc B câble automatiquement le périmètre GOLD : le workspace/item ci-dessus
sont posés comme GATEWAY_FABRIC_GOLD_* → seules les tables gold sont lisibles.

Réglages réseau (optionnels) :
  ONIX_E2E_HTTP_TIMEOUT      timeout HTTP en secondes (défaut 20)
  Hôtes souverains : GATEWAY_GRAPH_HOST / GATEWAY_GRAPH_AUTHORITY /
  GATEWAY_FABRIC_API_HOST / GATEWAY_ONELAKE_HOST / GATEWAY_POWERBI_HOST
  (mêmes valeurs que la passerelle — voir app/config.py).

────────────────────────────────────────────────────────────────────────────
SORTIES DE PROCESSUS
  0  tous les blocs PRÉSENTS sont passés (et ≥1 bloc exécuté)
  1  au moins un scénario a ÉCHOUÉ
  2  aucun bloc n'avait ses variables (skip total) — PAS un échec

Usage :
    ONIX_E2E_TENANT_ID=... ONIX_E2E_CLIENT_ID=... ONIX_E2E_CLIENT_SECRET=... \
    ONIX_E2E_SP_SITE_ID=... [autres vars] \
        python access-gateway/tests/e2e/run_access_e2e.py [--help]

SÉCURITÉ : on ne journalise JAMAIS un secret ni un jeton. Aucun appel réseau au
moment de l'import (tout est sous ``if __name__ == "__main__"``).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

# ── Rendre `app` (gateway) importable, comme run_e2e.py ────────────────────
_HERE = Path(__file__).resolve().parent           # .../access-gateway/tests/e2e
_GW_ROOT = _HERE.parent.parent                     # .../access-gateway
if str(_GW_ROOT) not in sys.path:
    sys.path.insert(0, str(_GW_ROOT))


# ───────────────────────────────────────────────────────────────────────────
# Présentation des résultats — un Scenario = (code, libellé, statut, preuve).
# ───────────────────────────────────────────────────────────────────────────
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

_MARK = {PASS: "✅", FAIL: "❌", SKIP: "⏭"}


class Report:
    """Collecteur de scénarios + synthèse imprimée (structure calquée sur
    run_e2e.py : rapport texte, pas de framework de test)."""

    def __init__(self) -> None:
        self.rows: list[dict[str, str]] = []
        self.blocks_run = 0  # nb de blocs (SP/Fabric) effectivement exécutés

    def add(self, code: str, label: str, status: str, proof: str) -> None:
        self.rows.append(
            {"code": code, "label": label, "status": status, "proof": proof}
        )
        mark = _MARK.get(status, "?")
        print(f"\n{mark} {code} — {label} [{status}]")
        print(f"   preuve : {proof}")

    def block_skipped(self, name: str, missing: list[str]) -> None:
        print(f"\n{_MARK[SKIP]} BLOC {name} — SKIP")
        print(f"   variables manquantes : {', '.join(missing)}")

    @property
    def failures(self) -> int:
        return sum(1 for r in self.rows if r["status"] == FAIL)

    @property
    def passes(self) -> int:
        return sum(1 for r in self.rows if r["status"] == PASS)

    def summary(self) -> None:
        print("\n" + "═" * 78)
        print(
            f"RÉSULTAT ACCESS E2E (LIVE) : {self.passes} PASS, "
            f"{self.failures} FAIL — blocs exécutés : {self.blocks_run}"
        )
        print("═" * 78)
        for r in self.rows:
            mark = _MARK.get(r["status"], "?")
            print(f"  {mark} {r['code']:<10} {r['label']}")


# ───────────────────────────────────────────────────────────────────────────
# Env helpers — lecture des ONIX_E2E_* et mapping vers les GATEWAY_* (Settings).
# ───────────────────────────────────────────────────────────────────────────
def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _missing(names: list[str]) -> list[str]:
    """Renvoie le sous-ensemble de `names` absent/vide de l'environnement."""
    return [n for n in names if not _env(n)]


def _az_available() -> bool:
    """`True` si l'exécutable `az` (Azure CLI) est sur le PATH."""
    return shutil.which("az") is not None


def auth_mode() -> str:
    """Mode d'authentification effectif : `azcli` (défaut si `az` présent) ou
    `clientsecret`. Surchargé par ONIX_E2E_AUTH ∈ {azcli, clientsecret}."""
    raw = _env("ONIX_E2E_AUTH").lower()
    if raw in {"azcli", "clientsecret"}:
        return raw
    # Auto : az si la CLI est dispo, sinon client secret.
    return "azcli" if _az_available() else "clientsecret"


def _build_settings():
    """Construit un `Settings` à partir des ONIX_E2E_* communs + des hôtes.

    On mappe les identifiants e2e vers les variables GATEWAY_* lues par
    ``app.config.get_settings`` (source unique de vérité), puis on réinitialise
    le cache LRU pour relire l'environnement. AUCUN secret n'est imprimé.

    Mode `azcli` : l'identité vient de `az login` ; on active GATEWAY_FABRIC_USE_AZCLI
    et on ne pose PAS de client_secret. On câble aussi le périmètre GOLD à partir
    des cibles Fabric (workspace/item) → lecture restreinte aux tables gold.
    """
    import app.config as gw_config

    os.environ["GATEWAY_GRAPH_TENANT_ID"] = _env("ONIX_E2E_TENANT_ID")
    os.environ["GATEWAY_GRAPH_CLIENT_ID"] = _env("ONIX_E2E_CLIENT_ID")

    mode = auth_mode()
    if mode == "azcli":
        # Pas de secret : l'identité vient de `az login`. On active le provider az.
        os.environ["GATEWAY_GRAPH_CLIENT_SECRET"] = ""
        os.environ["GATEWAY_FABRIC_USE_AZCLI"] = "true"
    else:
        os.environ["GATEWAY_GRAPH_CLIENT_SECRET"] = _env("ONIX_E2E_CLIENT_SECRET")
        os.environ["GATEWAY_FABRIC_USE_AZCLI"] = "false"

    # Périmètre GOLD : on mappe les cibles Fabric e2e vers les GATEWAY_FABRIC_GOLD_*
    # → seules les tables gold du lakehouse ciblé sont lisibles (cf. config.py).
    if _env("ONIX_E2E_FABRIC_WORKSPACE_ID"):
        os.environ["GATEWAY_FABRIC_GOLD_WORKSPACE_ID"] = _env("ONIX_E2E_FABRIC_WORKSPACE_ID")
    if _env("ONIX_E2E_FABRIC_ITEM_ID"):
        os.environ["GATEWAY_FABRIC_GOLD_LAKEHOUSE_ID"] = _env("ONIX_E2E_FABRIC_ITEM_ID")
    if _env("ONIX_E2E_FABRIC_ITEM_TYPE"):
        os.environ["GATEWAY_FABRIC_GOLD_LAKEHOUSE_TYPE"] = _env("ONIX_E2E_FABRIC_ITEM_TYPE")
    if _env("ONIX_E2E_FABRIC_GOLD_TABLES_PREFIX"):
        os.environ["GATEWAY_FABRIC_GOLD_TABLES_PREFIX"] = _env(
            "ONIX_E2E_FABRIC_GOLD_TABLES_PREFIX"
        )

    gw_config.reset_settings_cache()
    return gw_config.get_settings()


# ───────────────────────────────────────────────────────────────────────────
# BLOC A — SharePoint via Microsoft Graph.
# ───────────────────────────────────────────────────────────────────────────
_SP_VARS = [
    "ONIX_E2E_SP_SITE_ID",
    "ONIX_E2E_SP_DRIVE_ID",
    "ONIX_E2E_SP_ITEM_ID",
    "ONIX_E2E_SP_USER_OK",
    "ONIX_E2E_SP_USER_DENIED",
]


async def run_sharepoint(settings, report: Report, timeout: float) -> None:
    """Exécute A1 (connectivité), A2 (RBAC autorisé), A3 (RBAC refusé)."""
    import httpx

    from app.graph_acl import GraphSession, build_graph_acl
    from app.graph_client import (
        GraphError,
        acquire_app_token,
        fetch_transitive_group_ids,
    )

    site_id = _env("ONIX_E2E_SP_SITE_ID")
    drive_id = _env("ONIX_E2E_SP_DRIVE_ID")
    item_id = _env("ONIX_E2E_SP_ITEM_ID")
    user_ok = _env("ONIX_E2E_SP_USER_OK")
    user_denied = _env("ONIX_E2E_SP_USER_DENIED")

    print("\n" + "─" * 78)
    print("BLOC A — SharePoint (Microsoft Graph, app-only)")
    print("─" * 78)

    # En mode azcli, le jeton Graph vient de `az` (zéro secret) ; sinon client
    # credentials via acquire_app_token (provider par défaut).
    graph_provider = _graph_token_provider(settings)

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        # ── A1 : connectivité (jeton + listing du drive) ──────────────────
        try:
            token = (
                await graph_provider() if graph_provider
                else await acquire_app_token(settings, client)
            )
            # Preuve de lecture : lister les enfants de la racine du drive.
            url = (
                f"{settings.graph_host}/v1.0/sites/{site_id}"
                f"/drives/{drive_id}/root/children?$select=id,name&$top=10"
            )
            resp = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
            if resp.status_code != 200:
                report.add(
                    "A1", "Connectivité Graph + listing drive", FAIL,
                    f"listing drive HTTP {resp.status_code} (jeton acquis, "
                    "mais lecture refusée — vérifier Sites.Read.All + ids).",
                )
                # Sans connectivité, le RBAC est inexploitable : on stoppe le bloc.
                return
            children = resp.json().get("value", [])
            report.add(
                "A1", "Connectivité Graph + listing drive", PASS,
                f"jeton app-only acquis ; {len(children)} élément(s) lus à la "
                f"racine du drive (auth + lecture prouvées).",
            )
        except GraphError as exc:
            report.add(
                "A1", "Connectivité Graph + listing drive", FAIL,
                f"acquisition jeton / listing impossible ({exc}).",
            )
            return
        except (httpx.HTTPError, OSError) as exc:
            report.add(
                "A1", "Connectivité Graph + listing drive", FAIL,
                f"erreur réseau ({type(exc).__name__}).",
            )
            return

        # ── ACL VIVANTE de l'item de test (réutilise build_graph_acl) ──────
        # Mapping minimal : un doc_id factice ↔ l'item SharePoint réel.
        doc_id = "e2e-sp-item"
        session = GraphSession(
            client=client, settings=settings, token_provider=graph_provider
        )
        try:
            acl = await build_graph_acl(
                session,
                {doc_id: {"site_id": site_id, "drive_id": drive_id, "item_id": item_id}},
                default_policy="deny",
            )
        except GraphError as exc:
            report.add(
                "A2", "RBAC SharePoint — utilisateur AUTORISÉ accordé", FAIL,
                f"construction de l'ACL de l'item impossible ({exc}).",
            )
            return

        item_listed = len(acl) == 1  # 1 => permissions de l'item lues avec succès
        if not item_listed:
            # L'item a été OMIS (lecture des permissions échouée) → tout deny.
            report.add(
                "A2", "RBAC SharePoint — utilisateur AUTORISÉ accordé", FAIL,
                "permissions de l'item NON lues (item omis de l'ACL) — "
                "vérifier ONIX_E2E_SP_ITEM_ID + Sites.Read.All.",
            )

        # ── A2 : utilisateur AUTORISÉ doit être ACCORDÉ ───────────────────
        ok_principal, ok_note = await _resolve_principal(
            user_ok, settings, client, fetch_transitive_group_ids, graph_provider
        )
        if item_listed:
            granted_ok = acl.is_authorized(doc_id, ok_principal)
            report.add(
                "A2", "RBAC SharePoint — utilisateur AUTORISÉ accordé",
                PASS if granted_ok else FAIL,
                _rbac_proof(
                    user_ok, ok_principal, granted_ok, expected=True,
                    source="permissions de l'item SharePoint (Graph)", note=ok_note,
                ),
            )

        # ── A3 : utilisateur NON-AUTORISÉ doit être REFUSÉ (fail-closed) ──
        denied_principal, denied_note = await _resolve_principal(
            user_denied, settings, client, fetch_transitive_group_ids, graph_provider
        )
        if item_listed:
            granted_denied = acl.is_authorized(doc_id, denied_principal)
            # Attendu : refus. granted_denied=True => FUITE (échec dur).
            report.add(
                "A3", "RBAC SharePoint — utilisateur NON-AUTORISÉ refusé",
                FAIL if granted_denied else PASS,
                _rbac_proof(
                    user_denied, denied_principal, granted_denied, expected=False,
                    source="permissions de l'item SharePoint (Graph)", note=denied_note,
                ),
            )


async def _resolve_principal(user, settings, client, fetch_groups, token_provider=None):
    """Construit un `Principal` + une note, avec ses groupes transitifs
    (best-effort). Renvoie ``(principal, note)``.

    Si la résolution des groupes échoue (droits Graph insuffisants), on retombe
    sur une liste vide : la décision RBAC reste valide (fail-closed) et la note
    le mentionne. On accepte oid OU UPN comme identifiant (les deux sont testés
    par GraphDocACL : override user gagne, sinon appartenance de groupe).
    `token_provider` (mode azcli) fournit le jeton Graph via `az` (zéro secret)."""
    from app.identity import Principal
    from app.graph_client import GraphError

    groups: list[str] = []
    note = ""
    try:
        groups = await fetch_groups(
            user, settings, client=client, token_provider=token_provider
        )
    except GraphError as exc:
        note = f" (groupes non résolus : {exc})"
    upn = user if "@" in user else None
    p = Principal(user_id=user, upn=upn, group_ids=groups, source="e2e")
    return p, note


def _rbac_proof(user, principal, granted, *, expected: bool, source: str, note: str = "") -> str:
    """Construit une chaîne de preuve lisible (sans secret). Indique la décision,
    l'attendu, le nombre de groupes transitifs et la source."""
    n_groups = len(getattr(principal, "group_ids", []) or [])
    decision = "ACCORDÉ" if granted else "REFUSÉ"
    attendu = "accordé" if expected else "refusé"
    verdict = "conforme" if granted == expected else "NON CONFORME (alerte)"
    return (
        f"utilisateur '{_mask(user)}' → décision {decision} (attendu {attendu}, "
        f"{verdict}) ; {n_groups} groupe(s) transitif(s) ; source : {source}{note}"
    )


def _mask(identifier: str) -> str:
    """Masque partiellement un identifiant pour le rapport (ni secret, mais on
    évite d'étaler un UPN complet). Garde le début pour l'identification."""
    if not identifier:
        return "?"
    if "@" in identifier:
        local, _, domain = identifier.partition("@")
        head = local[:3] + "…" if len(local) > 3 else local
        return f"{head}@{domain}"
    return identifier[:8] + "…" if len(identifier) > 8 else identifier


# ───────────────────────────────────────────────────────────────────────────
# BLOC B — Microsoft Fabric / OneLake / Power BI.
# ───────────────────────────────────────────────────────────────────────────
_FABRIC_VARS = [
    "ONIX_E2E_FABRIC_WORKSPACE_ID",
    "ONIX_E2E_FABRIC_ITEM_ID",
    "ONIX_E2E_FABRIC_ITEM_TYPE",
    "ONIX_E2E_FABRIC_PRINCIPAL_OK",
    "ONIX_E2E_FABRIC_PRINCIPAL_DENIED",
]


async def run_fabric(settings, report: Report, timeout: float) -> None:
    """Exécute B1..B5 (connectivité contrôle/OneLake/Power BI + RBAC par-item).

    GOLD-ONLY : le workspace/item ciblés ont été câblés en GATEWAY_FABRIC_GOLD_*
    (cf. _build_settings) → OneLake ne lit que les tables gold ; can_principal_read
    refuse tout item hors gold."""
    import httpx

    from app.fabric_client import FabricClient, FabricError, make_azcli_token_provider
    from app.fabric_acl import can_principal_read
    from app.graph_client import GraphError, fetch_transitive_group_ids

    workspace_id = _env("ONIX_E2E_FABRIC_WORKSPACE_ID")
    item_id = _env("ONIX_E2E_FABRIC_ITEM_ID")
    item_type = _env("ONIX_E2E_FABRIC_ITEM_TYPE")
    principal_ok = _env("ONIX_E2E_FABRIC_PRINCIPAL_OK")
    principal_denied = _env("ONIX_E2E_FABRIC_PRINCIPAL_DENIED")
    onelake_path = _env("ONIX_E2E_ONELAKE_PATH")        # optionnel
    pbi_workspace = _env("ONIX_E2E_PBI_WORKSPACE_ID")   # optionnel

    print("\n" + "─" * 78)
    print("BLOC B — Microsoft Fabric / OneLake / Power BI (app-only par audience)")
    print("─" * 78)

    http = httpx.AsyncClient(timeout=httpx.Timeout(timeout))
    # En mode azcli, le client Fabric acquiert ses jetons via `az` (zéro secret) ;
    # en clientsecret, on laisse le provider par défaut (client credentials).
    token_provider = (
        make_azcli_token_provider(settings) if settings.fabric_use_azcli else None
    )
    fabric = FabricClient(settings, client=http, token_provider=token_provider)
    try:
        # ── B1 : connectivité contrôle Fabric (workspaces + items) ────────
        try:
            workspaces = await fabric.list_workspaces()
            items = await fabric.list_items(workspace_id)
            report.add(
                "B1", "Connectivité Fabric — list_workspaces / list_items", PASS,
                f"jeton Fabric acquis ; {len(workspaces)} workspace(s) visibles ; "
                f"{len(items)} item(s) dans le workspace de test.",
            )
        except FabricError as exc:
            report.add(
                "B1", "Connectivité Fabric — list_workspaces / list_items", FAIL,
                f"appel Fabric impossible ({exc}) — vérifier l'habilitation du "
                "SPN (Fabric APIs + rôle workspace).",
            )

        # ── B2 : connectivité OneLake (listing + lecture optionnelle) ─────
        try:
            paths = await fabric.onelake_list_paths(workspace_id, item_id, item_type)
            proof = (
                f"jeton stockage acquis ; {len(paths)} chemin(s) listé(s) sous "
                f"{item_id}.{item_type}/Files."
            )
            if onelake_path:
                blob = await fabric.onelake_read_file(
                    workspace_id, item_id, item_type, onelake_path
                )
                proof += f" Lecture de '{onelake_path}' : {len(blob)} octet(s)."
            report.add("B2", "Connectivité OneLake — list paths (+ read)", PASS, proof)
        except FabricError as exc:
            report.add(
                "B2", "Connectivité OneLake — list paths (+ read)", FAIL,
                f"appel OneLake impossible ({exc}) — vérifier le rôle data + "
                "ONIX_E2E_FABRIC_ITEM_TYPE/PATH.",
            )

        # ── B3 : connectivité Power BI (optionnelle) ──────────────────────
        if pbi_workspace:
            try:
                datasets = await fabric.list_powerbi_datasets(pbi_workspace)
                report.add(
                    "B3", "Connectivité Power BI — list_powerbi_datasets", PASS,
                    f"jeton Power BI acquis ; {len(datasets)} dataset(s) listé(s) "
                    f"dans le workspace Power BI de test.",
                )
            except FabricError as exc:
                report.add(
                    "B3", "Connectivité Power BI — list_powerbi_datasets", FAIL,
                    f"appel Power BI impossible ({exc}).",
                )
        else:
            print(
                f"\n{_MARK[SKIP]} B3 — Connectivité Power BI [SKIP] "
                "(ONIX_E2E_PBI_WORKSPACE_ID absent)"
            )

        # ── Groupes transitifs des principals (best-effort, via Graph) ────
        graph_provider = _graph_token_provider(settings)
        groups_ok = await _fabric_groups(
            principal_ok, settings, http, fetch_transitive_group_ids, graph_provider
        )
        groups_denied = await _fabric_groups(
            principal_denied, settings, http, fetch_transitive_group_ids, graph_provider
        )

        # ── B4 : principal AUTORISÉ doit être ACCORDÉ ─────────────────────
        try:
            granted_ok = await can_principal_read(
                principal_ok, workspace_id, item_id,
                fabric=fabric, principal_group_ids=groups_ok,
            )
            report.add(
                "B4", "RBAC Fabric — principal AUTORISÉ accordé",
                PASS if granted_ok else FAIL,
                _fabric_rbac_proof(
                    principal_ok, granted_ok, len(groups_ok), expected=True
                ),
            )
        except FabricError as exc:
            report.add(
                "B4", "RBAC Fabric — principal AUTORISÉ accordé", FAIL,
                f"décision RBAC impossible ({exc}).",
            )

        # ── B5 : principal NON-AUTORISÉ doit être REFUSÉ (fail-closed) ────
        try:
            granted_denied = await can_principal_read(
                principal_denied, workspace_id, item_id,
                fabric=fabric, principal_group_ids=groups_denied,
            )
            report.add(
                "B5", "RBAC Fabric — principal NON-AUTORISÉ refusé",
                FAIL if granted_denied else PASS,
                _fabric_rbac_proof(
                    principal_denied, granted_denied, len(groups_denied), expected=False
                ),
            )
        except FabricError as exc:
            report.add(
                "B5", "RBAC Fabric — principal NON-AUTORISÉ refusé", FAIL,
                f"décision RBAC impossible ({exc}).",
            )
    finally:
        await fabric.aclose()


async def _fabric_groups(principal, settings, client, fetch_groups, token_provider=None) -> list[str]:
    """Résout les groupes Entra transitifs d'un principal (best-effort). Un SPN
    n'est pas un utilisateur Graph ; en cas d'échec on retourne [] (la décision
    par roleAssignment direct reste valide). En mode azcli, `token_provider`
    fournit le jeton Graph via `az` (zéro secret)."""
    from app.graph_client import GraphError

    try:
        return await fetch_groups(
            principal, settings, client=client, token_provider=token_provider
        )
    except GraphError:
        return []


def _graph_token_provider(settings):
    """Construit un fournisseur de jeton GRAPH (sans argument, signature attendue
    par graph_client) via `az` en mode azcli ; sinon None (provider par défaut =
    client credentials de graph_client)."""
    if not settings.fabric_use_azcli:
        return None
    from app.fabric_client import AUDIENCE_GRAPH, acquire_token_via_azcli

    tenant = settings.graph_tenant_id or None

    async def _provider() -> str:
        return acquire_token_via_azcli(AUDIENCE_GRAPH, tenant=tenant)

    return _provider


def _fabric_rbac_proof(principal, granted, n_groups, *, expected: bool) -> str:
    decision = "ACCORDÉ" if granted else "REFUSÉ"
    attendu = "accordé" if expected else "refusé"
    verdict = "conforme" if granted == expected else "NON CONFORME (alerte)"
    return (
        f"principal '{_mask(principal)}' → décision {decision} (attendu "
        f"{attendu}, {verdict}) ; {n_groups} groupe(s) transitif(s) ; source : "
        f"roleAssignments workspace (+ principalAccess OneLake si dispo)."
    )


# ───────────────────────────────────────────────────────────────────────────
# Orchestration.
# ───────────────────────────────────────────────────────────────────────────
def _common_vars() -> list[str]:
    """Variables communes REQUISES selon le mode d'auth.

    * clientsecret : tenant + client id + secret (SPN client credentials).
    * azcli        : tenant uniquement (l'identité vient de `az login` ; le secret
                     n'est PAS requis). Le tenant peut même être déduit de
                     `az account show`, mais on l'exige ici pour rester explicite.
    """
    if auth_mode() == "azcli":
        return ["ONIX_E2E_TENANT_ID"]
    return ["ONIX_E2E_TENANT_ID", "ONIX_E2E_CLIENT_ID", "ONIX_E2E_CLIENT_SECRET"]


def _print_skip_total() -> None:
    """Message clair quand AUCUN bloc n'a ses variables (skip total → exit 2)."""
    mode = auth_mode()
    print("═" * 78)
    print("ACCESS E2E — SKIP TOTAL : aucune cible configurée.")
    print("═" * 78)
    print(
        "\nCe harnais est LIVE-ONLY : il s'exécute contre un vrai tenant Entra.\n"
        f"Mode d'auth actif : {mode} "
        f"({'jetons via az login — zéro secret' if mode == 'azcli' else 'SPN client secret'}).\n"
        "Surcharge possible via ONIX_E2E_AUTH ∈ {azcli, clientsecret}.\n"
        "Pour exécuter un bloc, définis ses variables d'environnement :\n"
    )
    print(f"  Communes (REQUISES pour tout bloc, mode {mode}) :")
    for v in _common_vars():
        print(f"    - {v}")
    print("\n  Bloc A (SharePoint) — toutes requises :")
    for v in _SP_VARS:
        print(f"    - {v}")
    print("\n  Bloc B (Fabric) — toutes requises (GOLD-ONLY, lecture seule) :")
    for v in _FABRIC_VARS:
        print(f"    - {v}")
    print(
        "    Optionnelles : ONIX_E2E_ONELAKE_PATH (sous tables gold), "
        "ONIX_E2E_FABRIC_GOLD_TABLES_PREFIX, ONIX_E2E_PBI_WORKSPACE_ID"
    )
    print("\n  Voir --help pour le détail. Sortie : code 2 (skip total).")


async def _amain(args: argparse.Namespace) -> int:
    timeout = float(os.environ.get("ONIX_E2E_HTTP_TIMEOUT", "20"))

    # Quelles cibles sont configurées ? (communes + spécifiques de chaque bloc)
    common_vars = _common_vars()
    common_missing = _missing(common_vars)
    sp_missing = _missing(common_vars + _SP_VARS)
    fabric_missing = _missing(common_vars + _FABRIC_VARS)
    want_sp = not sp_missing
    want_fabric = not fabric_missing

    if not want_sp and not want_fabric:
        _print_skip_total()
        return 2

    settings = _build_settings()
    report = Report()

    print("═" * 78)
    print("ACCESS E2E (LIVE) — preuve SharePoint + Fabric à travers les modules onix")
    print(f"  mode d'auth  : {auth_mode()}"
          f"{' (jetons via az login)' if settings.fabric_use_azcli else ' (SPN client secret)'}")
    print(f"  hôte Graph   : {settings.graph_host}")
    print(f"  hôte Fabric  : {settings.fabric_api_host}")
    print(f"  hôte OneLake : {settings.onelake_host}")
    print(f"  Fabric gold  : {'configuré' if settings.fabric_gold_configured else 'NON configuré'}")
    print(f"  timeout HTTP : {timeout:.0f}s")
    print("═" * 78)

    # ── BLOC A : SharePoint ───────────────────────────────────────────────
    if want_sp:
        report.blocks_run += 1
        await run_sharepoint(settings, report, timeout)
    else:
        report.block_skipped("A (SharePoint)", sp_missing)

    # ── BLOC B : Fabric ───────────────────────────────────────────────────
    if want_fabric:
        report.blocks_run += 1
        await run_fabric(settings, report, timeout)
    else:
        report.block_skipped("B (Fabric)", fabric_missing)

    report.summary()

    # Exit : 1 si un scénario a échoué, sinon 0 (au moins un bloc a tourné).
    return 1 if report.failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run_access_e2e.py",
        description=(
            "Harnais e2e LIVE : prouve l'accès SharePoint (Graph) et Microsoft "
            "Fabric (connectivité + RBAC fail-closed) à travers les modules onix. "
            "Lit les cibles depuis l'environnement ; SKIP propre si absentes."
        ),
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--list-vars", action="store_true",
        help="affiche les variables d'environnement attendues, puis quitte.",
    )
    args = ap.parse_args()

    if args.list_vars:
        _print_skip_total()
        return 0

    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
