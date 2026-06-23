#!/bin/bash
# =============================================================================
# DÉMO RÉCONCILIATION contrat ↔ SI Fabric (POC AC360).
# -----------------------------------------------------------------------------
# OCR du contrat (PDF SharePoint) -> extraction des champs -> référence lue dans
# le SI Fabric (OneLake, via service principal) -> audit -> verdict d'écarts.
# Endpoint : POST /audit/reconcile/file (onix-actions). Cf. docs/scopes/actions.md.
#
# Usage :  ./demo-reconcile.sh [beta|gamma]
#   beta  -> contrat 12 500 €/an vs SI 13 000 €  => ECART (cotisation détectée)
#   gamma -> contrat 8 900 €/an  vs SI 8 900 €   => CONFORME
#
# Pré-requis (VM de démo) : pile onix lancée, ONIX_FABRIC_* configurés dans .env
# (URL OneLake + service principal), contrats dans $DEMO_CONTRATS_DIR. Adaptez
# les chemins pour un autre hôte.
# =============================================================================
set -uo pipefail
ONIX_DIR="${ONIX_DIR:-/home/azureuser/onix}"
DEMO_CONTRATS_DIR="${ONIX_DEMO_CONTRATS_DIR:-/home/azureuser/demo-contrats}"
ACTIONS_CTR="${ONIX_ACTIONS_CONTAINER:-onix-actions-1}"
cd "$ONIX_DIR" || { echo "racine onix introuvable: $ONIX_DIR"; exit 1; }
case "${1:-beta}" in
  beta)  PDF=contrat_beta-201.pdf;  CK="CLIENT BETA";;
  gamma) PDF=contrat_gamma-301.pdf; CK="CLIENT GAMMA";;
  *) echo "usage: demo-reconcile.sh [beta|gamma]"; exit 1;;
esac
API_KEY=$(sed -n 's/^ONIX_ACTIONS_API_KEY=//p' .env | head -1)
docker cp "$DEMO_CONTRATS_DIR/$PDF" "${ACTIONS_CTR}:/tmp/c.pdf" 2>/dev/null
echo "=== Reconciliation : $CK ($PDF)  vs  SI Fabric OneLake ==="
docker exec "$ACTIONS_CTR" sh -c \
  "curl -s -X POST -H 'X-API-Key: $API_KEY' -F 'file=@/tmp/c.pdf' -F 'client_key=$CK' http://localhost:8100/audit/reconcile/file" \
  | python3 -c "
import sys, json
r = json.load(sys.stdin)
print('VERDICT:', r['verdict'], '  (source reference:', r['_reference_source'] + ')')
for f in r['fields']:
    if f['statut'] in ('MATCH', 'MISMATCH'):
        flag = '  OK   ' if f['statut'] == 'MATCH' else ' ECART '
        print('  [' + flag + ']', f['champ'], ':  contrat=', repr(f.get('valeur_document')),
              ' vs  SI=', repr(f.get('valeur_reference')))
"
