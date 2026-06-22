# -*- coding: utf-8 -*-
"""
Tests AUTONOMES du CALCUL de tuning mémoire d'Ollama (scripts/detect-hardware.sh).

Pourquoi ces tests : le runtime Azure (cf. .planning/RUNTIME-EVIDENCE.md #10) a
PROUVÉ que `make tune` sous-dimensionnait `OLLAMA_MEM_LIMIT` (12 Go) pour
qwen2.5:14b dont l'empreinte RÉELLE en génération ≈ 20 Go → llama-server
OOM-killé (SIGKILL) sur un vrai prompt RAG. On verrouille ici, hors-runtime, le
contrat anti-OOM : un 14B retenu doit obtenir un plafond couvrant son pic réel.

Méthode : on EXÉCUTE le vrai script bash avec des surcharges `ONIX_FORCE_*`
(détection matérielle simulée — pas de mock du calcul lui-même), on parse les
lignes `KEY=VALEUR` du rapport, et on assert sur le profil produit. Aucun
matériel réel n'est touché ; le calcul testé est le code de production.

Skip propre si bash absent (poste Windows sans Git Bash) — jamais un faux vert.
"""
import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.normpath(os.path.join(HERE, "..", "detect-hardware.sh"))

_BASH = shutil.which("bash")


def _gb(value: str) -> float:
    """Convertit une valeur Docker (« 24g », « 512m ») en Go (float)."""
    value = value.strip()
    if value.endswith("g"):
        return float(value[:-1])
    if value.endswith("m"):
        return float(value[:-1]) / 1024.0
    return float(value)


def run_profile(ram_gb, cores=8, gpu="none", vram_gb=0):
    """Lance le script en mode rapport (lecture seule) avec matériel forcé.

    Retourne un dict {KEY: valeur} des réglages émis.
    """
    env = dict(os.environ)
    env["ONIX_FORCE_RAM_GB"] = str(ram_gb)
    env["ONIX_FORCE_CORES"] = str(cores)
    env["ONIX_FORCE_GPU"] = gpu
    env["ONIX_SKIP_DOCKER"] = "1"   # n'appelle jamais `docker info` (lent/bloquant)
    if vram_gb:
        env["ONIX_FORCE_VRAM_GB"] = str(vram_gb)
    # Pas d'argument --apply : rapport seul, n'écrit aucun .env. Timeout large :
    # le démarrage de bash sur un chemin OneDrive peut être lent (pas le calcul).
    proc = subprocess.run(
        [_BASH, SCRIPT],
        capture_output=True, text=True, env=env, timeout=240,
    )
    assert proc.returncode == 0, f"script échec rc={proc.returncode}\n{proc.stderr}\n{proc.stdout}"
    out = proc.stdout
    settings = {}
    for line in out.splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)=(.+)$", line)
        if m:
            settings[m.group(1)] = m.group(2).strip()
    return settings, out


@unittest.skipIf(_BASH is None, "bash indisponible (Git Bash requis) — test sauté proprement")
class TestOllamaMemSizing(unittest.TestCase):
    # --- Cœur du bug #10 : la VM Azure (64 Go / 16 vCPU / CPU) ----------------
    def test_azure_vm_14b_gets_safe_floor(self):
        """64 Go CPU : doit retenir un 14B ET lui donner ≥ 24 Go (anti-OOM)."""
        s, out = run_profile(ram_gb=64, cores=16, gpu="none")
        self.assertIn("14b", s["OLLAMA_MODELS_TO_PULL"],
                      f"un 64 Go doit pouvoir servir un 14B. Profil:\n{out}")
        mem = _gb(s["OLLAMA_MEM_LIMIT"])
        self.assertGreaterEqual(
            mem, 24.0,
            f"REGRESSION #10 : OLLAMA_MEM_LIMIT={s['OLLAMA_MEM_LIMIT']} < 24g pour "
            f"un 14B (empreinte réelle ~20 Go). Profil:\n{out}")

    def test_14b_never_below_real_footprint(self):
        """Quel que soit le palier RAM, si un 14B est choisi son plafond ≥ 20 Go."""
        for ram in (48, 96):
            s, out = run_profile(ram_gb=ram, cores=16, gpu="none")
            if "14b" in s["OLLAMA_MODELS_TO_PULL"]:
                mem = _gb(s["OLLAMA_MEM_LIMIT"])
                self.assertGreaterEqual(
                    mem, 20.0,
                    f"RAM={ram} : 14B avec plafond {s['OLLAMA_MEM_LIMIT']} < 20 Go.\n{out}")

    # --- Cohérence modèle ↔ plafond : jamais un gros modèle sous-alloué -------
    def test_model_fits_its_ceiling(self):
        """Le modèle retenu doit toujours tenir dans OLLAMA_MEM_LIMIT."""
        need = {"1b": 3, "3b": 5, "7b": 12, "14b": 22, "32b": 40}
        for ram in (16, 32, 64):
            s, out = run_profile(ram_gb=ram, cores=8, gpu="none")
            model = s["OLLAMA_MODELS_TO_PULL"]
            mem = _gb(s["OLLAMA_MEM_LIMIT"])
            for tag, req in need.items():
                if tag in model:
                    # Plafond ≥ empreinte réelle - petite tolérance (la boucle
                    # anti-OOM ne descend jamais un modèle sous son besoin : elle
                    # rétrograde le modèle à la place).
                    self.assertGreaterEqual(
                        mem, req,
                        f"RAM={ram} : modèle {tag} (besoin {req} Go) mais plafond "
                        f"{s['OLLAMA_MEM_LIMIT']}.\n{out}")
                    break

    # --- Garantie anti-OOM globale : somme des limites < RAM ------------------
    def test_sum_of_limits_below_ram(self):
        """La somme de TOUTES les *_MEM_LIMIT reste < RAM physique."""
        keys = [
            "OLLAMA_MEM_LIMIT", "OPENSEARCH_MEM_LIMIT", "INFERENCE_MEM_LIMIT",
            "BACKGROUND_MEM_LIMIT", "API_SERVER_MEM_LIMIT", "WEB_MEM_LIMIT",
            "POSTGRES_MEM_LIMIT", "MINIO_MEM_LIMIT", "NGINX_MEM_LIMIT",
        ]
        for ram in (16, 32, 64):
            s, out = run_profile(ram_gb=ram, cores=8, gpu="none")
            total = sum(_gb(s[k]) for k in keys if k in s)
            # redis 256m fixe non émis comme ligne ? il l'est dans le tableau,
            # mais REDIS_MEM_LIMIT n'a pas de ligne emit → on ajoute son 0.25.
            total += 0.25
            self.assertLess(
                total, float(ram),
                f"RAM={ram} : somme limites {total:.2f} Go >= RAM (sur-allocation).\n{out}")

    # --- Petite RAM : avertissement fail-closed bruyant -----------------------
    def test_small_ram_warns_not_silent(self):
        """4 Go CPU : profil dégradé MAIS avertissement explicite, jamais muet."""
        s, out = run_profile(ram_gb=4, cores=2, gpu="none")
        # Le plus petit modèle est retenu ; on n'exige pas qu'il tienne, on exige
        # que le script PRÉVIENNE (anti « avalement silencieux »).
        self.assertTrue(
            ("⚠" in out) or ("trop juste" in out.lower()),
            f"4 Go : aucun avertissement de capacité émis (fail-closed informatif).\n{out}")

    # --- GPU : empreinte RAM hôte faible (poids en VRAM) ----------------------
    def test_gpu_low_host_ram(self):
        """GPU 24 Go : plafond RAM hôte Ollama modeste (poids en VRAM)."""
        s, out = run_profile(ram_gb=32, cores=8, gpu="nvidia", vram_gb=24)
        self.assertLessEqual(
            _gb(s["OLLAMA_MEM_LIMIT"]), 8.0,
            f"GPU : RAM hôte Ollama devrait rester basse.\n{out}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
