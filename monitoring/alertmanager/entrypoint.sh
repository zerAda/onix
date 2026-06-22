#!/bin/sh
# =============================================================================
# onix — Entrypoint Alertmanager : rendu fail-closed du gabarit de config.
# -----------------------------------------------------------------------------
# Rôle : substituer ${ALERT_WEBHOOK_URL} dans alertmanager.yml.tmpl AVANT de
# lancer Alertmanager, et REFUSER de démarrer si l'URL est absente/vide.
#
# Pourquoi : Alertmanager n'expanse PAS les variables d'environnement dans sa
# config. Sans rendu, "${ALERT_WEBHOOK_URL}" resterait une chaîne littérale et
# les alertes partiraient dans le vide. INVARIANT (AGENTS.md) : fail-closed —
# en cas de doute/config absente, refus BRUYANT, jamais d'avalement silencieux.
#
# Stdlib-first : POSIX sh + sed (présents dans l'image prom/alertmanager,
# busybox). Aucune dépendance (pas d'envsubst/gettext requis).
# =============================================================================
set -eu

TMPL="/etc/alertmanager/alertmanager.yml.tmpl"
OUT="/tmp/alertmanager.rendered.yml"

# --- FAIL-CLOSED : URL de webhook obligatoire -------------------------------
# Vide ou absente => on refuse de démarrer (sinon toute alerte serait perdue
# silencieusement, comportement vulnérable de l'ancienne config).
if [ -z "${ALERT_WEBHOOK_URL:-}" ]; then
  echo "CRITICAL [alertmanager] ALERT_WEBHOOK_URL absent/vide : REFUS de démarrer." >&2
  echo "CRITICAL [alertmanager] Les alertes (budget FinOps, service down, audit rompu) n'auraient AUCUNE destination." >&2
  echo "CRITICAL [alertmanager] Renseignez ALERT_WEBHOOK_URL (cf. env.template) avant 'make monitor-up'. Fail-closed." >&2
  exit 1
fi

# Garde supplémentaire : l'URL doit ressembler à un endpoint HTTP(S).
case "${ALERT_WEBHOOK_URL}" in
  http://*|https://*) : ;;
  *)
    echo "CRITICAL [alertmanager] ALERT_WEBHOOK_URL ne commence pas par http:// ou https:// : REFUS (fail-closed)." >&2
    exit 1
    ;;
esac

if [ ! -f "${TMPL}" ]; then
  echo "CRITICAL [alertmanager] Gabarit ${TMPL} introuvable : REFUS (fail-closed)." >&2
  exit 1
fi

# --- Rendu : substitution littérale de ${ALERT_WEBHOOK_URL} ------------------
# On échappe les caractères spéciaux sed de l'URL (&, /, \) pour une
# substitution sûre, puis on remplace le placeholder par la valeur réelle.
_esc=$(printf '%s' "${ALERT_WEBHOOK_URL}" | sed -e 's/[\/&\\]/\\&/g')
sed "s/\${ALERT_WEBHOOK_URL}/${_esc}/g" "${TMPL}" > "${OUT}"

# Vérif anti-régression : le placeholder ${ALERT_WEBHOOK_URL} ne doit plus
# subsister dans le rendu (le mot seul reste présent dans les commentaires).
if grep -qF '${ALERT_WEBHOOK_URL}' "${OUT}"; then
  echo "CRITICAL [alertmanager] Placeholder non substitué dans le rendu : REFUS (fail-closed)." >&2
  exit 1
fi

echo "[alertmanager] Config rendue depuis le gabarit ; webhook de notification ACTIF (send_resolved=true)."

# Lance Alertmanager avec la config rendue. "$@" = arguments du `command:`.
exec /bin/alertmanager --config.file="${OUT}" "$@"
