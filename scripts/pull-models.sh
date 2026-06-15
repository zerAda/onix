#!/usr/bin/env bash
# =============================================================================
# Pré-télécharge les modèles Ollama DANS le conteneur (volume ollama_data),
# pour qu'ils soient disponibles immédiatement dans Onyx (zéro attente au 1er chat).
# Liste lue depuis OLLAMA_MODELS_TO_PULL (.env) ou passée en argument.
#   Usage : ./scripts/pull-models.sh [ "modele1 modele2" ]
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
done

echo
echo "✓ Modèles présents :"
$DC exec -T ollama ollama ls
