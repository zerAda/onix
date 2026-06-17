#!/usr/bin/env python3
# =============================================================================
# sync-doc-acl.py — synchronise l'ACL par-document depuis SharePoint (Graph) et
# ÉCRIT un `doc_acl.json` compatible avec la passerelle (StaticDocACL).
#
# Pourquoi un CLI ?
#   La passerelle peut consommer l'ACL Graph EN VIF (CompositeDocACL, cf.
#   docs/RBAC.md §4.3). Mais on veut aussi pouvoir matérialiser l'ACL dans un
#   FICHIER JSON figé pour : (a) garder le chemin `StaticDocACL` qui marche déjà
#   sans changement de code, (b) auditer/diff le résultat, (c) le rafraîchir par
#   cron/CI. Ce script lit un mapping {doc_id: {site_id, drive_id, item_id}} +
#   les creds Graph (mêmes noms d'env que la gateway), interroge Graph et écrit
#   le `doc_acl.json`.
#
# Lien doc_id ↔ item SharePoint (le maillon dur — voir docs/connectors/SHAREPOINT.md) :
#   Onyx stocke l'URL source / l'id de drive-item dans les MÉTADONNÉES du document
#   (connecteur SharePoint). On NE devine PAS ce lien : on consomme un mapping
#   explicite (fourni par l'admin / un export depuis Onyx).
#
# Permission Graph (APPLICATION, admin consent) : Sites.Read.All (ou
#   Sites.Selected + octroi par site). Endpoint :
#   GET /v1.0/sites/{site-id}/drives/{drive-id}/items/{item-id}/permissions
#
# Secrets : lus dans l'ENVIRONNEMENT uniquement (GATEWAY_GRAPH_*). Jamais en arg,
#   jamais journalisés.
#
# Exemples :
#   GATEWAY_GRAPH_TENANT_ID=... GATEWAY_GRAPH_CLIENT_ID=... \
#   GATEWAY_GRAPH_CLIENT_SECRET=... \
#     python scripts/sync-doc-acl.py \
#       --mapping access-gateway/config/doc_acl_mapping.json \
#       --out access-gateway/config/doc_acl.json
#   # ou via Makefile :  make sync-doc-acl
# =============================================================================
"""Sync de l'ACL par-document SharePoint→`doc_acl.json` (Microsoft Graph, app-only)."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

# Rendre le package `app` de la passerelle importable (réutilise graph_acl, config,
# graph_client — aucune duplication de logique Graph).
_HERE = os.path.dirname(os.path.abspath(__file__))
_GATEWAY = os.path.join(_HERE, "..", "access-gateway")
if _GATEWAY not in sys.path:
    sys.path.insert(0, os.path.abspath(_GATEWAY))

import httpx  # noqa: E402

import app.config as config  # noqa: E402
from app.graph_acl import (  # noqa: E402
    GraphSession,
    build_graph_acl,
    entries_to_acl_obj,
    load_mapping,
)
from app.graph_client import GraphError  # noqa: E402


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="sync-doc-acl",
        description=(
            "Synchronise l'ACL par-document depuis SharePoint (Microsoft Graph) "
            "et écrit un doc_acl.json compatible passerelle (StaticDocACL)."
        ),
    )
    p.add_argument(
        "--mapping",
        default=os.environ.get(
            "GATEWAY_DOC_ACL_MAPPING_PATH",
            "access-gateway/config/doc_acl_mapping.json",
        ),
        help="JSON { doc_id: {site_id, drive_id, item_id} } (défaut: $GATEWAY_DOC_ACL_MAPPING_PATH).",
    )
    p.add_argument(
        "--out",
        default=os.environ.get("GATEWAY_DOC_ACL_PATH", "access-gateway/config/doc_acl.json"),
        help="Fichier doc_acl.json à écrire (défaut: $GATEWAY_DOC_ACL_PATH).",
    )
    p.add_argument(
        "--default-policy",
        default=os.environ.get("GATEWAY_DOC_ACL_DEFAULT_POLICY", "deny"),
        choices=("deny", "allow"),
        help="Politique d'un doc non synchronisable (défaut: deny). Informative ici "
        "(le fichier écrit ne contient que les docs résolus).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("GATEWAY_UPSTREAM_TIMEOUT", "30")),
        help="Timeout (s) des appels Graph.",
    )
    return p.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    settings = config.get_settings()
    if not settings.graph_configured:
        print(
            "ERREUR : Microsoft Graph non configuré. Renseignez GATEWAY_GRAPH_TENANT_ID, "
            "GATEWAY_GRAPH_CLIENT_ID, GATEWAY_GRAPH_CLIENT_SECRET (env).",
            file=sys.stderr,
        )
        return 2

    try:
        mapping = load_mapping(args.mapping)
    except GraphError as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 2

    async with httpx.AsyncClient(timeout=httpx.Timeout(args.timeout)) as client:
        graph = GraphSession(client=client, settings=settings)
        acl = await build_graph_acl(
            graph,
            mapping,
            default_policy=args.default_policy,
            # TTL non pertinent pour un dump one-shot (fichier figé).
            ttl_seconds=0,
        )

    out_obj = entries_to_acl_obj(acl)
    # Métadonnées non bloquantes (ignorées par StaticDocACL.from_obj : clés '_*').
    payload: dict = {
        "_generated_by": "scripts/sync-doc-acl.py",
        "_source": "microsoft_graph_item_permissions",
        "_doc_count": len(out_obj),
        **out_obj,
    }
    # Écriture atomique (tmp + replace) pour ne jamais laisser un fichier partiel
    # si le process est tué en cours d'écriture.
    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp_path = f"{out_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp_path, out_path)

    print(
        f"OK : {len(out_obj)} document(s) écrit(s) dans {out_path} "
        f"(default_policy={args.default_policy})."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return asyncio.run(_run(args))
    except GraphError as exc:
        # Erreur Graph globale (jeton, etc.) — message clair, jamais de secret.
        print(f"ERREUR Graph : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
