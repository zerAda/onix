#!/usr/bin/env python3
"""check-docs-map.py — validateur STRUCTUREL de l'infra de documentation agents.

Source de vérité : `docs/scopes/scopes.json` (registre des scopes). Sans dépendance
externe (stdlib pure). Vérifie que la « doc-infra » reste saine et NON-divergente :

  1. Registre ↔ fichiers : chaque scope déclaré a son `dossier`, son `audit` et son
     `state` (existants), et ses préfixes `code` existent sur disque (anti-renommage).
  2. Conformité du gabarit : chaque dossier de scope contient les N sections
     numérotées attendues (`## 1.` … `## N.`) — pour que l'agent trouve toujours
     les mêmes rubriques.
  3. Liens de navigation : tous les liens Markdown **relatifs** des fichiers de
     navigation (CLAUDE.md, AGENTS.md, DOCS_INDEX.md, docs/scopes/*.md, llms.txt)
     résolvent (0 lien mort) — y compris les chemins de CODE cités dans les dossiers.
  4. Orphelins (avertissement) : `docs/*.md` non référencés par l'index ni un dossier.

Codes de sortie : 0 = OK ; 1 = erreur (registre incohérent, section manquante,
lien mort, fichier déclaré absent). Usage : `python scripts/check-docs-map.py`.
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "docs", "scopes", "scopes.json")

_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def load_registry() -> dict:
    with open(REGISTRY, encoding="utf-8") as fh:
        return json.load(fh)


def _targets(md_path: str) -> list[str]:
    """Cibles de liens relatifs d'un .md (hors http(s)/mailto/ancres pures)."""
    with open(md_path, encoding="utf-8") as fh:
        text = fh.read()
    out: list[str] = []
    for raw in _LINK_RE.findall(text):
        target = raw.strip()
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#", 1)[0].split(" ", 1)[0].strip()
        if target:
            out.append(target)
    return out


def check_registry(reg: dict) -> list[str]:
    """Cohérence registre ↔ disque : fichiers déclarés présents, préfixes code réels."""
    errs: list[str] = []
    for name, spec in reg["scopes"].items():
        for key in ("dossier", "audit", "state"):
            rel = spec.get(key)
            if not rel:
                errs.append(f"scope '{name}' : champ '{key}' manquant dans le registre")
                continue
            if not os.path.isfile(os.path.join(ROOT, rel)):
                errs.append(f"scope '{name}' : {key} déclaré mais absent → {rel}")
        for prefix in spec.get("code", []):
            if not os.path.exists(os.path.join(ROOT, prefix)):
                errs.append(f"scope '{name}' : préfixe code introuvable → {prefix}")
    return errs


def check_sections(reg: dict) -> list[str]:
    """Chaque dossier doit contenir les N sections numérotées du gabarit."""
    n = int(reg.get("required_sections", 0))
    errs: list[str] = []
    for name, spec in reg["scopes"].items():
        path = os.path.join(ROOT, spec["dossier"])
        if not os.path.isfile(path):
            continue  # déjà signalé par check_registry
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for i in range(1, n + 1):
            if not re.search(rf"^## {i}\.", text, re.MULTILINE):
                errs.append(f"dossier '{spec['dossier']}' : section '## {i}.' absente")
    return errs


def nav_files(reg: dict) -> list[str]:
    files = ["CLAUDE.md", "AGENTS.md", "docs/DOCS_INDEX.md", "docs/scopes/README.md", "llms.txt"]
    files += [spec["dossier"] for spec in reg["scopes"].values()]
    return [f for f in files if os.path.isfile(os.path.join(ROOT, f))]


def check_links(reg: dict) -> list[str]:
    """Tous les liens relatifs des fichiers de navigation doivent résoudre."""
    errs: list[str] = []
    for rel in nav_files(reg):
        src = os.path.join(ROOT, rel)
        base = os.path.dirname(src)
        for target in _targets(src):
            resolved = os.path.normpath(os.path.join(base, target))
            if not os.path.exists(resolved):
                errs.append(f"lien mort dans {rel} -> {target}")
    return errs


def check_orphans(reg: dict) -> list[str]:
    """Avertissement : docs/*.md non référencés par l'index ni les dossiers."""
    referenced: set[str] = set()
    sources = [os.path.join(ROOT, "docs", "DOCS_INDEX.md"),
               os.path.join(ROOT, "docs", "scopes", "README.md")]
    sources += [os.path.join(ROOT, spec["dossier"]) for spec in reg["scopes"].values()]
    for src in sources:
        if not os.path.isfile(src):
            continue
        base = os.path.dirname(src)
        for target in _targets(src):
            referenced.add(os.path.normpath(os.path.join(base, target)))
    warns: list[str] = []
    docs_dir = os.path.join(ROOT, "docs")
    for name in sorted(os.listdir(docs_dir)):
        if not name.endswith(".md") or name == "DOCS_INDEX.md":
            continue
        p = os.path.join(docs_dir, name)
        if os.path.isfile(p) and p not in referenced:
            warns.append(f"doc potentiellement orpheline (non indexée) : docs/{name}")
    return warns


def main() -> int:
    try:
        reg = load_registry()
    except (OSError, ValueError) as exc:
        print(f"✗ registre illisible ({REGISTRY}) : {exc}")
        return 1

    errors = check_registry(reg) + check_sections(reg) + check_links(reg)
    warnings = check_orphans(reg)

    for w in warnings:
        print(f"  ⚠ {w}")
    if errors:
        print(f"\n✗ check-docs-map : {len(errors)} erreur(s) :")
        for e in errors:
            print(f"  ✗ {e}")
        return 1
    print(
        f"✓ check-docs-map : {len(reg['scopes'])} scopes (registre), gabarit conforme, "
        f"liens de navigation valides"
        + (f" ({len(warnings)} avertissement(s))" if warnings else "")
        + "."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
