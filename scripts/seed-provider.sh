#!/usr/bin/env bash
# =============================================================================
# SEED idempotent du provider LLM Ollama dans Onyx (corrige le bug #9, prouvé au
# runtime Azure — cf. .planning/RUNTIME-EVIDENCE.md).
# -----------------------------------------------------------------------------
# PROBLÈME : après `make up-local-prod` + `make models`, la table Onyx
# `llm_provider` est VIDE. `make models` tire bien le modèle DANS Ollama, mais ne
# crée AUCUNE ligne provider côté Onyx → le chat échoue instantanément
# (« No default LLM model found »). Un déploiement neuf a donc le chat MORT tant
# qu'un admin ne configure pas le provider à la main dans l'UI (Admin → LLM).
#
# CE SCRIPT : enregistre le provider Ollama dans Onyx via l'API admin, le définit
# par défaut, et grave le modèle tiré comme default + fast. IDEMPOTENT : si un
# provider du même nom existe déjà, il ne recrée rien (met seulement à jour si
# demandé). FAIL-CLOSED : toute condition manquante (API injoignable, identifiants
# admin absents, modèle introuvable) → message BRUYANT + sortie non nulle ; jamais
# d'acceptation silencieuse.
#
# PRÉ-REQUIS : la pile doit être SAINE (api_server healthy) et un compte ADMIN
# doit exister (1er compte créé = admin). Fournir ses identifiants par env :
#     ONIX_ADMIN_EMAIL=...  ONIX_ADMIN_PASSWORD=...
# (jamais en repo — secrets via env, cf. AGENTS.md). En OIDC d'entreprise
# (deploy/prod), créez plutôt une CLÉ API admin Onyx et passez ONIX_ADMIN_API_KEY.
#
#   Usage :  ./scripts/seed-provider.sh
#            ONIX_SEED_FORCE=1 ./scripts/seed-provider.sh   # met à jour si présent
# =============================================================================
set -uo pipefail
cd "$(dirname "$0")/.." || { echo "✗ racine du dépôt inaccessible." >&2; exit 1; }

DC="docker compose"; $DC version >/dev/null 2>&1 || DC="docker-compose"

# --- Paramètres (tous surchargeables par env ; défauts internes sûrs) --------
PROVIDER_NAME="${ONIX_PROVIDER_NAME:-ollama}"
# URL interne d'Ollama vue depuis api_server (JAMAIS localhost — cf. POC_LOCAL §7).
OLLAMA_BASE="${ONIX_OLLAMA_URL:-http://ollama:11434}"
# api_server interne : on appelle l'API DEPUIS le conteneur api_server lui-même
# (réseau onix-net, pas de port hôte requis). Endpoint admin sur 8080.
API_HOST="${ONIX_API_HOST:-http://127.0.0.1:8080}"
FORCE="${ONIX_SEED_FORCE:-0}"

# --- Modèle de chat à enregistrer : argument > .env > échec fail-closed -------
# On déduit le modèle de CHAT (pas l'embedding) de OLLAMA_MODELS_TO_PULL (.env),
# aligné sur ce que `make tune`/`make models` ont réellement tiré.
MODEL="${1:-${ONIX_SEED_MODEL:-}}"
if [ -z "$MODEL" ] && [ -f .env ]; then
  list="$(sed -n 's/^OLLAMA_MODELS_TO_PULL=//p' .env | head -n1)"
  for m in $list; do
    case "$m" in *embed*|*bge-m3*|*e5*) : ;; *) MODEL="$m"; break ;; esac
  done
fi
if [ -z "$MODEL" ]; then
  echo "✗ FAIL-CLOSED : aucun modèle de chat déterminé." >&2
  echo "  Renseignez OLLAMA_MODELS_TO_PULL dans .env (make tune) ou passez le modèle :" >&2
  echo "    ./scripts/seed-provider.sh qwen2.5:7b-instruct" >&2
  exit 1
fi

# --- Authentification admin : clé API > login email/mdp ----------------------
# Construit l'en-tête d'auth qui sera utilisé pour TOUS les appels admin.
AUTH_HEADER=""
COOKIE_FILE=""
api_ready() { $DC exec -T api_server sh -c "command -v curl >/dev/null 2>&1"; }

# Wrapper : exécute curl DANS api_server (réseau interne). Renvoie le corps ;
# le code HTTP est écrit sur la dernière ligne via -w.
icurl() { # icurl METHOD PATH [JSON_BODY]
  local method="$1" path="$2" body="${3:-}"
  local hdr=""
  [ -n "$AUTH_HEADER" ] && hdr="-H \"$AUTH_HEADER\""
  local cookie=""
  [ -n "$COOKIE_FILE" ] && cookie="-b $COOKIE_FILE -c $COOKIE_FILE"
  if [ -n "$body" ]; then
    $DC exec -T api_server sh -c \
      "curl -sS -o /tmp/seed_body -w '%{http_code}' $cookie $hdr -X $method \
       -H 'Content-Type: application/json' -d '$body' '$API_HOST$path'; echo; cat /tmp/seed_body" \
      2>/dev/null
  else
    $DC exec -T api_server sh -c \
      "curl -sS -o /tmp/seed_body -w '%{http_code}' $cookie $hdr -X $method '$API_HOST$path'; echo; cat /tmp/seed_body" \
      2>/dev/null
  fi
}

echo "→ Vérification de la disponibilité de l'API Onyx (api_server)…"
ready=0
for _ in $(seq 1 30); do
  hc="$($DC exec -T api_server sh -c "curl -sS -o /dev/null -w '%{http_code}' $API_HOST/health" 2>/dev/null)"
  [ "$hc" = "200" ] && { ready=1; break; }
  sleep 2
done
if [ "$ready" != 1 ]; then
  echo "✗ FAIL-CLOSED : api_server /health != 200 (pile pas saine). Lancez 'make up-local-prod' et attendez la convergence." >&2
  exit 1
fi
api_ready || { echo "✗ FAIL-CLOSED : curl absent dans api_server — impossible de piloter l'API." >&2; exit 1; }

# --- Établir l'authentification admin ----------------------------------------
if [ -n "${ONIX_ADMIN_API_KEY:-}" ]; then
  AUTH_HEADER="Authorization: Bearer ${ONIX_ADMIN_API_KEY}"
  echo "→ Auth admin : clé API (ONIX_ADMIN_API_KEY)."
elif [ -n "${ONIX_ADMIN_EMAIL:-}" ] && [ -n "${ONIX_ADMIN_PASSWORD:-}" ]; then
  echo "→ Auth admin : login ${ONIX_ADMIN_EMAIL} (session cookie)."
  COOKIE_FILE="/tmp/seed_cookie"
  # Onyx (fastapi-users) : login en form-urlencoded username/password sur /auth/login.
  login_code="$($DC exec -T api_server sh -c \
    "curl -sS -o /dev/null -w '%{http_code}' -c $COOKIE_FILE \
     -H 'Content-Type: application/x-www-form-urlencoded' \
     --data-urlencode 'username=${ONIX_ADMIN_EMAIL}' \
     --data-urlencode 'password=${ONIX_ADMIN_PASSWORD}' \
     $API_HOST/auth/login" 2>/dev/null)"
  case "$login_code" in
    200|204) : ;;
    *) echo "✗ FAIL-CLOSED : login admin a échoué (HTTP $login_code). Vérifiez ONIX_ADMIN_EMAIL/PASSWORD (1er compte créé = admin)." >&2; exit 1 ;;
  esac
else
  echo "✗ FAIL-CLOSED : aucun moyen d'authentification admin fourni." >&2
  echo "  Renseignez ONIX_ADMIN_EMAIL + ONIX_ADMIN_PASSWORD (déploiement basic)," >&2
  echo "  ou ONIX_ADMIN_API_KEY (déploiement OIDC : Admin → API Keys)." >&2
  exit 1
fi

# --- IDEMPOTENCE : le provider existe-t-il déjà ? ----------------------------
echo "→ Inventaire des providers LLM existants…"
resp="$(icurl GET /admin/llm/provider)"
http="$(printf '%s\n' "$resp" | sed -n '1p')"
json="$(printf '%s\n' "$resp" | sed -n '2,$p')"
case "$http" in
  200) : ;;
  401|403) echo "✗ FAIL-CLOSED : accès admin refusé (HTTP $http). Le compte n'est pas admin ou la clé est invalide." >&2; exit 1 ;;
  *) echo "✗ FAIL-CLOSED : GET /admin/llm/provider a renvoyé HTTP $http." >&2; exit 1 ;;
esac

# Détection de présence SANS dépendance jq (grep sur le nom exact entre guillemets).
if printf '%s' "$json" | grep -q "\"name\"[[:space:]]*:[[:space:]]*\"$PROVIDER_NAME\""; then
  if [ "$FORCE" != "1" ]; then
    echo "✓ Provider '$PROVIDER_NAME' DÉJÀ présent → rien à faire (idempotent)."
    echo "  Pour forcer une mise à jour : ONIX_SEED_FORCE=1 $0"
    exit 0
  fi
  echo "→ Provider '$PROVIDER_NAME' présent mais ONIX_SEED_FORCE=1 → mise à jour."
fi

# --- Création / mise à jour du provider Ollama -------------------------------
# Corps minimal pour un provider custom Ollama dans Onyx : provider 'ollama',
# api_base = URL interne, default + fast = le modèle tiré. model_configurations
# déclare le modèle visible. (Contrat aligné sur l'API admin Onyx 4.x.)
BODY="$(cat <<JSON
{"name":"$PROVIDER_NAME","provider":"ollama","api_base":"$OLLAMA_BASE","default_model_name":"$MODEL","fast_default_model_name":"$MODEL","model_configurations":[{"name":"$MODEL","is_visible":true}],"is_default_provider":true}
JSON
)"
echo "→ Enregistrement du provider '$PROVIDER_NAME' (api_base=$OLLAMA_BASE, modèle=$MODEL)…"
resp="$(icurl PUT '/admin/llm/provider?is_creation=true' "$BODY")"
http="$(printf '%s\n' "$resp" | sed -n '1p')"
json="$(printf '%s\n' "$resp" | sed -n '2,$p')"
case "$http" in
  200|201) echo "✓ Provider enregistré (HTTP $http)." ;;
  409) echo "✓ Provider déjà existant côté API (HTTP 409) — considéré OK (idempotent)." ;;
  *) echo "✗ FAIL-CLOSED : PUT /admin/llm/provider a renvoyé HTTP $http :" >&2
     printf '   %s\n' "$json" >&2; exit 1 ;;
esac

# --- Le définir PAR DÉFAUT (si l'API expose un point dédié) -------------------
# Récupère l'id du provider créé pour le marquer par défaut (selon version d'API).
prov_id="$(printf '%s' "$json" | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*\([0-9]\+\).*/\1/p' | head -n1)"
if [ -n "$prov_id" ]; then
  dcode="$(icurl POST "/admin/llm/provider/$prov_id/default" | sed -n '1p')"
  case "$dcode" in
    200|204) echo "✓ Provider '$PROVIDER_NAME' marqué par défaut (HTTP $dcode)." ;;
    404|405) echo "  (point /default absent sur cette version — is_default_provider du corps fait foi)." ;;
    *) echo "  ⚠ marquage par défaut : HTTP $dcode (le corps is_default_provider=true reste appliqué)." ;;
  esac
fi

echo "✓ SEED terminé : le chat dispose d'un LLM par défaut (plus de « No default LLM model found »)."
