#!/usr/bin/env bash
# =============================================================================
# Vérification de bout en bout : pré-requis, services, câblage Onyx↔Ollama,
# et un test réel de génération en français. Sortie non nulle si un point dur
# échoue → utilisable en CI / contrôle d'acceptation ("ne rien laisser dire").
# =============================================================================
set -uo pipefail
cd "$(dirname "$0")/.."

DC="docker compose"; $DC version >/dev/null 2>&1 || DC="docker-compose"
PASS=0; FAIL=0; WARN=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
ko()   { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; WARN=$((WARN+1)); }

echo "== Pré-requis =="
docker info >/dev/null 2>&1 && ok "Docker opérationnel" || ko "Docker injoignable"
[ -f .env ] && ok ".env présent" || ko ".env manquant (make secrets)"
if [ -f .env ]; then
  for k in SECRET POSTGRES_PASSWORD OPENSEARCH_ADMIN_PASSWORD MINIO_ROOT_PASSWORD S3_AWS_SECRET_ACCESS_KEY; do
    v="$(sed -n "s/^$k=//p" .env | head -n1)"
    [ -n "$v" ] && ok "secret $k défini" || ko "secret $k vide (make secrets)"
  done
fi
if [ "$(uname -s)" = "Linux" ]; then
  mmc="$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)"
  [ "${mmc:-0}" -ge 262144 ] && ok "vm.max_map_count=$mmc" || warn "vm.max_map_count=$mmc (<262144) : OpenSearch peut échouer"
fi

echo "== Services =="
for s in api_server background web_server relational_db opensearch cache minio ollama nginx; do
  state="$($DC ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$s" '$1==s{print $2}')"
  case "$state" in
    running) ok "$s : running" ;;
    "")      ko "$s : absent" ;;
    *)       warn "$s : $state" ;;
  esac
done

echo "== Câblage Onyx ↔ Ollama (réseau interne) =="
if $DC exec -T ollama ollama ls >/dev/null 2>&1; then
  ok "API Ollama répond (http://ollama:11434, interne)"
  models="$($DC exec -T ollama ollama ls 2>/dev/null | awk 'NR>1{print $1}' | tr '\n' ' ')"
  [ -n "$models" ] && ok "modèles présents : $models" || warn "aucun modèle (make models)"
  # Onyx atteint-il Ollama par le DNS de service ? Test depuis api_server.
  if $DC exec -T api_server python -c "import socket; socket.create_connection(('ollama',11434),5)" >/dev/null 2>&1; then
    ok "api_server peut joindre ollama:11434"
  else
    warn "api_server n'a pas joint ollama:11434 (services pas encore prêts ?)"
  fi
else
  ko "API Ollama injoignable"
fi

echo "== Test de génération (LLM local, français) =="
first_model="$($DC exec -T ollama ollama ls 2>/dev/null | awk 'NR==2{print $1}')"
if [ -n "$first_model" ]; then
  out="$($DC exec -T ollama ollama run "$first_model" 'Réponds en un mot : capitale de la France ?' 2>/dev/null | tr -d '\r')"
  if echo "$out" | grep -qi 'paris'; then ok "génération OK ($first_model) → ${out:0:60}"
  else warn "génération sans 'Paris' (modèle: $first_model) → ${out:0:80}"; fi
else
  warn "pas de modèle pour tester la génération (make models)"
fi

echo "== Frontend (localhost) =="
PORT="$(sed -n 's/^ONYX_HOST_PORT=//p' .env 2>/dev/null | head -n1)"; PORT="${PORT:-3000}"
code=""
if command -v curl >/dev/null 2>&1; then code="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/" 2>/dev/null)"; fi
case "$code" in
  200|307|302|401) ok "UI joignable http://localhost:$PORT (HTTP $code)" ;;
  "") warn "curl absent — testez http://localhost:$PORT dans le navigateur" ;;
  *)  warn "UI a répondu HTTP $code (démarrage en cours ?)" ;;
esac

echo
printf 'Résultat : \033[32m%d OK\033[0m, \033[33m%d avertissements\033[0m, \033[31m%d échecs\033[0m\n' "$PASS" "$WARN" "$FAIL"
[ "$FAIL" -eq 0 ] && { echo "✓ Stack saine. Ouvrez http://localhost:$PORT et créez le 1er compte (= admin)."; exit 0; }
echo "✗ Des points durs ont échoué — voir docs/RUNBOOK.md § Dépannage."; exit 1
