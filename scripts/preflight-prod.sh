#!/bin/sh
# =============================================================================
# onix — Garde-fou « défaut-sûr » de PRODUCTION (pré-vol).
# -----------------------------------------------------------------------------
# REFUSE de laisser démarrer une instance EXPOSÉE (BIND_IP != 127.0.0.1) si la
# triade de sécurité minimale n'est pas réunie :
#     TLS (domaine public)  +  OIDC (Entra ID)  +  vérification d'e-mail.
# Tourne dans un conteneur busybox éphémère AVANT api_server/web_server/caddy
# (qui en dépendent via depends_on: condition service_healthy). S'il échoue,
# il sort en erreur (jamais healthy) → la stack exposée NE MONTE PAS.
#
# En accès STRICTEMENT localhost (BIND_IP=127.0.0.1), le pré-vol laisse passer :
# la surface est limitée à la machine (cas dev/test sur l'hôte). POSIX sh pur.
# =============================================================================
set -u

BIND_IP="${BIND_IP:-127.0.0.1}"
AUTH_TYPE="${AUTH_TYPE:-basic}"
REQUIRE_EMAIL_VERIFICATION="${REQUIRE_EMAIL_VERIFICATION:-false}"
VALID_EMAIL_DOMAINS="${VALID_EMAIL_DOMAINS:-}"
ONYX_DOMAIN="${ONYX_DOMAIN:-}"
WEB_DOMAIN="${WEB_DOMAIN:-}"
OAUTH_CLIENT_ID="${OAUTH_CLIENT_ID:-}"
OAUTH_CLIENT_SECRET="${OAUTH_CLIENT_SECRET:-}"
OPENID_CONFIG_URL="${OPENID_CONFIG_URL:-}"

ERR=0
fail() { printf '  \033[31m[REFUS]\033[0m %s\n' "$1"; ERR=1; }
ok()   { printf '  \033[32m[ OK ]\033[0m %s\n' "$1"; }

echo "== Pré-vol production (défaut-sûr) =="
echo "   BIND_IP=$BIND_IP  AUTH_TYPE=$AUTH_TYPE"

# Exposition réseau ? Tout ce qui n'est pas strictement la boucle locale est
# considéré EXPOSÉ (0.0.0.0, ::, IP de LAN/publique, nom d'hôte...).
EXPOSED=1
case "$BIND_IP" in
  127.0.0.1|::1|localhost) EXPOSED=0 ;;
esac

if [ "$EXPOSED" -eq 0 ]; then
  ok "Accès localhost strict ($BIND_IP) — garde-fou non bloquant (cas dev/test)."
  : > /tmp/ok
  echo "✓ Pré-vol franchi (localhost)."
  # On reste vivant pour que le healthcheck voie /tmp/ok (état healthy stable).
  exec sleep 2147483647
fi

echo "   Exposition réseau détectée → exigence TLS + OIDC + vérification e-mail."

# --- 1. TLS / domaine public -------------------------------------------------
[ -n "$ONYX_DOMAIN" ] || fail "ONYX_DOMAIN vide : TLS impossible (Caddy a besoin d'un domaine public)."
case "$WEB_DOMAIN" in
  https://*) ok "WEB_DOMAIN en https." ;;
  *) fail "WEB_DOMAIN doit commencer par https:// (callback OIDC + cookies sécurisés). Reçu: '${WEB_DOMAIN:-<vide>}'." ;;
esac

# --- 2. OIDC (SSO Entra ID) --------------------------------------------------
[ "$AUTH_TYPE" = "oidc" ] || fail "AUTH_TYPE doit être 'oidc' en exposition (reçu: '$AUTH_TYPE'). Pas de comptes 'basic' exposés."
[ -n "$OAUTH_CLIENT_ID" ]     || fail "OAUTH_CLIENT_ID vide (enregistrement d'application Entra ID requis)."
[ -n "$OAUTH_CLIENT_SECRET" ] || fail "OAUTH_CLIENT_SECRET vide."
case "$OPENID_CONFIG_URL" in
  https://*/.well-known/openid-configuration) ok "OPENID_CONFIG_URL bien formée." ;;
  "") fail "OPENID_CONFIG_URL vide (ex: https://login.microsoftonline.com/<TENANT>/v2.0/.well-known/openid-configuration)." ;;
  *) fail "OPENID_CONFIG_URL invalide : doit être https://.../.well-known/openid-configuration (reçu: '$OPENID_CONFIG_URL')." ;;
esac

# --- 3. Vérification d'e-mail + domaines autorisés ---------------------------
[ "$REQUIRE_EMAIL_VERIFICATION" = "true" ] || fail "REQUIRE_EMAIL_VERIFICATION doit être 'true' en exposition (reçu: '$REQUIRE_EMAIL_VERIFICATION')."
[ -n "$VALID_EMAIL_DOMAINS" ] || fail "VALID_EMAIL_DOMAINS vide : restreignez aux domaines de votre organisation (ex: exemple.fr)."

if [ "$ERR" -ne 0 ]; then
  echo ""
  echo "✗ Pré-vol ÉCHOUÉ : démarrage exposé REFUSÉ tant que TLS + OIDC + vérification"
  echo "  d'e-mail ne sont pas tous configurés. Voir docs/DEPLOY_PROD.md."
  echo "  (Pour un test purement local sans ces garanties : BIND_IP=127.0.0.1.)"
  exit 1
fi

: > /tmp/ok
echo ""
echo "✓ Pré-vol franchi : TLS + OIDC + vérification d'e-mail réunis. Démarrage autorisé."
# Rester vivant pour conserver l'état healthy (les dépendants attendent healthy).
exec sleep 2147483647
