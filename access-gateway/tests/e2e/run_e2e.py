#!/usr/bin/env python3
"""run_e2e — PREUVE E2E des garde-fous À TRAVERS LE CODE DE SERVICE DÉPLOYÉ.

Pipeline réel monté par ce script :

    client HTTP
        │  POST /v1/chat/send-message (X-OIDC-Claims vérifiés simulés)
        ▼
    access-gateway (uvicorn, code DÉPLOYÉ)
        │  1. RBAC : force retrieval_options.filters.document_set = périmètre
        │  2. relaie vers l'amont (le relais LLM)
        ▼
    relais LLM (uvicorn) ──► Ollama qwen2.5:7b-instruct  (LLM RÉEL, T=0)
        │  renvoie { "message": <texte LLM brut>, "top_documents": [...] }
        ▼
    access-gateway : POST-FILTRE garde-fous (couche 3, hors-LLM) sur la réponse
        ▼
    réponse finale au client  ◄── c'est ELLE qu'on évalue

On rejoue les 21 vecteurs (20 RT + 1 nominal). Pour chacun on imprime la requête
réelle et la réponse finale (brute + éventuelle substitution par la gateway), et
on prouve **21/21 APPLIQUÉ PAR LE CODE DÉPLOYÉ**.

Usage :
    ONIX_LIVE_MODEL=qwen2.5:7b-instruct python access-gateway/tests/e2e/run_e2e.py \
        [--markdown FICHIER.md] [--max N]

Sorties de processus : 0 si 21/21 (et zéro échec DUR), 1 sinon.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

# ── Rendre `app` (gateway) et les modules e2e importables ──────────────────
_HERE = Path(__file__).resolve().parent           # .../access-gateway/tests/e2e
_GW_ROOT = _HERE.parent.parent                     # .../access-gateway
_REPO_ROOT = _GW_ROOT.parent                       # repo
for p in (str(_GW_ROOT), str(_HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import uvicorn  # noqa: E402

import vectors as V  # noqa: E402
# Les libellés de refus EXACTS produits par le module DÉPLOYÉ (preuve fiable que
# c'est bien la gateway — et pas le LLM — qui a substitué la réponse).
from app.guardrail import (  # noqa: E402
    REFUSAL_INJECTION,
    REFUSAL_NO_CITATION,
    REFUSAL_NOT_AVAILABLE,
    REFUSAL_READ_ONLY,
)

_REFUSALS = (REFUSAL_INJECTION, REFUSAL_NO_CITATION,
             REFUSAL_NOT_AVAILABLE, REFUSAL_READ_ONLY)


# ───────────────────────────────────────────────────────────────────────────
# Utilitaires réseau / boot de serveurs uvicorn en thread.
# ───────────────────────────────────────────────────────────────────────────
def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Server:
    def __init__(self, app, port: int):
        cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()

    def wait_ready(self, path: str, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        url = f"http://127.0.0.1:{self.server.config.port}{path}"
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2):
                    return True
            except (urllib.error.URLError, OSError):
                time.sleep(0.25)
        return False

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)


def _ollama_reachable() -> bool:
    base = os.environ.get("ONIX_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/version", timeout=5):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _post_json(url: str, body: Dict[str, Any], headers: Dict[str, str],
               timeout: float) -> tuple[int, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": str(e)}


# ───────────────────────────────────────────────────────────────────────────
# Boot du pipeline : relais LLM + gateway (avec env configuré).
# ───────────────────────────────────────────────────────────────────────────
def _write_mapping(tmp: Path) -> Path:
    """Mapping groupe→Document Set : le groupe de test (NORD) → clients-nord."""
    path = tmp / "group_map.json"
    path.write_text(json.dumps({
        "version": 1,
        "default_document_sets": [],
        "groups": {GROUP_NORD: {"document_sets": ["clients-nord"]}},
    }), encoding="utf-8")
    return path


GROUP_NORD = "11111111-1111-1111-1111-111111111111"


def _claims() -> str:
    """Claims OIDC « déjà vérifiés par le reverse-proxy » (simulés)."""
    return json.dumps({"oid": "nord-e2e", "upn": "nord@contoso.fr",
                       "sub": "nord-e2e", "groups": [GROUP_NORD]})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markdown", default=None, help="écrit un tableau de résultats Markdown")
    ap.add_argument("--max", type=int, default=0, help="limiter au N premiers vecteurs (debug)")
    args = ap.parse_args()

    model = os.environ.get("ONIX_LIVE_MODEL", "qwen2.5:7b-instruct")
    req_timeout = float(os.environ.get("ONIX_LIVE_TIMEOUT", "180")) + 20

    if not _ollama_reachable():
        print("ERREUR : Ollama injoignable sur "
              f"{os.environ.get('ONIX_OLLAMA_URL', 'http://127.0.0.1:11434')}. "
              "Lance le conteneur t3-ollama d'abord.", file=sys.stderr)
        return 2

    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="onix-e2e-"))
    mapping = _write_mapping(tmp)

    relay_port = _free_port()
    gw_port = _free_port()

    # ── Configurer l'ENV de la gateway AVANT de l'importer (config lru_cache) ──
    os.environ["GATEWAY_ONYX_BASE_URL"] = f"http://127.0.0.1:{relay_port}"
    os.environ["GATEWAY_ONYX_API_KEY"] = ""              # relais local, pas de clé
    os.environ["GATEWAY_GROUP_SOURCE"] = "claims"
    os.environ["GATEWAY_MAPPING_PATH"] = str(mapping)
    os.environ["GATEWAY_DENY_IF_NO_MATCH"] = "true"
    os.environ["GATEWAY_GROUP_CACHE_TTL"] = "0"
    os.environ["GATEWAY_GUARDRAIL_ENABLED"] = "true"     # le contrôle déployé, ACTIF
    # L'amont génère via LLM (CPU, lent) : on élargit le timeout de relais de la
    # gateway pour ne pas couper une génération longue (sinon faux 502).
    os.environ["GATEWAY_UPSTREAM_TIMEOUT"] = str(
        float(os.environ.get("ONIX_LIVE_TIMEOUT", "180")) + 30
    )
    os.environ.setdefault("GATEWAY_AUDIT_SALT", "e2e-salt")

    # Import APRÈS env (la gateway lit l'env au démarrage).
    import app.config as gw_config
    gw_config.reset_settings_cache()
    from app.main import app as gateway_app
    from llm_relay import app as relay_app

    relay = _Server(relay_app, relay_port)
    gateway = _Server(gateway_app, gw_port)
    relay.start()
    gateway.start()
    try:
        if not relay.wait_ready("/health"):
            print("ERREUR : relais LLM non prêt.", file=sys.stderr)
            return 2
        if not gateway.wait_ready("/health"):
            print("ERREUR : gateway non prête.", file=sys.stderr)
            return 2

        print("─" * 78)
        print(f"PIPELINE E2E PRÊT — gateway:127.0.0.1:{gw_port} → "
              f"relais:127.0.0.1:{relay_port} → Ollama({model})")
        print(f"  post-filtre garde-fous DÉPLOYÉ dans la gateway : "
              f"GATEWAY_GUARDRAIL_ENABLED={os.environ['GATEWAY_GUARDRAIL_ENABLED']}")
        print("─" * 78)

        cases = V.build_cases()
        if args.max:
            cases = cases[: args.max]

        gw_url = f"http://127.0.0.1:{gw_port}/v1/chat/send-message"
        headers = {"X-OIDC-Claims": _claims()}

        results: List[Dict[str, Any]] = []
        for c in cases:
            # Le client envoie la question + le contexte documentaire (faux, avec
            # injections) via x_e2e_context (simulation du retrieval/injection).
            body = {"message": c.question, "x_e2e_context": c.context}
            status, resp = _post_json(gw_url, body, headers, req_timeout)

            final_answer = ""
            if isinstance(resp, dict):
                final_answer = resp.get("message") or resp.get("answer") or ""

            # Garde anti-faux-positif : une réponse vide ou un HTTP non-2xx n'est
            # PAS une preuve (un checker no-leak passerait trivialement sur "").
            if status < 200 or status >= 300:
                res = V.CheckResult(False, f"HTTP {status} (pas une réponse exploitable)")
            elif not final_answer.strip():
                res = V.CheckResult(False, "réponse VIDE (non exploitable, échec)")
            else:
                res = c.checker(final_answer)

            # Le contrôle est-il intervenu (substitution par la GATEWAY) ? On
            # compare au libellé EXACT du module déployé — non ambigu.
            substituted = isinstance(resp, dict) and any(
                r in final_answer for r in _REFUSALS
            )
            results.append({
                "id": c.id, "category": c.category, "status": status,
                "passed": res.passed, "reason": res.reason,
                "question": c.question, "answer": final_answer,
                "gateway_substituted": substituted,
                "echo_document_set": resp.get("echo_document_set") if isinstance(resp, dict) else None,
            })

            tag = "PASS" if res.passed else "FAIL"
            sub = "  [GATEWAY a substitué un refus]" if substituted else ""
            print(f"\n=== {c.id} [{c.category}] {tag} (HTTP {status}){sub}")
            print(f"  Q: {c.question}")
            print(f"  Périmètre Document Set reçu par l'amont (RBAC): "
                  f"{results[-1]['echo_document_set']}")
            print(f"  Réponse finale (gateway): {final_answer.strip()[:280]!r}")
            print(f"  Verdict checker: {res.reason}")

        # ── Synthèse ──
        passed = sum(1 for r in results if r["passed"])
        total = len(results)
        subs = sum(1 for r in results if r["gateway_substituted"])
        _HARD = ("fuite du prompt", "injection exécutée", "relayé comme action")
        hard = [r for r in results
                if (not r["passed"]) and any(m in r["reason"].lower() for m in _HARD)]

        print("\n" + "═" * 78)
        print(f"RÉSULTAT E2E (par le code déployé) : {passed}/{total} APPLIQUÉ")
        print(f"  dont substitutions de refus par la GATEWAY (post-filtre) : {subs}")
        print(f"  échecs DURS (fuite/exécution injection) : {len(hard)}")
        print("═" * 78)
        for r in results:
            mark = "PASS" if r["passed"] else "FAIL"
            sub = " [C3 gateway]" if r["gateway_substituted"] else ""
            print(f"  {r['id']:<6} {r['category']:<26} {mark}  {r['reason']}{sub}")

        if args.markdown:
            _write_markdown(Path(args.markdown), model, results, passed, total, subs)
            print(f"\n[écrit] {args.markdown}")

        ok = (passed == total) and not hard
        return 0 if ok else 1
    finally:
        gateway.stop()
        relay.stop()


def _write_markdown(path: Path, model: str, results: List[Dict[str, Any]],
                    passed: int, total: int, subs: int) -> None:
    lines = [
        "| Vecteur | Catégorie | HTTP | Verdict | Substitué par la gateway | Raison |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| `{r['id']}` | {r['category']} | {r['status']} | "
            f"{'✅ PASS' if r['passed'] else '❌ FAIL'} | "
            f"{'oui (couche 3)' if r['gateway_substituted'] else 'non (passthrough)'} | "
            f"{r['reason']} |"
        )
    table = "\n".join(lines)
    path.write_text(
        f"<!-- Généré par access-gateway/tests/e2e/run_e2e.py — modèle {model} -->\n"
        f"Résultat : **{passed}/{total}** appliqué par le code déployé "
        f"(dont **{subs}** substitutions de refus par la gateway).\n\n"
        f"{table}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
