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

# Surcouche compose à empiler (cf. backup.sh) : un déploiement PROD exposé doit
# arrêter/redémarrer AUSSI Caddy/oauth2-proxy/gateway, sinon le bord reste en
# place pendant l'écrasement des volumes. Profil surchargeable :
#   PROFILE=prod ENV=deploy/prod/.env.prod ./scripts/restore.sh <dir>
#   PROFILE=local-prod ./scripts/restore.sh <dir>
PROFILE="${PROFILE:-}"
ENV="${ENV:-}"
case "$ENV" in deploy/prod/*|*/deploy/prod/*) PROFILE="${PROFILE:-prod}" ;; esac
DC_ARGS=(-p "$PROJ" -f docker-compose.yml)
case "$PROFILE" in
  prod)
    [ -n "$ENV" ] && DC_ARGS=(--env-file "$ENV" "${DC_ARGS[@]}")
    DC_ARGS+=(-f deploy/prod/docker-compose.prod.yml)
    echo "→ Profil PROD exposé (Caddy/oauth2-proxy/gateway inclus)"
    ;;
  local-prod)
    DC_ARGS+=(-f docker-compose.prod-local.yml)
    echo "→ Profil PROD machine unique (overlay prod-local inclus)"
    ;;
  ""|base) : ;;
  *) echo "PROFILE inconnu: $PROFILE (attendu: base|prod|local-prod)" >&2; exit 1 ;;
esac

# --- Déchiffrement FAIL-CLOSED (BKP-02) --------------------------------------
# Les archives chiffrées (`*.tgz.enc`) exigent la passphrase `ONIX_BACKUP_PASSPHRASE`.
# Sans elle → refus (jamais d'échec silencieux). Compat ascendante : on accepte
# aussi les anciennes archives en clair (`*.tgz`).
if ls "$SRC"/*.tgz.enc >/dev/null 2>&1 && [ -z "${ONIX_BACKUP_PASSPHRASE:-}" ]; then
  echo "✗ FAIL-CLOSED : archives chiffrées (.enc) mais ONIX_BACKUP_PASSPHRASE absente — refus." >&2
  exit 1
fi

printf '⚠ Cette opération ÉCRASE les données actuelles depuis %s. Continuer ? [oui/non] ' "$SRC"
read -r ans; [ "$ans" = "oui" ] || { echo "Annulé."; exit 0; }

echo "→ Arrêt de la stack…"
$DC "${DC_ARGS[@]}" down

for v in $VOLS; do
  if [ -f "$SRC/${v}.tgz.enc" ]; then
    echo "→ Restauration $v (déchiffrement)"
    docker volume create "${PROJ}_${v}" >/dev/null
    # Déchiffre et PIPE le flux dans tar : aucun clair écrit sur disque.
    openssl enc -d -aes-256-cbc -pbkdf2 -pass env:ONIX_BACKUP_PASSPHRASE -in "$SRC/${v}.tgz.enc" \
      | docker run --rm -i -v "${PROJ}_${v}:/v" alpine \
          sh -c "cd /v && rm -rf ./* ./.[!.]* 2>/dev/null; tar xzf -"
  elif [ -f "$SRC/${v}.tgz" ]; then
    echo "→ Restauration $v (clair, archive legacy)"
    docker volume create "${PROJ}_${v}" >/dev/null
    docker run --rm -v "${PROJ}_${v}:/v" -v "$SRC:/b:ro" alpine \
      sh -c "cd /v && rm -rf ./* ./.[!.]* 2>/dev/null; tar xzf /b/${v}.tgz"
  else
    echo "  (ignoré : $v absent)"; continue
  fi
done

echo "→ Redémarrage…"
$DC "${DC_ARGS[@]}" up -d
echo "✓ Restauration terminée depuis $SRC"
