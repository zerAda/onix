#!/usr/bin/env bash
# =============================================================================
# Pré-télécharge les modèles Ollama DANS le conteneur (volume ollama_data) ET
# applique les RÉGLAGES RAG optimaux (num_ctx + température) via un Modelfile, pour
# qu'ils soient prêts à l'emploi dans Onyx (zéro attente, contexte NON tronqué).
#   Usage : ./scripts/pull-models.sh [ "modele1 modele2" ]
#
# Pourquoi un Modelfile : le défaut Ollama `num_ctx=4096` tronque silencieusement
# le contexte RAG. On reconstruit chaque modèle de CHAT « sur lui-même »
# (FROM <modele>) en y GRAVANT num_ctx + temperature, SANS changer son nom (Onyx
# le voit inchangé). Best-effort : un échec n'interrompt pas le pré-tirage — le
# défaut serveur OLLAMA_CONTEXT_LENGTH (docker-compose / Helm) prend alors le relais.
#
# Les modèles d'EMBEDDING (nomic-embed-text, bge-m3…) sont laissés TELS QUELS.
# NB : Onyx n'embed PAS via Ollama (il a son propre model-server) — `nomic` n'est
# utile que pour des usages Ollama-natifs (juge d'éval RAGAS, outils tiers).
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

DC="docker compose"
$DC version >/dev/null 2>&1 || DC="docker-compose"

# Liste de modèles : argument > .env > défaut sûr CPU.
MODELS="${1:-}"
if [ -z "$MODELS" ] && [ -f .env ]; then
  MODELS="$(sed -n 's/^OLLAMA_MODELS_TO_PULL=//p' .env | head -n1)"
fi
MODELS="${MODELS:-llama3.2:3b nomic-embed-text}"

# Température gravée par défaut (Onyx peut la surcharger par requête). 0.2 =
# factuel/stable pour du RAG sourcé (cf. docs/RAG_OPTIMIZATION.md).
ONIX_TEMP="${ONIX_TEMP:-0.2}"

# num_ctx par défaut selon la taille du modèle (alignés scripts/detect-hardware.sh).
# Surchargeable globalement par ONIX_NUM_CTX.
ctx_for() {
  case "$1" in
    *:1b*|*:3b*)   echo 8192 ;;
    *:7b*|*:8b*)   echo 12288 ;;
    *:14b*|*:32b*) echo 16384 ;;
    *)             echo 8192 ;;
  esac
}

# Modèle d'embedding (pas de num_ctx/température applicables).
is_embedding() {
  case "$1" in *embed*|*bge-m3*|*e5*) return 0 ;; *) return 1 ;; esac
}

echo "→ Attente du conteneur Ollama (santé)…"
ready=0
for i in $(seq 1 60); do
  if $DC exec -T ollama ollama ls >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
if [ "$ready" != 1 ]; then
  echo "✗ Ollama indisponible. Lancez d'abord 'make up'."; exit 1
fi

for m in $MODELS; do
  echo "→ ollama pull $m"
  $DC exec -T ollama ollama pull "$m"

  if is_embedding "$m"; then
    echo "  · $m = modèle d'embedding → réglages RAG non applicables (laissé tel quel)."
    continue
  fi

  ctx="${ONIX_NUM_CTX:-$(ctx_for "$m")}"
  echo "  · réglages RAG gravés dans $m : num_ctx=$ctx, temperature=$ONIX_TEMP"
  # On écrit un Modelfile dans le conteneur puis on reconstruit le modèle SUR
  # LUI-MÊME (même nom). Best-effort : en cas d'échec, on prévient et on continue.
  if ! $DC exec -T ollama sh -c \
        "printf 'FROM %s\nPARAMETER num_ctx %s\nPARAMETER temperature %s\n' '$m' '$ctx' '$ONIX_TEMP' > /tmp/onix.Modelfile && ollama create '$m' -f /tmp/onix.Modelfile" \
        >/dev/null 2>&1; then
    echo "  ⚠ réglages non gravés dans $m (non bloquant : OLLAMA_CONTEXT_LENGTH serveur s'applique)."
  fi
done

echo
echo "✓ Modèles présents :"
$DC exec -T ollama ollama ls
