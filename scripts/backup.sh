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

# Surcouche compose à empiler pour l'arrêt/redémarrage COHÉRENT. Sans elle, un
# déploiement PROD exposé (Caddy + oauth2-proxy + access-gateway, définis par
# deploy/prod/docker-compose.prod.yml) ne serait PAS arrêté par `docker compose
# stop` nu : on couperait Onyx mais pas le bord → backup incohérent / fuite de
# trafic. On détecte le profil et on construit le MÊME jeu de fichiers que le
# Makefile (COMPOSE_PROD / COMPOSE_LOCAL_PROD). Le projet reste `onix` (volumes
# onix_*) quel que soit le profil. Surchargeable explicitement par l'opérateur :
#   PROFILE=prod ENV=deploy/prod/.env.prod ./scripts/backup.sh
#   PROFILE=local-prod ./scripts/backup.sh
PROFILE="${PROFILE:-}"
ENV="${ENV:-}"
# Auto-détection : un ENV sous deploy/prod/ implique le profil prod exposé.
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
  ""|base) : ;;  # base mono-poste : docker-compose.yml seul
  *) echo "PROFILE inconnu: $PROFILE (attendu: base|prod|local-prod)" >&2; exit 1 ;;
esac

echo "→ Arrêt de la stack (cohérence)…"
$DC "${DC_ARGS[@]}" stop

for v in $VOLS; do
  echo "→ Archivage $v"
  docker run --rm -v "${PROJ}_${v}:/v:ro" -v "$DEST:/b" alpine \
    sh -c "cd /v && tar czf /b/${v}.tgz ."
done

echo "→ Redémarrage…"
$DC "${DC_ARGS[@]}" start
echo "✓ Sauvegarde : $DEST"
ls -lh "$DEST"
