#!/usr/bin/env python3
"""check-docs-map.py — validateur de l'infra de documentation pour agents.

Vérifie, sans dépendance externe (stdlib pure), que la « doc-infra » reste saine :

  1. CHAQUE scope a son dossier `docs/scopes/<scope>.md` (sentinelle).
  2. Les liens Markdown **relatifs** des fichiers de navigation (CLAUDE.md, AGENTS.md,
     docs/DOCS_INDEX.md, docs/scopes/*.md) **résolvent** vers un fichier/dossier existant
     (0 lien mort) — c'est l'invariant « quand on cherche un sujet, on tombe sur le bon md ».
  3. (Avertissement, non bloquant) les `docs/*.md` non référencés par l'index ou les
     dossiers de scope (orphelins potentiels).

Codes de sortie : 0 = OK ; 1 = lien mort ou dossier de scope manquant.

Usage : `python scripts/check-docs-map.py` (ou `make docs-check`).
"""
from __future__ import annotations

import os
import re
import sys

# Racine du dépôt = parent de scripts/.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Les 6 scopes du projet (cf. ralph/scopes, docs/audit-reality).
SCOPES = [
    "access-gateway",
    "actions",
    "rag-prompts",
    "monitoring",
    "deploy-ops",
    "security-governance",
]

# Fichiers de navigation dont les liens DOIVENT résoudre (cœur de la doc-infra).
NAV_FILES = [
    "CLAUDE.md",
    "AGENTS.md",
    "docs/DOCS_INDEX.md",
] + [f"docs/scopes/{s}.md" for s in SCOPES] + ["docs/scopes/README.md"]

# Lien Markdown : [texte](cible). On ignore le texte, on garde la cible.
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _targets(md_path: str) -> list[str]:
    """Cibles de liens relatifs d'un fichier .md (hors http(s)/mailto/ancres pures)."""
    with open(md_path, encoding="utf-8") as fh:
        text = fh.read()
    out: list[str] = []
    for raw in _LINK_RE.findall(text):
        target = raw.strip()
        # Ignore les liens externes, mailto et ancres internes pures.
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        # Retire une éventuelle ancre (#section) et les espaces de titre.
        target = target.split("#", 1)[0].split(" ", 1)[0].strip()
        if target:
            out.append(target)
    return out


def check_scope_dossiers() -> list[str]:
    """Chaque scope doit avoir son dossier agent."""
    errs = []
    for s in SCOPES:
        p = os.path.join(ROOT, "docs", "scopes", f"{s}.md")
        if not os.path.isfile(p):
            errs.append(f"dossier de scope MANQUANT : docs/scopes/{s}.md")
    return errs


def check_links() -> list[str]:
    """Tous les liens relatifs des fichiers de navigation doivent résoudre."""
    errs = []
    for rel in NAV_FILES:
        src = os.path.join(ROOT, rel)
        if not os.path.isfile(src):
            errs.append(f"fichier de navigation absent : {rel}")
            continue
        base = os.path.dirname(src)
        for target in _targets(src):
            resolved = os.path.normpath(os.path.join(base, target))
            if not os.path.exists(resolved):
                errs.append(f"lien mort dans {rel} -> {target}")
    return errs


def check_orphans() -> list[str]:
    """Avertissement : docs/*.md non référencés par l'index ni les dossiers de scope."""
    referenced: set[str] = set()
    sources = [os.path.join(ROOT, "docs", "DOCS_INDEX.md")] + [
        os.path.join(ROOT, "docs", "scopes", f"{s}.md") for s in SCOPES
    ] + [os.path.join(ROOT, "docs", "scopes", "README.md")]
    for src in sources:
        if not os.path.isfile(src):
            continue
        base = os.path.dirname(src)
        for target in _targets(src):
            referenced.add(os.path.normpath(os.path.join(base, target)))
    warns = []
    docs_dir = os.path.join(ROOT, "docs")
    for name in sorted(os.listdir(docs_dir)):
        if not name.endswith(".md") or name in {"DOCS_INDEX.md"}:
            continue
        p = os.path.join(docs_dir, name)
        if os.path.isfile(p) and p not in referenced:
            warns.append(f"doc potentiellement orpheline (non indexée) : docs/{name}")
    return warns


def main() -> int:
    errors = check_scope_dossiers() + check_links()
    warnings = check_orphans()

    for w in warnings:
        print(f"  ⚠ {w}")
    if errors:
        print(f"\n✗ check-docs-map : {len(errors)} erreur(s) :")
        for e in errors:
            print(f"  ✗ {e}")
        return 1
    print(
        f"✓ check-docs-map : {len(SCOPES)} dossiers de scope présents, "
        f"liens de navigation valides"
        + (f" ({len(warnings)} avertissement(s))" if warnings else "")
        + "."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
