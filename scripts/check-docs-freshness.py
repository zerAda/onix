#!/usr/bin/env python3
"""check-docs-freshness.py — garde anti-DRIFT doc↔code (« à chaque action, vérifie »).

Principe (docs-as-code, état de l'art 2025) : du code et sa doc évoluent sur des
cycles séparés → la doc dérive. Ce garde **refuse** une modification qui touche le
code d'un scope **sans** toucher AU MOINS un de ses fichiers de doc agent
(`dossier` / `audit` / `state`, cf. `docs/scopes/scopes.json`). C'est le « verify
if update needed » exécutable, à brancher en pre-commit ET en CI.

Modes :
  * défaut    : compare une BASE (arg 1, ou $ONIX_DOCS_BASE, ou `origin/main`) à HEAD
                — usage CI/PR et revue locale.
  * --staged  : compare l'index (staged) à HEAD — usage hook pre-commit.

Dérogation (rare, justifiée) : inclure `[docs-skip]` (tous) ou
`[docs-skip:<scope>,<scope>]` dans un message de commit de l'intervalle, OU exporter
`ONIX_DOCS_SKIP=all` / `ONIX_DOCS_SKIP=<scope>,<scope>`.

Robustesse : si git est indisponible ou la BASE introuvable (clone superficiel),
le garde **n'échoue pas** (exit 0 + avertissement) — il ne bloque jamais sur un
problème d'historique. Exit 1 UNIQUEMENT sur un vrai drift non dérogé.
"""
from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - appels git à arguments fixes, jamais shell=True
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "docs", "scopes", "scopes.json")


def _git(args: list[str]) -> str | None:
    """Exécute `git <args>` (liste fixe, pas de shell). None si échec."""
    try:
        out = subprocess.run(  # nosec B603 B607
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
        )
    except (OSError, ValueError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def _changed_files(staged: bool, base: str) -> list[str] | None:
    if staged:
        out = _git(["diff", "--cached", "--name-only"])
        return out.splitlines() if out is not None else None
    # Vérifie que la base est résolvable, sinon on dégrade proprement.
    if _git(["rev-parse", "--verify", "--quiet", base]) is None:
        return None
    out = _git(["diff", "--name-only", f"{base}...HEAD"])
    return out.splitlines() if out is not None else None


def _skip_set(staged: bool, base: str) -> set[str]:
    """Scopes dérogés via $ONIX_DOCS_SKIP ou marqueur [docs-skip[:..]] dans les commits."""
    skip: set[str] = set()
    env = os.environ.get("ONIX_DOCS_SKIP", "").strip()
    if env:
        skip.update("__all__" if env.lower() == "all" else s.strip() for s in env.split(","))
    if not staged:
        msgs = _git(["log", "--format=%B", f"{base}..HEAD"]) or ""
        for token in ("[docs-skip]",):
            if token in msgs:
                skip.add("__all__")
        # [docs-skip:a,b]
        import re

        for m in re.findall(r"\[docs-skip:([^\]]+)\]", msgs):
            skip.update(s.strip() for s in m.split(","))
    return skip


def main() -> int:
    staged = "--staged" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    base = args[0] if args else os.environ.get("ONIX_DOCS_BASE", "origin/main")

    try:
        with open(REGISTRY, encoding="utf-8") as fh:
            reg = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"✗ registre illisible : {exc}")
        return 1

    changed = _changed_files(staged, base)
    if changed is None:
        print(f"⚠ check-docs-freshness : base '{base}' non résolvable / git indisponible "
              "→ contrôle ignoré (non bloquant).")
        return 0
    changed = [c for c in changed if c]
    if not changed:
        print("✓ check-docs-freshness : aucun fichier modifié.")
        return 0

    skip = _skip_set(staged, base)
    violations: list[str] = []
    for name, spec in reg["scopes"].items():
        if "__all__" in skip or name in skip:
            continue
        prefixes = spec.get("code", [])
        if not prefixes:
            continue  # scope transverse : pas de déclencheur de code
        code_touched = [f for f in changed if any(f.startswith(p) for p in prefixes)]
        if not code_touched:
            continue
        doc_paths = {spec.get(k) for k in ("dossier", "audit", "state")}
        doc_touched = any(f in doc_paths for f in changed)
        if not doc_touched:
            violations.append(
                f"scope '{name}' : code modifié ({len(code_touched)} fichier(s), ex. "
                f"{code_touched[0]}) sans MAJ de sa doc agent "
                f"({spec['dossier']} / {spec['audit']} / {spec['state']})"
            )

    if violations:
        print(f"✗ check-docs-freshness : {len(violations)} drift(s) doc↔code :")
        for v in violations:
            print(f"  ✗ {v}")
        print(
            "\n→ Mets à jour la doc du/des scope(s) touché(s) (carte du code, preuve "
            "fichier:ligne dans audit-reality, journal state), OU justifie une "
            "dérogation : [docs-skip:<scope>] dans le message de commit."
        )
        return 1

    print("✓ check-docs-freshness : toute modif de code de scope s'accompagne d'une MAJ doc.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
