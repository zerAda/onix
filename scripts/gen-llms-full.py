#!/usr/bin/env python3
"""gen-llms-full.py — génère `llms-full.txt` (carte agent à CONTENU EMBARQUÉ).

Standard llms.txt : `llms.txt` = carte compacte avec liens ; `llms-full.txt` =
**tout le contenu d'orientation embarqué** pour un agent SANS accès fichiers (un
seul bloc à lire). On le GÉNÈRE (au lieu de le maintenir à la main) pour qu'il ne
DÉRIVE jamais : il concatène, dans un ordre déterministe, la doc d'orientation +
les dossiers de scope du registre `docs/scopes/scopes.json`.

Usage :
  python scripts/gen-llms-full.py           # (ré)écrit llms-full.txt
  python scripts/gen-llms-full.py --check    # exit 1 si llms-full.txt est périmé
                                             # (appelé par check-docs-map.py → CI/pre-commit)
Stdlib pure.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "docs", "scopes", "scopes.json")
OUTPUT = os.path.join(ROOT, "llms-full.txt")

# Doc d'orientation embarquée AVANT les dossiers de scope (ordre déterministe).
ORIENTATION = ["AGENTS.md", "CLAUDE.md", "docs/scopes/README.md"]

_BANNER = (
    "# onix — llms-full.txt (carte agent, CONTENU EMBARQUÉ)\n"
    "# FICHIER GÉNÉRÉ — NE PAS ÉDITER À LA MAIN. Régénérer : `make llms-full`.\n"
    "# Source : scripts/gen-llms-full.py (orientation + dossiers de scope du registre).\n"
    "# Pour un agent sans accès fichiers : tout le contexte d'orientation en un bloc.\n"
)


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read().rstrip("\n")


def build_content() -> str:
    reg = json.load(open(REGISTRY, encoding="utf-8"))
    files = list(ORIENTATION) + [spec["dossier"] for spec in reg["scopes"].values()]

    parts: list[str] = [_BANNER]
    # Sommaire.
    parts.append("\n## Sommaire (fichiers embarqués)\n")
    for rel in files:
        parts.append(f"- {rel}")
    # Sections.
    for rel in files:
        parts.append(
            "\n\n" + "=" * 78 + f"\n# FICHIER EMBARQUÉ : {rel}\n" + "=" * 78 + "\n\n" + _read(rel)
        )
    return "\n".join(parts).rstrip("\n") + "\n"


def main() -> int:
    content = build_content()
    if "--check" in sys.argv:
        try:
            current = open(OUTPUT, encoding="utf-8").read()
        except OSError:
            current = None
        if current != content:
            print("✗ llms-full.txt est PÉRIMÉ (doc d'orientation/dossiers modifiés). "
                  "Régénère-le : `make llms-full`.")
            return 1
        print("✓ llms-full.txt à jour.")
        return 0
    with open(OUTPUT, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"✓ llms-full.txt généré ({len(content)} octets).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
