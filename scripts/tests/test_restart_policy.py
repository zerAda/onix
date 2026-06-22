# -*- coding: utf-8 -*-
"""
Validation YAML de la POLITIQUE DE REDÉMARRAGE des services critiques
(durcissement du trou de résilience #6, cf. .planning/RUNTIME-EVIDENCE.md).

Contexte runtime : `api_server` tué PENDANT son init est resté `exited (137)`,
`restart: always` ne l'a pas rattrapé (course étroite au démarrage). Le reboot
complet récupère, mais le kill-pendant-init est le trou. On verrouille ici, en
statique, le CONTRAT d'auto-réparation que l'overlay prod-local DOIT garantir :

  1. TOUS les services critiques portent `restart: always` dans l'overlay
     prod-local (pas `unless-stopped`/`no`) → Docker relance après tout exit, y
     compris un SIGKILL pendant l'init.
  2. `api_server` (le plus critique, init = migrations Alembic) a un healthcheck
     avec un `start_period` qui couvre la fenêtre d'init (≥120s) → l'état de
     santé reflète l'init et le démarrage ordonné en dépend.

Ces invariants sont la défense documentée + testable contre #6. La reprise après
un kill ciblé pendant l'init reste un comportement RUNTIME du démon Docker (non
simulable ici sans démarrer la pile) — dit honnêtement, pas « testé ».

stdlib + PyYAML (déjà dans le toolchain du repo). Gère les tags compose
`!reset`/`!override`/`!override`.
"""
import os
import unittest

import yaml

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
PROD_LOCAL = os.path.join(ROOT, "docker-compose.prod-local.yml")
BASE = os.path.join(ROOT, "docker-compose.yml")

# Services dont la panne casse le service rendu (cœur de la pile prod-local).
CRITICAL = [
    "relational_db", "opensearch", "cache", "minio",
    "inference_model_server", "ollama", "api_server",
    "background", "web_server", "nginx",
]


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader tolérant aux tags d'override/reset de docker-compose."""


def _passthrough(loader, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


for _tag in ("!reset", "!override"):
    _ComposeLoader.add_constructor(_tag, _passthrough)


def load_compose(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.load(fh, Loader=_ComposeLoader) or {}


class TestProdLocalRestartPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.overlay = load_compose(PROD_LOCAL)
        cls.base = load_compose(BASE)
        cls.services = cls.overlay.get("services", {})

    def test_all_critical_services_present_in_overlay(self):
        """Chaque service critique est durci par l'overlay prod-local."""
        for svc in CRITICAL:
            self.assertIn(
                svc, self.services,
                f"service critique '{svc}' absent de docker-compose.prod-local.yml "
                f"→ non durci (pas de restart: always).")

    def test_critical_services_restart_always(self):
        """Invariant #6 : restart=always (pas unless-stopped/no) partout."""
        for svc in CRITICAL:
            policy = self.services.get(svc, {}).get("restart")
            self.assertEqual(
                policy, "always",
                f"service '{svc}' : restart='{policy}' attendu 'always'. "
                f"Sans cela, un exit pendant l'init n'est pas rattrapé (trou #6).")

    def test_api_server_healthcheck_covers_init(self):
        """api_server : healthcheck présent + start_period >= 120s (couvre Alembic)."""
        api = self.services.get("api_server", {})
        hc = api.get("healthcheck")
        self.assertIsInstance(
            hc, dict, "api_server doit avoir un healthcheck (état de santé = init réel).")
        sp = str(hc.get("start_period", "0s")).strip().lower()
        # Parse simple « 180s » / « 3m ».
        if sp.endswith("ms"):
            secs = float(sp[:-2]) / 1000.0
        elif sp.endswith("s"):
            secs = float(sp[:-1])
        elif sp.endswith("m"):
            secs = float(sp[:-1]) * 60.0
        else:
            secs = float(sp or 0)
        self.assertGreaterEqual(
            secs, 120.0,
            f"api_server start_period={sp} < 120s : trop court pour couvrir "
            f"l'init (migrations + uvicorn) → faux 'unhealthy' pendant le boot.")

    def test_api_server_ordered_start_depends_healthy(self):
        """api_server attend ses dépendances de données SAINES (anti-course init)."""
        api = self.services.get("api_server", {})
        deps = api.get("depends_on", {})
        self.assertIsInstance(deps, dict, "api_server depends_on doit être en forme longue.")
        for need in ("relational_db", "opensearch", "cache"):
            cond = deps.get(need, {})
            self.assertEqual(
                cond.get("condition"), "service_healthy",
                f"api_server doit attendre {need}: service_healthy avant alembic.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
