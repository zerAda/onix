#!/usr/bin/env bash
# ralph/loop.sh — Runner de boucle Ralph borné pour onix.
#
# Rejoue la consigne `ralph/scopes/<scope>.md` à un agent Claude Code, itération
# après itération, en forçant les portes qualité vertes et en journalisant l'état
# dans `ralph/state/<scope>.md`. S'arrête sur la sentinelle RALPH_DONE ou au plafond.
#
# Usage :
#   ./ralph/loop.sh <scope> [max_iterations]
#   ./ralph/loop.sh access-gateway 8
#
# Conventions (cf. ralph/ORCHESTRATION.md) :
#   - Un commit ne part QUE sur des gates verts.
#   - Surfaces disjointes : un scope = une surface ; ne pas paralléliser deux scopes
#     qui touchent les mêmes fichiers partagés (Makefile, compose racine, DOCS_INDEX).
set -euo pipefail

SCOPE="${1:?usage: ralph/loop.sh <scope> [max_iterations]}"
MAX_ITER="${2:-8}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROMPT_FILE="${ROOT}/ralph/scopes/${SCOPE}.md"
STATE_FILE="${ROOT}/ralph/state/${SCOPE}.md"
LOG_DIR="${ROOT}/ralph/state/logs"
mkdir -p "${LOG_DIR}"

# --- Garde-fous d'entrée -----------------------------------------------------
[ -f "${PROMPT_FILE}" ] || { echo "✗ Prompt introuvable : ${PROMPT_FILE}" >&2; exit 2; }
if ! command -v claude >/dev/null 2>&1; then
  echo "✗ CLI 'claude' absent du PATH. Installe Claude Code puis relance." >&2
  echo "  (Astuce : ce runner peut aussi servir de gabarit à un orchestrateur d'agents.)" >&2
  exit 3
fi

# Initialise le journal d'état si absent.
if [ ! -f "${STATE_FILE}" ]; then
  cp "${ROOT}/ralph/state/_TEMPLATE.md" "${STATE_FILE}" 2>/dev/null || \
    printf '# État Ralph — %s\n\n(initialisé par loop.sh)\n' "${SCOPE}" > "${STATE_FILE}"
fi

# Gate qualité : ciblé par scope pour des itérations rapides, complet sinon.
run_gates() {
  case "${SCOPE}" in
    access-gateway) ( cd "${ROOT}" && python -m pytest access-gateway/tests -q ) ;;
    actions)        ( cd "${ROOT}" && python -m pytest actions/tests -q ) ;;
    rag-prompts)    ( cd "${ROOT}" && python -m pytest tests/rag -q ) ;;
    deploy-ops)     ( cd "${ROOT}" && make compose-validate && make k8s-lint ) ;;
    monitoring)     ( cd "${ROOT}" && make compose-validate ) ;;
    *)              ( cd "${ROOT}" && make pytest ) ;;
  esac
}

echo "▶ Boucle Ralph — scope=${SCOPE} max_iter=${MAX_ITER}"
for i in $(seq 1 "${MAX_ITER}"); do
  ts="$(date +%Y%m%dT%H%M%S)"
  echo "── Itération ${i}/${MAX_ITER} (${ts}) ──"

  # Sentinelle de fin : on s'arrête si le scope est déclaré terminé.
  if grep -q '^RALPH_DONE' "${STATE_FILE}" 2>/dev/null; then
    echo "✔ ${SCOPE} : RALPH_DONE détecté — arrêt propre."
    break
  fi

  # Une itération = un passage de l'agent en mode headless.
  # --permission-mode acceptEdits : autorise les édits sans prompt (boucle non-interactive).
  #   Alternatives : 'plan' (lecture seule) pour un dry-run, ou --dangerously-skip-permissions
  #   en bac-à-sable isolé uniquement. --max-turns borne le coût par itération.
  claude -p "$(cat "${PROMPT_FILE}")" \
    --permission-mode acceptEdits \
    --max-turns 40 \
    2>&1 | tee "${LOG_DIR}/${SCOPE}-${ts}.log" || {
      echo "⚠ Itération ${i} : l'agent a retourné une erreur — voir le log." >&2
    }

  # Portes qualité : on ne commit JAMAIS sur du rouge.
  if run_gates; then
    if [ -n "$(cd "${ROOT}" && git status --porcelain)" ]; then
      ( cd "${ROOT}" \
        && git add -A \
        && git commit -m "ralph(${SCOPE}): itération ${i} — incrément production-ready (gates verts)" )
      echo "✔ Itération ${i} commitée (gates verts)."
    else
      echo "ℹ Itération ${i} : aucun changement à committer."
    fi
  else
    echo "✗ Itération ${i} : gates ROUGES — pas de commit. L'agent réparera à la prochaine passe." >&2
  fi
done

echo "■ Fin de boucle ${SCOPE}. Journal : ${STATE_FILE}"
