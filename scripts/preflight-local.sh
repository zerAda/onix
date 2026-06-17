#!/usr/bin/env bash
# =============================================================================
# Pré-vol LOCAL — à lancer AVANT `make up`.
# -----------------------------------------------------------------------------
# But : détecter, AVANT tout démarrage, les conditions qui feraient échouer le
# PREMIER `make up` (daemon Docker absent, vm.max_map_count trop bas → OpenSearch
# qui ne monte pas, RAM/disque insuffisants, port déjà pris, secrets manquants).
# Chaque vérification donne un message d'ACTION clair ; on sort en code ≠0 si un
# prérequis BLOQUANT manque, mais on affiche TOUJOURS le récap d'abord.
#
# Pourquoi un script dédié (vs scripts/verify.sh) ? verify.sh contrôle la stack
# APRÈS démarrage (services up, câblage, génération). Ici on est EN AMONT : aucun
# conteneur ne tourne, on valide juste le terrain. Best-effort et tolérant : si un
# outil de diagnostic manque (ss/lsof/free…), on n'échoue pas — on le signale.
#
# `set -u` (variables non définies = erreur) mais PAS `set -e` : un check qui
# renvoie non-zéro ne doit jamais court-circuiter le récap final.
# =============================================================================
set -u
# On se place à la racine du dépôt ; si ce cd échoue, mieux vaut sortir net que
# de lire un .env/compose au mauvais endroit (le préflight doit rester fiable).
cd "$(dirname "$0")/.." || { echo "✗ Impossible d'accéder à la racine du dépôt." >&2; exit 1; }

OS="$(uname -s)"
PASS=0; FAIL=0; WARN=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; PASS=$((PASS+1)); }
ko()   { printf '  \033[31m✗\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; WARN=$((WARN+1)); }
note() { printf '    \033[2m%s\033[0m\n' "$1"; }   # ligne d'aide (action), discrète

# Liste des actions à corriger (BLOQUANTES) : remplie au fil de l'eau, rejouée
# en conclusion pour que l'utilisateur ait la marche à suivre d'un coup d'œil.
ACTIONS=""
add_action() { ACTIONS="$ACTIONS
  - $1"; }

echo "== Pré-vol local (avant make up) =="
printf '   OS=%s\n' "$OS"

# ---------------------------------------------------------------------------
# 1. Docker : binaire présent + daemon JOIGNABLE + Compose v2.
#    `docker info` échoue si le daemon n'est pas lancé (cas n°1 du 1er run).
# ---------------------------------------------------------------------------
echo "== Docker =="
if command -v docker >/dev/null 2>&1; then
  ok "binaire docker présent"
  if docker info >/dev/null 2>&1; then
    ok "daemon Docker joignable"
  else
    ko "daemon Docker INJOIGNABLE (docker info a échoué)"
    note "Démarrez Docker (Linux: sudo systemctl start docker ; Desktop: lancez l'app)."
    add_action "Démarrer le daemon Docker, puis relancer ce pré-vol."
  fi
  # Compose v2 = sous-commande `docker compose` (et non l'ancien binaire v1).
  if docker compose version >/dev/null 2>&1; then
    ok "docker compose v2 présent ($(docker compose version 2>/dev/null | head -n1))"
  else
    ko "docker compose v2 absent (sous-commande 'docker compose')"
    note "Installez le plugin Compose v2 (Docker Desktop l'inclut ; sinon docker-compose-plugin)."
    add_action "Installer Docker Compose v2 (docker compose version)."
  fi
else
  ko "docker introuvable dans le PATH"
  note "Installez Docker Engine (Linux) ou Docker Desktop (macOS/Windows+WSL2)."
  add_action "Installer Docker, puis relancer ce pré-vol."
fi

# ---------------------------------------------------------------------------
# 2. vm.max_map_count (Linux) : OpenSearch refuse de démarrer sous 262144.
#    Sur non-Linux (Docker Desktop), le réglage vit dans la VM → on n'y touche
#    pas depuis l'hôte : skip avec note.
# ---------------------------------------------------------------------------
echo "== Noyau (OpenSearch) =="
if [ "$OS" = "Linux" ]; then
  mmc="$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)"
  if [ "${mmc:-0}" -ge 262144 ]; then
    ok "vm.max_map_count=$mmc (≥262144)"
  else
    ko "vm.max_map_count=$mmc (<262144) → OpenSearch ne démarrera pas"
    note "Immédiat : sudo sysctl -w vm.max_map_count=262144"
    note "Persistant : echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-onyx.conf && sudo sysctl --system"
    add_action "Régler vm.max_map_count à 262144 (voir messages ci-dessus)."
  fi
else
  warn "vm.max_map_count non vérifiable sur $OS (réglage dans la VM Docker Desktop)"
  note "Docker Desktop applique en général une valeur suffisante ; en cas d'échec d'OpenSearch, voir docs/POC_LOCAL.md §7."
fi

# ---------------------------------------------------------------------------
# 3. RAM physique : avertissement sous 16 Go (minimum recommandé). On informe
#    que 64 Go laissent de la marge pour le modèle 14b. Détection best-effort
#    selon l'OS ; si on ne sait pas lire la RAM, on ne plante pas.
# ---------------------------------------------------------------------------
echo "== Ressources =="
RAM_GB=0
case "$OS" in
  Linux)
    kb="$(sed -n 's/^MemTotal:[[:space:]]*\([0-9]*\).*/\1/p' /proc/meminfo 2>/dev/null)"
    # On arrondit au Go le plus proche (le noyau réserve une part de MemTotal).
    [ -n "$kb" ] && RAM_GB=$(( (kb + 512 * 1024) / 1024 / 1024 )) ;;
  Darwin)
    bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
    [ "${bytes:-0}" -gt 0 ] && RAM_GB=$(( (bytes + 512 * 1024 * 1024) / 1024 / 1024 / 1024 )) ;;
esac
if [ "$RAM_GB" -lt 1 ]; then
  warn "RAM indéterminée sur $OS — vérifiez ≥16 Go manuellement"
elif [ "$RAM_GB" -lt 16 ]; then
  warn "RAM=$RAM_GB Go (<16 Go recommandés) : profil dégradé conseillé (make tune choisit un petit modèle)"
  note "make tune adapte le modèle/limites à la RAM ; sous 16 Go privilégiez llama3.2:3b."
else
  ok "RAM=$RAM_GB Go (≥16 Go)"
  [ "$RAM_GB" -ge 48 ] && note "≥48 Go : large marge — le modèle 14b (qwen2.5:14b-instruct) est confortable."
fi

# ---------------------------------------------------------------------------
# 4. Espace disque libre là où vivent les VOLUMES Docker (images + modèle +
#    index ≈ 25-30 Go). On interroge le répertoire data-root de Docker s'il est
#    connu, sinon la racine. Best-effort (df peut manquer sur un OS exotique).
# ---------------------------------------------------------------------------
DOCKER_ROOT=""
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null)"
fi
DISK_PATH="${DOCKER_ROOT:-/}"
[ -d "$DISK_PATH" ] || DISK_PATH="/"
if command -v df >/dev/null 2>&1; then
  # -P : format POSIX stable (1 ligne de données, colonnes fixes). On lit les Ko
  # disponibles (4e colonne) → Go. -k pour forcer l'unité (portable Linux/macOS).
  avail_kb="$(df -Pk "$DISK_PATH" 2>/dev/null | awk 'NR==2{print $4}')"
  if [ -n "${avail_kb:-}" ] && [ "$avail_kb" -gt 0 ] 2>/dev/null; then
    avail_gb=$(( avail_kb / 1024 / 1024 ))
    if [ "$avail_gb" -ge 30 ]; then
      ok "disque libre ${avail_gb} Go sur $DISK_PATH (≥30 Go)"
    elif [ "$avail_gb" -ge 25 ]; then
      warn "disque libre ${avail_gb} Go sur $DISK_PATH (25-30 Go : juste pour images+modèle+index)"
    else
      ko "disque libre ${avail_gb} Go sur $DISK_PATH (<25 Go) : risque d'échec de pull/index"
      note "Libérez de l'espace (docker system prune) ou déplacez le data-root Docker."
      add_action "Libérer du disque : viser ≥25-30 Go sur $DISK_PATH."
    fi
  else
    warn "espace disque illisible sur $DISK_PATH — vérifiez ≥25-30 Go manuellement"
  fi
else
  warn "df absent — vérifiez ≥25-30 Go de libre manuellement"
fi

# ---------------------------------------------------------------------------
# 5. Ports hôte : nginx publie ONYX_HOST_PORT (déf. 3000) sur 127.0.0.1 ; le
#    monitoring publie GRAFANA_HOST_PORT (déf. 3001). Un port déjà pris ferait
#    échouer `docker compose up` (bind). Détection best-effort : ss → lsof → nc.
# ---------------------------------------------------------------------------
echo "== Ports hôte =="
get_env() { sed -n "s/^$1=//p" .env 2>/dev/null | head -n1; }
ONYX_PORT="$(get_env ONYX_HOST_PORT)"; ONYX_PORT="${ONYX_PORT:-3000}"
GRAFANA_PORT="$(get_env GRAFANA_HOST_PORT)"; GRAFANA_PORT="${GRAFANA_PORT:-3001}"

# Renvoie 0 si le port semble LIBRE, 1 si OCCUPÉ, 2 si on n'a pas su tester.
port_busy() {
  p="$1"
  if command -v ss >/dev/null 2>&1; then
    # -H sans en-tête, -l écoute, -t/-u tcp/udp, -n numérique. grep sur :PORT final.
    ss -Hlntu 2>/dev/null | awk '{print $5}' | grep -qE "[:.]$p\$" && return 1 || return 0
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1 && return 1 || return 0
  elif command -v nc >/dev/null 2>&1; then
    # nc -z : un succès de connexion = quelqu'un écoute → port occupé.
    nc -z 127.0.0.1 "$p" >/dev/null 2>&1 && return 1 || return 0
  else
    return 2
  fi
}

check_port() { # check_port PORT LIBELLÉ DURETÉ(dur|mou)
  p="$1"; label="$2"; hard="$3"
  port_busy "$p"; rc=$?
  case "$rc" in
    0) ok "port $p libre ($label)" ;;
    1) if [ "$hard" = "dur" ]; then
         ko "port $p OCCUPÉ ($label) : le bind nginx échouera"
         note "Changez ONYX_HOST_PORT dans .env, ou libérez le port $p."
         add_action "Libérer le port $p ou changer ONYX_HOST_PORT dans .env."
       else
         warn "port $p occupé ($label) — utile seulement si vous lancez make monitor-up"
       fi ;;
    *) warn "port $p non testable ($label) — ni ss, ni lsof, ni nc dispo" ;;
  esac
}
check_port "$ONYX_PORT" "UI Onyx / nginx" dur
check_port "$GRAFANA_PORT" "Grafana / monitoring" mou

# ---------------------------------------------------------------------------
# 6. .env + secrets requis NON VIDES. On ne lit JAMAIS la valeur affichée : on
#    teste seulement « présent et non vide ». Si .env manque → `make secrets`.
#    Liste alignée sur docker-compose.yml (variables ${...:?} obligatoires).
# ---------------------------------------------------------------------------
echo "== Configuration (.env + secrets) =="
REQUIRED_SECRETS="SECRET USER_AUTH_SECRET POSTGRES_PASSWORD OPENSEARCH_ADMIN_PASSWORD REDIS_PASSWORD S3_AWS_ACCESS_KEY_ID S3_AWS_SECRET_ACCESS_KEY MINIO_ROOT_USER MINIO_ROOT_PASSWORD ONIX_ACTIONS_API_KEY"
if [ -f .env ]; then
  ok ".env présent"
  missing=""
  for k in $REQUIRED_SECRETS; do
    v="$(get_env "$k")"
    if [ -n "$v" ]; then
      ok "secret $k défini (non affiché)"
    else
      ko "secret $k vide ou absent"
      missing="oui"
    fi
  done
  if [ -n "$missing" ]; then
    note "Génère/complète les secrets manquants : make secrets"
    add_action "Compléter les secrets manquants : make secrets."
  fi
  # Rappel sécurité non bloquant : .env doit être en 0600 (le gen-secrets le fait).
  if [ "$OS" = "Linux" ] || [ "$OS" = "Darwin" ]; then
    mode="$(stat -c '%a' .env 2>/dev/null || stat -f '%Lp' .env 2>/dev/null || echo '')"
    case "$mode" in
      600|400) : ;;  # correct, on ne pollue pas le récap
      "")      : ;;  # stat indisponible — on n'en fait pas un échec
      *)       warn ".env en mode $mode (recommandé 600) — make secrets le verrouille" ;;
    esac
  fi
else
  ko ".env absent"
  note "Créez-le et générez les secrets : make secrets (copie env.template puis remplit)."
  add_action "Créer .env et générer les secrets : make secrets."
fi

# ---------------------------------------------------------------------------
# Récapitulatif + conclusion.
# ---------------------------------------------------------------------------
echo
printf 'Récap : \033[32m%d OK\033[0m, \033[33m%d avertissements\033[0m, \033[31m%d échecs\033[0m\n' "$PASS" "$WARN" "$FAIL"
if [ "$FAIL" -eq 0 ]; then
  echo "✓ Prêt pour make up.$([ "$WARN" -gt 0 ] && echo ' (Avertissements ci-dessus : lisez-les, mais ils ne bloquent pas.)')"
  exit 0
fi
echo "✗ Pré-vol NON franchi — corrigez ces points BLOQUANTS avant make up :"
printf '%s\n' "$ACTIONS"
echo "  Puis relancez : bash scripts/preflight-local.sh"
exit 1
