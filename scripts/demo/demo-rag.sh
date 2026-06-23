#!/bin/bash
# =============================================================================
# RAG NON-AGENTIQUE onix — récupération + génération locale (Ollama).
# -----------------------------------------------------------------------------
# Contourne la boucle agentique d'Onyx 4.1.1, CASSÉE avec les modèles locaux
# (bug #12, cf. scripts/demo/README.md). Architecture recommandée pour un modèle
# local : on NE dépend PAS du tool-calling du modèle. On récupère le bon document,
# puis on génère une réponse sourcée par un appel DIRECT à Ollama (/api/generate).
#
# Usage :  ./demo-rag.sh "Quelle est la cotisation du dossier CLIENT BETA ?"
#
# Pré-requis (VM de démo) : fiches indexables dans $RAG_DOCS_DIR, conteneur
# onix-actions-1 (a `curl`) sur le même réseau qu'Ollama. Adaptez les chemins
# pour un autre hôte.
# =============================================================================
set -uo pipefail
Q="$*"
[ -z "$Q" ] && { echo "usage: demo-rag.sh <question>"; exit 1; }
RAG_DOCS_DIR="${ONIX_RAG_DOCS_DIR:-/home/azureuser/rag-docs}"
RAG_MODEL="${ONIX_RAG_MODEL:-gemma3:4b}"
ACTIONS_CTR="${ONIX_ACTIONS_CONTAINER:-onix-actions-1}"
cd "$RAG_DOCS_DIR" 2>/dev/null || { echo "pas de fiches dans $RAG_DOCS_DIR"; exit 1; }

# 1) RÉCUPÉRATION : la fiche dont le contenu recouvre le plus la question.
printf '%s' "$Q" > /tmp/q.txt
BEST=$(python3 -c "
import glob, re
q = set(re.findall(r'[a-z]{4,}', open('/tmp/q.txt', encoding='utf-8').read().lower()))
best, bs = None, -1
for f in glob.glob('*.txt'):
    w = set(re.findall(r'[a-z]{4,}', open(f, encoding='utf-8').read().lower()))
    s = len(q & w)
    if s > bs: bs, best = s, f
print(best or '')
")
[ -z "$BEST" ] && { echo "aucune fiche pertinente"; exit 1; }
cp "$BEST" /tmp/ctx.txt

# 2) GÉNÉRATION : réponse grounded à partir du SEUL contexte récupéré (local).
python3 > /tmp/g.json <<'PYEOF'
import json, os
ctx = open('/tmp/ctx.txt', encoding='utf-8').read()
q = open('/tmp/q.txt', encoding='utf-8').read().strip()
prompt = ("Tu es l'assistant client GEREP, souverain et local. Reponds en francais, "
          "concis, UNIQUEMENT a partir du CONTEXTE. Cite le numero de dossier.\n\n"
          "CONTEXTE:\n" + ctx + "\n\nQUESTION: " + q + "\n\nREPONSE:")
print(json.dumps({"model": os.environ.get("ONIX_RAG_MODEL", "gemma3:4b"),
                  "prompt": prompt, "stream": False, "keep_alive": "2h"}))
PYEOF
docker cp /tmp/g.json "${ACTIONS_CTR}:/tmp/g.json" >/dev/null 2>&1
echo "  [source recuperee : $BEST]"
ONIX_RAG_MODEL="$RAG_MODEL" docker exec -e ONIX_RAG_MODEL="$RAG_MODEL" "$ACTIONS_CTR" \
  sh -c 'curl -s http://ollama:11434/api/generate -d @/tmp/g.json' \
  | python3 -c "import sys, json; print('  REPONSE :', json.load(sys.stdin).get('response', '').strip())"
