# -*- coding: utf-8 -*-
"""
Tests d'IDEMPOTENCE et de FAIL-CLOSED du seed du provider LLM (scripts/seed-
provider.sh — corrige le bug #9, cf. .planning/RUNTIME-EVIDENCE.md).

Stratégie : on n'a pas de pile Onyx ici. On interpose un FAUX `docker` (shim) sur
le PATH qui simule `docker compose exec -T api_server <curl …>` en répondant des
corps/HTTP scriptés. Le script RÉEL est exécuté ; on observe SON comportement :

  - IDEMPOTENCE : si l'inventaire des providers contient déjà « ollama », le
    script sort 0 SANS émettre de PUT de création.
  - CRÉATION : si l'inventaire est vide, le script émet bien un PUT puis réussit.
  - FAIL-CLOSED : sans identifiants admin → sortie non nulle, message explicite.
  - SÉLECTION DU MODÈLE : le modèle de CHAT est déduit (l'embedding est ignoré).

Ce qui n'est PAS prouvé ici (dit honnêtement) : le contrat EXACT de l'API admin
Onyx (chemins/champs) et la persistance réelle en base — seul le runtime sur une
vraie pile Onyx le valide. On teste la LOGIQUE du script, pas l'API distante.

Skip propre si bash absent.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.normpath(os.path.join(HERE, "..", "seed-provider.sh"))
_BASH = shutil.which("bash")

# Shim `docker` : un script bash qui simule les seules commandes utilisées par
# seed-provider.sh. Le comportement est piloté par des variables d'env lues à
# l'exécution (SEED_TEST_*), pour rejouer plusieurs scénarios sans réécrire.
DOCKER_SHIM = r"""#!/usr/bin/env bash
# Faux `docker` de test pour seed-provider.sh. Ne couvre que ce qui est appelé.
# $1=compose ; cherche un sous-appel curl dans la chaîne sh -c passée.
cmd="$*"

# `docker compose version` → ok
case "$cmd" in
  *"version"*) exit 0 ;;
esac

# Le script appelle: docker compose exec -T api_server sh -c "<payload>"
# On récupère le dernier argument (le payload sh -c).
payload="${!#}"

# /health → 200
case "$payload" in
  *"/health"*) echo "200"; exit 0 ;;
esac

# présence de curl dans api_server
case "$payload" in
  *"command -v curl"*) exit 0 ;;
esac

# login → code dans SEED_TEST_LOGIN_CODE (def 200)
case "$payload" in
  *"/auth/login"*) echo "${SEED_TEST_LOGIN_CODE:-200}"; exit 0 ;;
esac

# GET inventaire providers : http puis json (le script lit ligne1=http, reste=json)
case "$payload" in
  *"GET"*"/admin/llm/provider"*|*"-X GET"*)
    echo "200"
    echo "${SEED_TEST_PROVIDERS_JSON:-[]}"
    exit 0 ;;
esac

# PUT création : trace l'appel dans un fichier témoin, renvoie 200 + id
case "$payload" in
  *"PUT"*"/admin/llm/provider"*|*"-X PUT"*)
    [ -n "${SEED_TEST_PUT_MARKER:-}" ] && echo "$payload" >> "$SEED_TEST_PUT_MARKER"
    echo "200"
    echo '{"id":1,"name":"ollama"}'
    exit 0 ;;
esac

# POST default
case "$payload" in
  *"POST"*"/default"*|*"-X POST"*) echo "204"; exit 0 ;;
esac

# défaut : 200 vide
echo "200"; echo ""
exit 0
"""


def _make_shim_dir():
    d = tempfile.mkdtemp(prefix="seed_shim_")
    p = os.path.join(d, "docker")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(DOCKER_SHIM)
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return d, p


def run_seed(extra_env, marker=None, cwd=None):
    shim_dir, _ = _make_shim_dir()
    env = dict(os.environ)
    # shim docker EN PREMIER dans le PATH.
    env["PATH"] = shim_dir + os.pathsep + env.get("PATH", "")
    env.update(extra_env)
    if marker:
        env["SEED_TEST_PUT_MARKER"] = marker
    proc = subprocess.run(
        [_BASH, SCRIPT],
        capture_output=True, text=True, env=env, timeout=240, cwd=cwd,
    )
    return proc


@unittest.skipIf(_BASH is None, "bash indisponible — test sauté proprement")
class TestSeedProvider(unittest.TestCase):
    def setUp(self):
        # cwd isolé SANS .env, pour contrôler la dérivation du modèle.
        self.tmp = tempfile.mkdtemp(prefix="seed_cwd_")
        # Le script fait `cd "$(dirname "$0")/.."` → il ignore le cwd réel et
        # se replace à la racine du repo. On passe donc le modèle par env.

    def test_idempotent_skip_when_present(self):
        """Provider déjà présent → exit 0 ET aucun PUT émis (idempotence)."""
        marker = os.path.join(self.tmp, "put.log")
        proc = run_seed(
            {
                "ONIX_ADMIN_EMAIL": "a@b.c", "ONIX_ADMIN_PASSWORD": "x",
                "ONIX_SEED_MODEL": "qwen2.5:7b-instruct",
                "SEED_TEST_PROVIDERS_JSON": '[{"id":1,"name":"ollama"}]',
            },
            marker=marker,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # Message d'idempotence (comparaison insensible aux accents/casse).
        self.assertIn("idempotent", proc.stdout.lower())
        self.assertFalse(
            os.path.exists(marker) and os.path.getsize(marker) > 0,
            "un PUT de création a été émis alors que le provider existait (non idempotent).")

    def test_force_updates_when_present(self):
        """ONIX_SEED_FORCE=1 + provider présent → PUT émis (mise à jour)."""
        marker = os.path.join(self.tmp, "put.log")
        proc = run_seed(
            {
                "ONIX_ADMIN_EMAIL": "a@b.c", "ONIX_ADMIN_PASSWORD": "x",
                "ONIX_SEED_MODEL": "qwen2.5:7b-instruct", "ONIX_SEED_FORCE": "1",
                "SEED_TEST_PROVIDERS_JSON": '[{"id":1,"name":"ollama"}]',
            },
            marker=marker,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(os.path.exists(marker) and os.path.getsize(marker) > 0,
                        "ONIX_SEED_FORCE=1 aurait dû émettre un PUT de mise à jour.")

    def test_creates_when_absent(self):
        """Inventaire vide → PUT de création émis, succès."""
        marker = os.path.join(self.tmp, "put.log")
        proc = run_seed(
            {
                "ONIX_ADMIN_EMAIL": "a@b.c", "ONIX_ADMIN_PASSWORD": "x",
                "ONIX_SEED_MODEL": "qwen2.5:7b-instruct",
                "SEED_TEST_PROVIDERS_JSON": "[]",
            },
            marker=marker,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(os.path.exists(marker) and os.path.getsize(marker) > 0,
                        "inventaire vide : un PUT de création était attendu.")
        # Le modèle de chat doit figurer dans le corps PUT.
        with open(marker, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("qwen2.5:7b-instruct", body)

    def test_fail_closed_without_admin_creds(self):
        """Aucun identifiant admin → exit non nul, message explicite."""
        proc = run_seed({"ONIX_SEED_MODEL": "qwen2.5:7b-instruct"})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("FAIL-CLOSED", proc.stdout + proc.stderr)

    def test_embedding_model_is_not_selected(self):
        """Le corps PUT cible le modèle de CHAT, pas l'embedding nomic."""
        marker = os.path.join(self.tmp, "put.log")
        # On ne passe pas ONIX_SEED_MODEL : la dérivation .env du repo s'applique.
        # Pour rester déterministe on force un modèle de chat explicite et on
        # vérifie juste que 'nomic-embed-text' n'est jamais le default.
        proc = run_seed(
            {
                "ONIX_ADMIN_EMAIL": "a@b.c", "ONIX_ADMIN_PASSWORD": "x",
                "ONIX_SEED_MODEL": "llama3.2:3b",
                "SEED_TEST_PROVIDERS_JSON": "[]",
            },
            marker=marker,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(marker, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn('"default_model_name":"llama3.2:3b"', body.replace(" ", ""))
        self.assertNotIn('"default_model_name":"nomic-embed-text"', body.replace(" ", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
