#!/usr/bin/env bash
# ralph/loop.sh — Runner de boucle Ralph borné pour onix.
#
# Rejoue la consigne `ralph/scopes/<scope>.md` à un agent Claude Code, itération
# après itération, en forçant les portes qualité vertes et en journalisant l'état
# dans `ralph/state/<scope>.md`. S'arrête sur la sentinelle RALPH_DONE, au plafond,
# ou via un disjoncteur (gates rouges consécutifs / stagnation sans diff).
#
# Usage :
#   ./ralph/loop.sh <scope> [max_iterations]
#   ./ralph/loop.sh access-gateway 8
#
# Sûreté réellement appliquée (M13 — durcissement) :
#   - Un commit ne part QUE sur des gates VERTS, et « gates » inclut désormais la
#     SÉCURITÉ (bandit + gitleaks + pip-audit) en plus des tests du scope — sinon
#     le message « gates verts » serait un mensonge (cf. ORCHESTRATION.md A3).
#     trivy (scan d'image) exige Docker → délégué à la CI, hors boucle.
#   - Commit par CHEMINS du scope (pas `git add -A`) : un diff hors périmètre n'est
#     jamais commité « en douce » ; il est signalé pour revue.
#   - Refus de tourner sur un arbre SALE + verrou de concurrence (`flock`) : à
#     défaut d'isolation par worktree (ORCHESTRATION.md §4, encore aspirationnel),
#     on sérialise au minimum et on évite de balayer des changements préexistants.
#   - Disjoncteurs : arrêt après N gates rouges consécutifs ou N itérations sans diff.
set -euo pipefail

SCOPE="${1:?usage: ralph/loop.sh <scope> [max_iterations]}"
MAX_ITER="${2:-8}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROMPT_FILE="${ROOT}/ralph/scopes/${SCOPE}.md"
STATE_FILE="${ROOT}/ralph/state/${SCOPE}.md"
LOG_DIR="${ROOT}/ralph/state/logs"
mkdir -p "${LOG_DIR}"

# Plafonds des disjoncteurs (surchargeables par env).
MAX_CONSEC_RED="${RALPH_MAX_CONSEC_RED:-2}"   # arrêt après N gates rouges consécutifs
MAX_NOOP="${RALPH_MAX_NOOP:-2}"               # arrêt après N itérations sans diff

# --- Gardes d'entrée ---------------------------------------------------------
[ -f "${PROMPT_FILE}" ] || { echo "✗ Prompt introuvable : ${PROMPT_FILE}" >&2; exit 2; }
if ! command -v claude >/dev/null 2>&1; then
  echo "✗ CLI 'claude' absent du PATH. Installe Claude Code puis relance." >&2
  echo "  (Astuce : ce runner peut aussi servir de gabarit à un orchestrateur d'agents.)" >&2
  exit 3
fi

# Refus de tourner sur un arbre SALE : la boucle commit par chemins du scope ; un
# arbre déjà modifié risquerait de voir des changements préexistants emportés ou
# laissés en plan de façon ambiguë. On exige un point de départ propre.
if [ -n "$(cd "${ROOT}" && git status --porcelain)" ]; then
  echo "✗ Arbre de travail non propre — committe ou range tes changements avant la boucle." >&2
  exit 4
fi

# Verrou de concurrence : une seule boucle à la fois sur cette copie de travail
# (les surfaces ne sont pas isolées par worktree → on sérialise au minimum).
LOCK="${ROOT}/ralph/state/.loop.lock"
if command -v flock >/dev/null 2>&1; then
  exec 9>"${LOCK}"
  flock -n 9 || { echo "✗ Une autre boucle Ralph tourne déjà (verrou ${LOCK})." >&2; exit 5; }
fi

# Initialise le journal d'état si absent.
if [ ! -f "${STATE_FILE}" ]; then
  cp "${ROOT}/ralph/state/_TEMPLATE.md" "${STATE_FILE}" 2>/dev/null || \
    printf '# État Ralph — %s\n\n(initialisé par loop.sh)\n' "${SCOPE}" > "${STATE_FILE}"
fi

# Chemins à stager pour ce scope (code + doc agent), au lieu de `git add -A`.
# Reflète docs/scopes/scopes.json ('code' = préfixes) + le triptyque doc/state.
scope_paths() {
  case "${SCOPE}" in
    access-gateway) printf '%s ' "access-gateway/" ;;
    actions)        printf '%s ' "actions/" ;;
    rag-prompts)    printf '%s ' "tests/rag/" "prompts/" ;;
    monitoring)     printf '%s ' "monitoring/" ;;
    deploy-ops)     printf '%s ' "deploy/" "nginx/" ;;
    security-governance) : ;;  # scope transverse : pas de préfixe de code
    *)              : ;;
  esac
  printf '%s ' "docs/scopes/${SCOPE}.md" "docs/audit-reality/${SCOPE}.md" "ralph/state/${SCOPE}.md"
}

# Tests du scope (rapides).
run_scope_gates() {
  case "${SCOPE}" in
    access-gateway) ( cd "${ROOT}" && python -m pytest access-gateway/tests -q ) ;;
    actions)        ( cd "${ROOT}" && python -m pytest actions/tests -q ) ;;
    rag-prompts)    ( cd "${ROOT}" && python -m pytest tests/rag -q ) ;;
    deploy-ops)     ( cd "${ROOT}" && make compose-validate && make k8s-lint ) ;;
    monitoring)     ( cd "${ROOT}" && make compose-validate ) ;;
    *)              ( cd "${ROOT}" && make pytest ) ;;
  esac
}

# Portes de SÉCURITÉ (offline) : sans elles, « gates verts » mentirait — un secret,
# un bandit-medium ou une CVE pourrait être commité. trivy (image) = CI (Docker).
run_security_gates() {
  ( cd "${ROOT}" && make bandit && make gitleaks && make pip-audit )
}

run_gates() { run_scope_gates && run_security_gates; }

echo "▶ Boucle Ralph — scope=${SCOPE} max_iter=${MAX_ITER} (disjoncteurs: rouge=${MAX_CONSEC_RED} noop=${MAX_NOOP})"
consec_red=0
consec_noop=0
for i in $(seq 1 "${MAX_ITER}"); do
  ts="$(date +%Y%m%dT%H%M%S)"
  echo "── Itération ${i}/${MAX_ITER} (${ts}) ──"

  if grep -q '^RALPH_DONE' "${STATE_FILE}" 2>/dev/null; then
    echo "✔ ${SCOPE} : RALPH_DONE détecté — arrêt propre."
    break
  fi

  # Une itération = un passage de l'agent en mode headless (édits auto, coût borné).
  claude -p "$(cat "${PROMPT_FILE}")" \
    --permission-mode acceptEdits \
    --max-turns 40 \
    2>&1 | tee "${LOG_DIR}/${SCOPE}-${ts}.log" || {
      echo "⚠ Itération ${i} : l'agent a retourné une erreur — voir le log." >&2
    }

  # Stagnation : aucun changement produit → on incrémente le compteur no-op.
  if [ -z "$(cd "${ROOT}" && git status --porcelain)" ]; then
    echo "ℹ Itération ${i} : aucun changement produit."
    consec_noop=$((consec_noop + 1))
    if [ "${consec_noop}" -ge "${MAX_NOOP}" ]; then
      echo "■ ${MAX_NOOP} itérations sans diff — arrêt (stagnation)."
      break
    fi
    continue
  fi
  consec_noop=0

  # Portes qualité (scope + sécurité) : on ne commit JAMAIS sur du rouge.
  if run_gates; then
    consec_red=0
    ( cd "${ROOT}" && git add -- $(scope_paths) ) || true
    if [ -n "$(cd "${ROOT}" && git diff --cached --name-only)" ]; then
      ( cd "${ROOT}" && git commit -m "ralph(${SCOPE}): itération ${i} — incrément (tests scope + bandit/gitleaks/pip-audit verts)" )
      echo "✔ Itération ${i} commitée (gates verts, périmètre ${SCOPE})."
    else
      echo "ℹ Itération ${i} : rien DANS le périmètre ${SCOPE} à committer."
    fi
    # Signale (sans committer) tout changement laissé HORS périmètre.
    if [ -n "$(cd "${ROOT}" && git status --porcelain)" ]; then
      echo "⚠ Changements HORS périmètre ${SCOPE} laissés non commités — revue manuelle :" >&2
      ( cd "${ROOT}" && git status --porcelain ) >&2
    fi
  else
    echo "✗ Itération ${i} : gates ROUGES — pas de commit." >&2
    consec_red=$((consec_red + 1))
    if [ "${consec_red}" -ge "${MAX_CONSEC_RED}" ]; then
      echo "■ ${MAX_CONSEC_RED} gates rouges consécutifs — arrêt (disjoncteur)." >&2
      break
    fi
  fi
done

echo "■ Fin de boucle ${SCOPE}. Journal : ${STATE_FILE}"
