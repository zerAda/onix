<!-- Pour clore le scope, écrire `RALPH_DONE` comme TOUTE PREMIÈRE ligne. -->
# État Ralph — rag-prompts

## Backlog (source : docs/audit-reality/rag-prompts.md)
| ID | Prio | Écart | Axe | Statut |
|---|---|---|---|---|
| R1 | P1 | Résultats LIVE sans encadré « indicatif » (`LIVE_GUARDRAILS_RESULTS.md`) | A1/A2 | ✅ encadré + traçabilité (transcript brut = futur run) |
| R2 | P1 | Baseline RAGAS non reproductible (générateur absent du repo) | A2/A6 | ✅ générateur déterministe byte-level (offline) |
| R3 | P1 | Red-team limité (20 vecteurs, FR seul, T=0) | A2 | ⬜ reporté (extension à valider idéalement en live) |
| R4 | P2 | Comptage « 21 vecteurs » imprécis / « 20+ » incohérent | A1 | ✅ harmonisé (20 RT + 1 nominal = 21 cas) |
| R5 | P2 | Transcripts E2E non datés (timestamp + version Ollama) | A1 | ✅ côté doc autorisée (E2E §4.3) + capture auto LIVE |

## Journal
| Itér. | Date | Item | Correctif | Gates | SHA |
|---|---|---|---|---|---|
| 1 | 2026-06-18 | R2 | `scripted_judge.py` + `gen_baseline.py` (régén. baseline byte-level, offline) + RAG_EVAL/README + test provenance | `pytest tests/rag -q` 175 passed, 64 skipped | bf096ca |
| 1 | 2026-06-18 | R1 | Encadré « indicatif/non reproductible byte-level » (run_live.py + doc) + `ollama_version()` + cadrage comptage + tests | idem (175 passed) | 6c0ccd4 |
| 1 | 2026-06-18 | R4+R5 | Comptage « 21 cas (20 RT + 1 NOM01) » (E2E/QA) + note traçabilité E2E §4.3 | idem (175 passed) | e75ad92 |
| 2 | 2026-06-25 | GUARD-CITATION (faux refus) | `has_citation` (post-filtre couche 3) ne reconnaissait PAS le format de citation natif d'Onyx `[1]`/`[[1]]` → toute réponse chiffrée correctement sourcée à ce format était **faussement bloquée** (no_citation), rendant le RAG inutilisable. Ajout reconnaissance `` `\[\d` `` (capte `[1]` ET `[[1]]` via le crochet interne). N'affecte QUE la règle groundedness (les règles sécurité 1-4 passent avant → red-team inchangé). +2 tests (cas positif fait+`[[1]]` passe ; reconnaissance unitaire). | `tests/rag` **178 passed/64⏭** ; bandit 0 | (branche prod) |
| 3 | 2026-06-25 | GUARD-VOCAB (groundedness métier) | `asserts_a_fact` ne connaissait que `cotisation/plafond/echeance/plancher/montant` → un fait chiffré sur `franchise`, `garantie`, `remboursement`, `prime`, `taux`, `capital`, `indemnite`, `carence` (vocabulaire assurance GEREP) **sans source passait** le garde-fou. Extension `_FACT_TERMS`. Complémentaire à `has_citation` : un fait correctement cité reste autorisé, seul l'ungrounded est bloqué. +2 tests (fait assurance non sourcé bloqué / cité passe). | `tests/rag` **180 passed/64⏭** ; bandit 0 | (branche prod) |

## Questions bloquantes
- (aucune) — R3 (extension red-team : jailbreaks avancés, multi-langue, variation
  de T°) laissé pour une itération ultérieure ; bénéficierait d'un run live pour
  valider l'effet (non disponible dans cet environnement).

## Notes itération 1
- Contrainte respectée : **aucun modèle live**. La baseline est régénérée par un
  juge SCRIPTÉ déterministe (pas un LLM) ; les chiffres LIVE existants sont
  **encadrés**, pas régénérés.
- Découverte : le `ScriptedJudge` (auparavant dans les tests) produit EXACTEMENT
  0.75/0.875/1.0 sur le golden set → confirme que c'était bien la provenance de la
  baseline committée. Extrait + outillé pour la rendre reproductible.
- Propriété de fichiers respectée : `access-gateway/tests/e2e/*` NON modifié
  (hors scope) ; R5 traité uniquement côté `docs/E2E_GUARDRAILS.md`.

## Critères de sortie A1–A7
- [x] A1 (écarts P2 de comptage/traçabilité fermés ; P1 preuve fiabilisée)
- [x] A2 (tests offline verts + nouveaux tests provenance/encadré)
- [ ] A3 (bandit/pip-audit/gitleaks non relancés cette itération — pytest seul)
- [ ] A4(n/a) - [ ] A5 - [x] A6 (baseline reproductible, gen_baseline --check)
- [ ] A7(n/a)
> Reste avant `RALPH_DONE` : R3 (extension red-team) + `make test` complet (A3).
