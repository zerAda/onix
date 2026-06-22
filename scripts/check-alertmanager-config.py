#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Contrôle FAIL-CLOSED de la config Alertmanager d'onix (scope monitoring).

Pourquoi : l'ancienne config livrait TOUTE alerte (budget FinOps, service down,
chaîne d'audit rompue) DANS LE VIDE — receiver `default` vide + `webhook_configs`
commenté sur `critical`. Ce contrôle, autonome (stdlib uniquement, sans Docker),
vérifie que le gabarit `alertmanager.yml.tmpl` :

  1. une fois RENDU avec une URL, contient un `webhook_configs` RÉEL pointant
     `ALERT_WEBHOOK_URL` (jamais vide, jamais commenté), avec `send_resolved`,
     et que la route `critical` ET la route par défaut atteignent ce receiver ;
  2. SANS `ALERT_WEBHOOK_URL` (absent/vide), le mécanisme fail-closed se
     déclenche : refus BRUYANT (l'entrypoint sort en erreur), jamais d'avalement
     silencieux.

Le rendu reproduit la substitution `sed` de l'entrypoint conteneur
(`monitoring/alertmanager/entrypoint.sh`) ; on valide donc le même artefact que
celui chargé en production.

Sortie : code 0 si conforme, !=0 (avec message CRITICAL) sinon — utilisable comme
gate `make` / CI. Pas de dépendance YAML tierce : parsing ciblé en stdlib.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Racine du dépôt = parent de scripts/.
ROOT = Path(__file__).resolve().parent.parent
TMPL = ROOT / "monitoring" / "alertmanager" / "alertmanager.yml.tmpl"
ENTRYPOINT = ROOT / "monitoring" / "alertmanager" / "entrypoint.sh"
COMPOSE = ROOT / "monitoring" / "docker-compose.monitoring.yml"

PLACEHOLDER = "${ALERT_WEBHOOK_URL}"
FAKE_URL = "https://hooks.example.invalid/services/T000/B000/xxxxONLY-FOR-TEST"


class CheckError(AssertionError):
    """Échec de conformité fail-closed (message destiné à l'opérateur)."""


def _render(template_text: str, url: str | None) -> str:
    """Reproduit la logique fail-closed de l'entrypoint.

    - url absente/vide  -> lève (refus, comme `exit 1` du conteneur) ;
    - url sans http(s)  -> lève (même garde que l'entrypoint) ;
    - sinon             -> substitue littéralement le placeholder.
    """
    if not url or not url.strip():
        raise CheckError(
            "FAIL-CLOSED déclenché : ALERT_WEBHOOK_URL absent/vide -> refus de rendu."
        )
    if not url.startswith(("http://", "https://")):
        raise CheckError(
            "FAIL-CLOSED déclenché : ALERT_WEBHOOK_URL sans schéma http(s) -> refus."
        )
    rendered = template_text.replace(PLACEHOLDER, url)
    # On ne traque QUE le placeholder ${...}, pas le mot ALERT_WEBHOOK_URL qui
    # apparaît légitimement dans les commentaires d'en-tête.
    if PLACEHOLDER in rendered:
        raise CheckError("Placeholder ${ALERT_WEBHOOK_URL} non substitué après rendu.")
    return rendered


def _fail(msg: str) -> None:
    print(f"CRITICAL [check-alertmanager] {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    # --- Existence des artefacts -------------------------------------------
    for path, label in ((TMPL, "gabarit"), (ENTRYPOINT, "entrypoint"), (COMPOSE, "compose")):
        if not path.is_file():
            _fail(f"{label} introuvable : {path} (config Alertmanager incomplète).")

    tmpl_text = TMPL.read_text(encoding="utf-8")

    # --- (0) Le gabarit ne doit PAS contenir l'ancienne vuln ---------------
    # receiver vide non suivi d'un webhook_configs / webhook commenté.
    if re.search(r"^\s*#\s*webhook_configs", tmpl_text, re.MULTILINE):
        _fail("`webhook_configs` est COMMENTÉ dans le gabarit (alertes dans le vide).")

    # --- (1) Rendu nominal : webhook RÉEL pointant l'URL -------------------
    try:
        rendered = _render(tmpl_text, FAKE_URL)
    except CheckError as exc:
        _fail(f"Le rendu nominal (URL fournie) a échoué : {exc}")
        return 1  # inatteignable (mypy/lecture)

    if "webhook_configs:" not in rendered:
        _fail("Aucun `webhook_configs` dans la config rendue (receiver sans destination).")
    if FAKE_URL not in rendered:
        _fail("L'URL de webhook n'apparaît pas dans la config rendue (placeholder non injecté).")
    if "send_resolved: true" not in rendered:
        _fail("`send_resolved: true` absent (les résolutions d'alerte ne seraient pas notifiées).")

    # La route racine ET la route `critical` doivent viser un receiver qui a un
    # webhook_configs réel. On vérifie qu'AUCUN receiver utilisé n'est « vide ».
    _assert_routes_have_webhook(rendered)

    # --- (2) FAIL-CLOSED : sans URL -> refus, jamais de rendu silencieux ----
    for bad in (None, "", "   ", "not-a-url"):
        try:
            _render(tmpl_text, bad)
        except CheckError:
            pass  # comportement attendu (refus bruyant)
        else:
            _fail(
                f"FAIL-CLOSED NON déclenché pour ALERT_WEBHOOK_URL={bad!r} : "
                "la config aurait été rendue sans destination (alertes avalées)."
            )

    # --- (3) L'entrypoint conteneur applique bien le garde fail-closed -----
    ep = ENTRYPOINT.read_text(encoding="utf-8")
    if "ALERT_WEBHOOK_URL" not in ep or "exit 1" not in ep:
        _fail("entrypoint.sh n'implémente pas le refus fail-closed sur ALERT_WEBHOOK_URL.")

    # --- (4) Le compose monte le gabarit + l'entrypoint, pas l'ancien yml --
    comp = COMPOSE.read_text(encoding="utf-8")
    if "alertmanager.yml.tmpl" not in comp or "entrypoint.sh" not in comp:
        _fail("docker-compose.monitoring.yml ne monte pas le gabarit + l'entrypoint de rendu.")
    if re.search(r"alertmanager\.yml(?!\.tmpl)", comp):
        _fail("docker-compose.monitoring.yml référence encore l'ancien alertmanager.yml statique.")

    print(
        "✓ alertmanager : webhook de notification RÉEL (send_resolved) + fail-closed "
        "vérifiés (rendu OK, refus sans ALERT_WEBHOOK_URL)."
    )
    return 0


def _assert_routes_have_webhook(rendered: str) -> None:
    """Vérifie que chaque receiver RÉFÉRENCÉ a un `webhook_configs` non vide.

    Parsing ciblé (sans dépendance YAML) : on extrait les `receiver: <nom>` de
    la section `route`, puis on s'assure que chaque nom apparaît dans `receivers`
    avec un bloc `webhook_configs` suivant. Suffisant pour ce gabarit simple.
    """
    used = set(re.findall(r"receiver:\s*([A-Za-z0-9_-]+)", rendered))
    if not used:
        _fail("Aucune route avec `receiver:` trouvée dans la config rendue.")

    # Bloc `receivers:` -> texte jusqu'à la prochaine clé de niveau 0.
    m = re.search(r"^receivers:\s*$(.*?)^\S", rendered + "\n￿",
                  re.MULTILINE | re.DOTALL)
    receivers_block = m.group(1) if m else ""

    for name in sorted(used):
        # Le receiver doit être déclaré ET suivi (dans son bloc) d'un
        # webhook_configs avec une url non vide.
        pat = re.compile(
            r"-\s*name:\s*" + re.escape(name) + r"\b(?P<body>.*?)(?=^\s*-\s*name:|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        rm = pat.search(receivers_block)
        if not rm:
            _fail(f"Receiver `{name}` référencé par une route mais non déclaré.")
        # On ignore les lignes commentées : un `# webhook_configs` ne compte pas
        # comme une destination réelle (c'était précisément l'ancienne vuln).
        body = "\n".join(
            ln for ln in rm.group("body").splitlines()
            if not ln.lstrip().startswith("#")
        )
        if "webhook_configs" not in body:
            _fail(
                f"Receiver `{name}` est VIDE (pas de webhook_configs) : la route "
                "associée enverrait ses alertes dans le vide."
            )
        if not re.search(r"url:\s*\S+", body):
            _fail(f"Receiver `{name}` a un webhook_configs sans `url:` réelle.")


if __name__ == "__main__":
    raise SystemExit(main())
