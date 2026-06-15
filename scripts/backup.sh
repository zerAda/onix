#!/usr/bin/env bash
# =============================================================================
# Sauvegarde cohérente des données persistantes (Postgres, OpenSearch, MinIO,
# fichiers Onyx). Arrête brièvement la stack pour garantir la cohérence, archive
# les volumes, puis redémarre. Les modèles Ollama ne sont PAS sauvegardés
# (re-téléchargeables via `make models`).
#   Usage : ./scripts/backup.sh          → backups/AAAAMMJJ-HHMMSS/
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

DC="docker compose"; $DC version >/dev/null 2>&1 || DC="docker-compose"
PROJ="onix"
TS="$(date +%Y%m%d-%H%M%S)"
DEST="$(pwd)/backups/$TS"
mkdir -p "$DEST"

VOLS="db_volume opensearch-data minio_data file-system"

echo "→ Arrêt de la stack (cohérence)…"
$DC stop

for v in $VOLS; do
  echo "→ Archivage $v"
  docker run --rm -v "${PROJ}_${v}:/v:ro" -v "$DEST:/b" alpine \
    sh -c "cd /v && tar czf /b/${v}.tgz ."
done

echo "→ Redémarrage…"
$DC start
echo "✓ Sauvegarde : $DEST"
ls -lh "$DEST"
