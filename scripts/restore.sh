#!/usr/bin/env bash
# =============================================================================
# Restaure les volumes depuis un dossier de sauvegarde créé par backup.sh.
# ÉCRASE les données actuelles. Stack arrêtée pendant l'opération.
#   Usage : ./scripts/restore.sh backups/AAAAMMJJ-HHMMSS
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="${1:-}"
[ -n "$SRC" ] && [ -d "$SRC" ] || { echo "Usage: $0 <dossier-de-sauvegarde>"; exit 1; }
SRC="$(cd "$SRC" && pwd)"

DC="docker compose"; $DC version >/dev/null 2>&1 || DC="docker-compose"
PROJ="onix"
VOLS="db_volume opensearch-data minio_data file-system"

printf '⚠ Cette opération ÉCRASE les données actuelles depuis %s. Continuer ? [oui/non] ' "$SRC"
read -r ans; [ "$ans" = "oui" ] || { echo "Annulé."; exit 0; }

echo "→ Arrêt de la stack…"
$DC down

for v in $VOLS; do
  [ -f "$SRC/${v}.tgz" ] || { echo "  (ignoré : $v.tgz absent)"; continue; }
  echo "→ Restauration $v"
  docker volume create "${PROJ}_${v}" >/dev/null
  docker run --rm -v "${PROJ}_${v}:/v" -v "$SRC:/b:ro" alpine \
    sh -c "cd /v && rm -rf ./* ./.[!.]* 2>/dev/null; tar xzf /b/${v}.tgz"
done

echo "→ Redémarrage…"
$DC up -d
echo "✓ Restauration terminée depuis $SRC"
