#!/usr/bin/env bash
# =============================================================================
# Génère des secrets forts dans .env (idempotent : ne réécrit jamais un secret
# déjà défini, pour ne pas casser une stack en cours). Crée .env depuis le
# gabarit si absent, puis verrouille les permissions (chmod 600).
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

# Fichier d'environnement cible : `.env` par défaut (dev/local). Surchargeable
# pour le multi-environnement (test/prod) via $ENV_FILE ou en 1er argument :
#   ENV_FILE=deploy/prod/.env.prod ./scripts/gen-secrets.sh
#   ./scripts/gen-secrets.sh deploy/prod/.env.prod
ENV_FILE="${ENV_FILE:-${1:-.env}}"
# Gabarit associé : env.prod.template pour les environnements prod/test (sous
# deploy/prod/), env.template sinon. Surchargeable via $TEMPLATE.
case "$ENV_FILE" in
  deploy/prod/*|*/deploy/prod/*) TEMPLATE="${TEMPLATE:-deploy/prod/env.prod.template}" ;;
  *access-gateway*)              TEMPLATE="${TEMPLATE:-access-gateway/.env.template}" ;;
  *)                             TEMPLATE="${TEMPLATE:-env.template}" ;;
esac

if [ ! -f "$ENV_FILE" ]; then
  cp "$TEMPLATE" "$ENV_FILE"
  echo "→ $ENV_FILE créé depuis $TEMPLATE"
fi

# Génère une chaîne alphanumérique de N caractères.
# IMPORTANT (RC 141 / SIGPIPE) : avec `set -euo pipefail`, un pipe dont la source
# est INFINIE (`tr … </dev/urandom | head`) reçoit SIGPIPE quand `head` ferme le
# flux après N octets → le script meurt (RC 141). On lit donc un bloc FINI de
# /dev/urandom AVANT de filtrer/tronquer : aucune source infinie, pas de SIGPIPE.
# (On lit large — n*8 octets — pour garder ~n caractères même après filtrage des
#  octets non-alphanumériques, puis on tronque à n.)
rand() {
  local n="${1:-32}" out=""
  if command -v openssl >/dev/null 2>&1; then
    # openssl produit une sortie FINIE → pas de SIGPIPE possible.
    out="$(openssl rand -base64 $((n * 2)) | LC_ALL=C tr -dc 'A-Za-z0-9')"
  fi
  # Fallback (ou complément si openssl a rendu trop peu) : lecture BORNÉE.
  while [ "${#out}" -lt "$n" ]; do
    out="$out$(head -c "$((n * 8))" /dev/urandom | LC_ALL=C tr -dc 'A-Za-z0-9')"
  done
  printf '%s' "${out:0:$n}"
}
gen_user() { printf 'onyx_%s' "$(rand 12)"; }
# OpenSearch exige une complexité (maj/min/chiffre/spécial) : on garantit les classes.
gen_os_pass() { printf '%sAa9!' "$(rand 24)"; }

get_val() { sed -n "s/^$1=//p" "$ENV_FILE" | head -n1; }

set_val() {
  local key="$1" val="$2"
  if grep -q "^$key=" "$ENV_FILE"; then
    awk -v k="$key" -v v="$val" -F= '$1==k{print k"="v; next}{print}' \
      "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

ensure() { # ensure KEY GENERATOR [ARGS...]
  local key="$1"; shift
  if [ -z "$(get_val "$key")" ]; then
    set_val "$key" "$("$@")"
    echo "  + $key généré"
  else
    echo "  = $key déjà défini (inchangé)"
  fi
}

echo "Génération des secrets dans $ENV_FILE :"
ensure SECRET                rand 48
ensure USER_AUTH_SECRET      rand 48
# Chiffrement AU REPOS des secrets connecteurs/LLM/OAuth stockés en base par Onyx.
# CRITIQUE : sans clé NON VIDE, Onyx les écrit EN CLAIR dans Postgres SANS erreur au
# boot (asymétrie : échoue sur USER_AUTH_SECRET vide, pas sur celle-ci). On la génère
# ici comme les autres secrets pour fermer le footgun. Cf. SECURITY.md:33, ARCHITECTURE.md:67.
ensure ENCRYPTION_KEY_SECRET rand 48
ensure POSTGRES_PASSWORD     rand 32
ensure DB_READONLY_PASSWORD  rand 32
ensure OPENSEARCH_ADMIN_PASSWORD gen_os_pass
ensure MINIO_ROOT_USER       gen_user
ensure MINIO_ROOT_PASSWORD   rand 32
# Redis : mot de passe (alphanumérique → sûr pour --requirepass et les URL).
# Honoré par Onyx (api_server/background lisent REDIS_PASSWORD) et par redis-server.
ensure REDIS_PASSWORD        rand 32
# Clé API du microservice onix-actions (en-tête X-API-Key). 48 caractères alphanum.
ensure ONIX_ACTIONS_API_KEY  rand 48

# Les identifiants S3 d'Onyx pointent sur le compte root MinIO (mêmes valeurs).
if [ -z "$(get_val S3_AWS_ACCESS_KEY_ID)" ]; then
  set_val S3_AWS_ACCESS_KEY_ID "$(get_val MINIO_ROOT_USER)"; echo "  + S3_AWS_ACCESS_KEY_ID = MINIO_ROOT_USER"
fi
if [ -z "$(get_val S3_AWS_SECRET_ACCESS_KEY)" ]; then
  set_val S3_AWS_SECRET_ACCESS_KEY "$(get_val MINIO_ROOT_PASSWORD)"; echo "  + S3_AWS_SECRET_ACCESS_KEY = MINIO_ROOT_PASSWORD"
fi

# --- WS2 ---
# Secrets de la couche sécurité/RGPD d'onix-actions (cf. docs/SECURITY_RGPD_ACTIONS.md).
echo "Génération des secrets WS2 (sécurité/RGPD onix-actions) :"
# Clé ADMIN distincte (en-tête X-Admin-Key) : OBLIGATOIRE par défaut (fail-closed)
# pour /admin/*. Séparer la clé admin de la clé de service évite qu'une fuite de
# la clé d'appel donne le contrôle d'administration (kill-switch, blocage).
ensure ONIX_ACTIONS_ADMIN_KEY          rand 48
# Secret HMAC d'identité d'appelant (signature par appel : X-Onix-Signature).
# Lie identité + horodatage + requête -> ni rejouable, ni transférable.
ensure ONIX_ACTIONS_CALLER_HMAC_SECRET rand 48
# Clé HMAC de chaînage du journal d'audit (admin_audit tamper-evident). Toute
# altération d'une ligne casse la chaîne et devient détectable (/admin/audit/verify).
ensure ONIX_ACTIONS_AUDIT_HMAC_KEY     rand 48

# --- Intégration (câblage WS6) ---
# Mot de passe admin Grafana (référencé par monitoring/ + env.template WS6). Sans
# génération, Grafana démarrerait sur 'admin' par défaut → on le force ici.
ensure GRAFANA_ADMIN_PASSWORD          rand 32

# --- Secrets PASSERELLE / PROD (aléatoires) --------------------------------
# Générés quand on cible l'env de la passerelle (access-gateway/.env) OU l'env de
# PROD (deploy/prod/.env.prod, qui DÉPLOIE la passerelle + oauth2-proxy). On ne
# génère QUE les secrets ALÉATOIRES ; les valeurs Entra EXTERNES (OAUTH_CLIENT_ID,
# OAUTH_CLIENT_SECRET, OAUTH2_PROXY_OIDC_ISSUER_URL, GATEWAY_GRAPH_*) restent à
# renseigner à la main (cf. docs/DEPLOY_PROD.md).
case "$ENV_FILE" in
  *access-gateway*|deploy/prod/*|*/deploy/prod/*)
    echo "Secrets passerelle :"
    # REQUIS si le cache est activé : sans lui le cache se DÉSACTIVE au démarrage
    # (log CRITICAL). Évite le footgun « cache silencieusement OFF ».
    ensure GATEWAY_CACHE_HMAC_SECRET     rand 48
    # Sel HMAC de pseudonymisation du journal d'accès (RGPD : pas d'UPN en clair).
    ensure GATEWAY_AUDIT_SALT            rand 48
    ;;
esac
# Secret de cookie oauth2-proxy (PROD uniquement) : sessions OIDC chiffrées.
case "$ENV_FILE" in
  deploy/prod/*|*/deploy/prod/*)
    echo "Secrets prod (oauth2-proxy) :"
    ensure OAUTH2_PROXY_COOKIE_SECRET    rand 32
    ;;
esac

chmod 600 "$ENV_FILE"
echo "✓ Secrets prêts. Permissions $ENV_FILE → 600 (lecture/écriture propriétaire uniquement)."
echo "  Sauvegardez ces secrets dans votre coffre (ex: Azure Key Vault / gestionnaire de mots de passe)."
