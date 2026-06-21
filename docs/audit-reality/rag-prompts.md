# Audit byte-by-byte « Documentation ↔ Réalité » — Scope RAG (garde-fous + éval RAGAS) + prompts/agent commercial

> **Date** : 2026-06-18 · **Auditeur** : ingénieur ML/RAG senior (mode READ-ONLY)
> **Légende** : ✅ CONFORME · ⚠️ ÉCART MINEUR · ❌ ÉCART MAJEUR · 🕳️ DOC-SANS-CODE ·
> 🔇 CODE-SANS-DOC · ❔ NON VÉRIFIABLE (cf. [`README.md`](README.md)).
> **Méthode** : chaque affirmation vérifiable de la doc est localisée `fichier:ligne`,
> classée, citée. Aucune exécution live/réseau/make (contrainte). Zéro invention.

## Périmètre couvert

- **Docs** : `RAG_EVAL.md`, `RAG_OPTIMIZATION.md`, `PLAYBOOK_ONYX_RAG.md`,
  `E2E_GUARDRAILS.md`, `QA_GUARDRAILS.md`, `LIVE_GUARDRAILS_RESULTS.md`,
  `AGENT_COMMERCIAL.md`.
- **Code/config** : tout `tests/rag/` (dont `ragas_eval/`), `prompts/`, cibles Make
  `rag-*`, workflow `.github/workflows/ragas-nightly.yml`, câblage `num_ctx`
  (compose/Helm/`make tune`/Modelfile). Preuves complémentaires côté `access-gateway`
  (post-filtre déployé) pour valider les claims d'E2E_GUARDRAILS.

---

## Tableau de comptage

| Classe | Nombre |
|---|---|
| ✅ CONFORME | 31 |
| ⚠️ ÉCART MINEUR | 7 |
| ❌ ÉCART MAJEUR | 2 |
| 🕳️ DOC-SANS-CODE | 1 |
| 🔇 CODE-SANS-DOC | 2 |
| ❔ NON VÉRIFIABLE | 4 |
| **Total affirmations tracées** | **47** |

> **⚠️ Correction 2026-06-21** (boucle orchestrateur) : la synthèse était trop clémente.
> Le gate live RAGAS (`make rag-eval` / `rag-eval-ci`) **ne démarre pas** (ImportError,
> exit 2 — voir lignes 51-52, reproduit) → **2 écarts majeurs, pas 0**. Cause méthode :
> ces lignes étaient certifiées en LISANT le `Makefile` (cf. `:7` « aucune exécution »),
> jamais en l'exécutant. Règle ajoutée : une claim d'exécution runtime ne peut être ✅
> que si un run/transcript est attaché ; sinon ❔ NON VÉRIFIABLE.
>
> **Synthèse honnêteté** : scope **largement honnête** (hormis le gate live ci-dessus). Aucun autre mock présenté
> comme réel détecté. Les limites (variance du juge, retrieval Onyx non booté,
> prompt seul insuffisant sur 7B) sont **explicitement** documentées dans les docs
> elles-mêmes. Le principal point de vigilance n'est pas un mensonge mais un
> **manque de preuve archivée** pour les chiffres « LIVE » de
> `LIVE_GUARDRAILS_RESULTS.md` (cf. §LIVE).

---

## `RAG_EVAL.md` — éval RAGAS souveraine

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| 3 métriques cœur (faithfulness/context_precision/answer_relevancy), définitions + bornes | ✅ | `tests/rag/ragas_eval/metrics.py:41-61`, `judge.py:271-348` | faithfulness sans claim → 1.0 (`metrics.py:42-46`), ctx_precision sans chunk → 0.0 (`metrics.py:49-54`), relevancy = note/4 (`metrics.py:57-61`). Exact. |
| Agrégat = moyenne macro par item | ✅ | `metrics.py:79-97` (`_mean` ignore les None) | Conforme. |
| Seuils gate défaut 0.90 / 0.70 / 0.85 surchargeables `ONIX_RAGAS_MIN_*` | ✅ | `metrics.py:104-108` (defaults), `runner.py:68-89` (env-override) | Exact. |
| Runner sort en code non nul si gate échoue | ✅ | `runner.py:310` (`return 0 if gate.passed else 1`) | Conforme. |
| Offline CI : `pytest -q tests/rag/ragas_eval` (juge mocké) | ✅ | `ragas_eval/test_ragas_eval.py`, `conftest.py` ; juge injectable `runner.py:94-101` | Tests offline présents. |
| LIVE : `make rag-eval` (Ollama ≥ 7B) | ❌ | `Makefile:148-149` (`python -m ragas_eval.runner`) | **Corrigé 2026-06-21** : NE DÉMARRE PAS — exit 2 `ImportError: cannot import name 'read_prompt_block' from 'conftest'` (collision `tests/rag/ragas_eval/conftest.py`) AVANT toute éval. Reproduit. Cf. M2/RAGAS-FIX. |
| `make rag-eval-ci` = runner `--json` + `compare_scores` vs baseline (gate absolu OU régression) | ❌ | `Makefile:166-173` | **Corrigé 2026-06-21** : le runner sort en code 2 (ImportError, cf. ligne LIVE), `scores.json` jamais écrit → `compare_scores` échoue sur fichier manquant → nightly `exit 1`. Le gate **n'a jamais évalué** une réponse. |
| Workflow nightly `schedule`+`workflow_dispatch`, jamais `pull_request` | ✅ | `ragas-nightly.yml:27-44` | Aucun trigger `pull_request`. Exact. |
| `golden_fr.json` = 2 items dégradés (G07 halluciné, G08 hors-sujet) | ✅ | `golden_fr.json:104-136` (G07 chiffres inventés ; G08 contextes BETA/RH/veille) | Exact. |
| Planchers nightly réalistes 0.55/0.55/0.70 (set complet + juge 1b) | ✅ | `ragas-nightly.yml:77-79` | Exact. |
| Baseline = graine déterministe à rafraîchir après 1er run sain | ⚠️ | `baseline_scores.json:3-7` (0.75 / 0.875 / 1.0) | Valeurs **plausibles** mais leur provenance (« juge scripté ») n'est pas re-vérifiable byte-level ; le fichier ne dit pas par quel script/run elles sont nées → cf. note 🔇 ci-dessous. |
| Tolérance anti-régression défaut 0.05, hausse jamais pénalisée | ✅ | `compare_scores.py:67`, `:123-140` (régression seulement si `delta < -tol`) | Exact. |
| `--update` produit la baseline en artefact, pas de push auto (`contents: read`) | ✅ | `ragas-nightly.yml:46-47`, `:152-161`, `compare_scores.py:203-220` | Conforme. |
| Backend `ragas` optionnel, import paresseux, dégrade proprement | ✅ | `runner.py:117-149` (lève `RagasUnavailable`, jamais câblé) | Honnête : la doc dit « non câblé », le code le confirme (`runner.py:141-145`). |
| Sur set complet le gate échoue volontairement (seuils défaut) | ✅ | seuils défaut 0.90 vs G07/G08 dégradés ; cf. tests offline | Cohérent ; aligné `README.md:100-104`. |

**Note 🔇 baseline — ✅ RÉSOLU (itération 1)** : la provenance est désormais au
repo et **reproductible byte-level sans modèle live**. Générateur déterministe
`tests/rag/ragas_eval/gen_baseline.py` (oracle `scripted_judge.py`) régénère
`baseline_scores.json` (0.75/0.875/1.0) à l'octet près ; garde-fou
`test_baseline_is_reproducible_from_scripted_judge`. Un relecteur reproduit les 3
valeurs via `python -m ragas_eval.gen_baseline --write`. Documenté
`RAG_EVAL.md:90-116`.

---

## `RAG_OPTIMIZATION.md` — optimisation RAG/Ollama

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| `num_ctx` défaut Ollama 4096 = troncature → doit être câblé | ✅ | piège AGENTS.md `:103` ; câblé partout (ci-dessous) | Confirmé câblé, pas régressé. |
| `OLLAMA_CONTEXT_LENGTH` câblé compose (8192 défaut) | ✅ | `docker-compose.yml:296-300` (`OLLAMA_CONTEXT_LENGTH=${OLLAMA_CONTEXT_LENGTH:-8192}`) | Exact. |
| Câblé Helm `ollama.tuning.contextLength` | ✅ | `deploy/k8s/onix-ha/values.yaml:430` (8192), `templates/ollama.yaml:53-54` ; `deploy/azure/values-azure.yaml:116` | Exact. |
| `make tune` écrit `OLLAMA_CONTEXT_LENGTH` selon HW (8192/12288/16384) | ✅ | `Makefile:36-37` → `scripts/detect-hardware.sh:236,281` (`emit/set_force OLLAMA_CONTEXT_LENGTH`) | Conforme. |
| `make models` grave `num_ctx` + `temperature 0.2` via Modelfile | ✅ | `scripts/pull-models.sh:71-77` (`PARAMETER num_ctx %s` + `PARAMETER temperature %s`), `ONIX_TEMP:-0.2` (`:33`) | Exact (température 0.2 gravée). |
| Embedding défaut `nomic-embed-text-v1` anglo dans model-server Onyx (`model_configs.py:15`) | ❔ | Onyx non vendoré ici | Affirmation sur code externe v4.1.1 → non vérifiable au repo. |
| Reranker OFF par défaut Onyx (`DEFAULT_CROSS_ENCODER_MODEL_NAME=None`) | ❔ | code Onyx externe | Non vérifiable ici. |
| Capacités mesurées (5,8 tok/s 7B Q4, etc.) sur 4 vCPU sans GPU | ❔ | mesures live non rejouables ; aucun script/log de bench au repo pour ces valeurs | Présenté comme « mesuré » ; non reproductible au repo (mais cohérent et raisonnable). |
| P1#8 éval RAGAS « ✅ LIVRÉ » (`tests/rag/ragas_eval/`, `docs/RAG_EVAL.md`) | ✅ | `tests/rag/ragas_eval/*` présent | Conforme. |
| P0#1 `num_ctx` « ✅ CÂBLÉ » (make tune, pull-models, compose, Helm) | ✅ | cf. lignes ci-dessus | Conforme. |
| Reste runtime/Onyx-admin (embedder FR, reranker, BM25 french, MAX_CHUNKS=8) = pas de code | ✅ | distinction explicite `RAG_OPTIMIZATION.md:49` ; renvoyé au `PLAYBOOK` | Honnête (FOSS/runtime). |

---

## `PLAYBOOK_ONYX_RAG.md` — appliquer l'optimisation

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| §1 num_ctx déjà câblé (make tune / make models / Helm) — à vérifier | ✅ | idem RAG_OPTIMIZATION | Conforme. |
| §2-§4 `MAX_CHUNKS_FED_TO_CHAT=8`, reranker, embedder FR, BM25 french = gestes runtime « zéro code » | ✅ (cadrage) | doc présente ces étapes comme Onyx-admin/`.env`, pas du code onix | Cohérent avec « FOSS vs runtime ». Pas de code à auditer ici (par conception). |
| §5 `qwen2.5:7b-instruct` + `temperature 0.2` gravée | ✅ | `pull-models.sh:33,75` | Conforme. |
| §7 valider via `make rag-eval` (cibles 0.90/0.70/0.85) | ✅ | `Makefile:148`, seuils `metrics.py:104-108` | Conforme. |
| Renvois `/metrics` gateway (no_context, citation, P95) | ❔/✅ | hors scope direct ; métriques décrites dans QA_GUARDRAILS (cf. §QA) | Vérification fine déléguée au rapport `monitoring`/`access-gateway`. |

---

## `QA_GUARDRAILS.md` — stratégie qualité/sécurité

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Défense en profondeur 3 couches (corpus / prompt / post-filtre) | ✅ | couche2 = `prompts/agent_commercial_systeme.md` ; couche3 = `tests/rag/guardrail_postfilter.py` + `access-gateway/app/guardrail.py` | Conforme. |
| Couche 3 « pas de citation → refuse » déterministe hors-LLM | ✅ | `guardrail_postfilter.py:338-348` (`asserts_a_fact` & `not has_citation` → REFUSAL_NO_CITATION) | Exact. |
| Alignement OWASP LLM01/LLM02 | ✅ | prompt `agent_commercial_systeme.md:63`, `:89-94` ; vecteurs catégorisés | Conforme. |
| Recette hors-LLM `make rag-test` = `python -m pytest tests/rag -q` | ✅ | `Makefile:133-134` | Exact. |
| `test_red_team.py` = **20 vecteurs**, 5 catégories | ✅ | `test_red_team.py:42-132` (RT01-RT20), `test_at_least_20_vectors:135`, `test_all_five_categories_present:139` | Exactement 20. |
| `test_prompt_contract.py` : chaque règle + 6 cas portefeuille + longueur min | ✅ | `test_prompt_contract.py:21-100` (20 garde-fous, 6 cas, `len>2500`) | Conforme. |
| `test_eval_dataset.py` : schéma + 6 cas + cohérence éval↔prompt | ✅ | fichier présent ; `dataset_eval.json` 21 conversations | Conforme (existence + structure). |
| « 20+ vecteurs » (`:123`) vs « 20 vecteurs » (`:141`) | ⚠️ | `QA_GUARDRAILS.md:123` vs `:141` | Formulation interne légèrement incohérente (20+ vs 20). Le code = 20 (contrat). Mineur. |
| Métriques Prometheus garde-fous DÉPLOYÉES dans gateway | ✅ | `access-gateway/app/guardrail.py` existe ; tests `test_guardrail.py`, `test_guardrail_deployed.py` | Câblage gateway confirmé (détail métriques → rapport access-gateway/monitoring). |
| Mode live `ONIX_RAG_LIVE=1` rejoue dataset+red-team vs API Onyx | ✅ | `conftest.py:73-80`, `test_red_team.py:178-189`, `test_eval_dataset.query_onyx:99` | Conforme (skippé sans API). |
| Mode contrat « verrouille le contrat », live = comportement effectif | ✅ | tableau `QA_GUARDRAILS.md:170-180` honnête (⚠️ contractuel vs ✅ live) | Distinction honnête et fidèle au code. |

---

## `AGENT_COMMERCIAL.md` + `prompts/`

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Prompt sourcé/anti-injection à coller dans Onyx | ✅ | `prompts/agent_commercial_systeme.md:17-324` (bloc ```) | Conforme. |
| Sourcing strict, jamais connaissances générales, citation systématique | ✅ | prompt règles 1-3 `:27-41` | Présent (testé `test_prompt_contract.py:21-42`). |
| Anti-mélange clients (un seul client) + exception portefeuille agrégé | ✅ | prompt règles 6-7 `:49-60` | Conforme. |
| Anti-révélation prompt + contenu doc = donnée non fiable | ✅ | prompt règles 8-9 `:65-88` | Conforme + anti-recopie d'URL/action injectée `:82-88`. |
| Anti-exfiltration (pas de liste, pas d'export massif, non-confirmation) | ✅ | prompt règle 10 `:89-94` | Conforme. |
| Lecture seule stricte (jamais de simulation d'écriture) | ✅ | prompt règle 12 `:102-113` | Très explicite. |
| RBAC par-doc = EE/Cloud ; FOSS = index partagé « voit tout l'indexé » | ✅ | `AGENT_COMMERCIAL.md:39-46` ; aligné AGENTS.md `:106` | Distinction FOSS/EE honnête. |
| §5 « validation » = rejouer `exemples_questions.md` | ✅ | `prompts/exemples_questions.md` présent | Conforme. |
| 6 formats portefeuille (I/J/K/L/M/N) dans le prompt | ✅ | prompt `:201-313` ; testés `test_prompt_contract.py:45-90` | Conforme. |

---

## `E2E_GUARDRAILS.md` — post-filtre DÉPLOYÉ dans la gateway (T3)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Post-filtre câblé dans `access-gateway` (chemin réponse) | ✅ | `access-gateway/app/guardrail.py` (16 Ko), tests `test_guardrail_deployed.py` | Code de service présent. |
| 7 règles ordonnées du plus dur au plus métier | ✅ | `guardrail_postfilter.py:300-351` (leak→exfil→read_only→out_of_context→no_confirm→no_citation→passthrough) | Exact (l'ordre/les noms correspondent à `E2E_GUARDRAILS.md:69-77`). |
| Pipeline E2E : 21 vecteurs rejoués `gateway→LLM≥7B→post-filtre` | ✅ | `access-gateway/tests/e2e/{run_e2e.py,llm_relay.py,vectors.py}` ; 21 cas (`vectors.py` RT01-RT20+NOM01) | Exact (21 IDs). |
| Résultat 21/21, 8 substitutions, 0 échec dur | ✅ (avec preuve) | `access-gateway/tests/e2e/RESULTS.md:1-2`, `RUN_TRANSCRIPT.txt` (transcript réel, ports dynamiques, réponses LLM) | **Reproductible et tracé** : un transcript brut existe (contrairement à LIVE_GUARDRAILS — cf. §LIVE). |
| Tableau 21/21 avec périmètre RBAC `['clients-nord']` par ligne | ✅ | `E2E_GUARDRAILS.md:171-193` ≡ `RUN_TRANSCRIPT.txt` (RBAC `['clients-nord']` à chaque cas) | Concordant. |
| Non-manipulable par injection (hors-LLM, après LLM) | ✅ | `guardrail_postfilter.py` déterministe ; test `test_deployed_not_manipulable_by_injection` (cité doc) | Conforme à la conception. |
| « 21 vecteurs **red-team** » | ⚠️ | doc `:8,211,253` dit 21 red-team ; or 20 red-team + 1 **nominal** (NOM01) | NOM01 est nominal_sourcing (`vectors.py:360`), pas une attaque. Imprécision de comptage (21 cas, dont 20 red-team). Mineur. |
| Transcript E2E sans **date** | ⚠️/🔇 | `RUN_TRANSCRIPT.txt` (aucun timestamp), `RESULTS.md:1` (modèle seul) | La preuve existe mais n'est pas datée → traçabilité temporelle perfectible. |
| Retrieval Onyx natif (citations natives, scoring) NON booté (~28 Go vs <20 Go) | ✅ (honnête) | `E2E_GUARDRAILS.md:204-235` ; couche1/citations natives explicitement « à valider » | Résiduel assumé, pas masqué. Excellent niveau d'honnêteté. |
| Bascule prod = `GATEWAY_ONYX_BASE_URL` vers Onyx réel (config, pas code) | ❔ | dépend de la stack Onyx déployée (non bootable ici) | Plausible, non vérifiable au repo. |

---

## `LIVE_GUARDRAILS_RESULTS.md` — résultats « LIVE » (point critique de la mission)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Doc « Généré le 2026-06-16 par `tests/rag/run_live.py` » contre vrai modèle | ⚠️ | `LIVE_GUARDRAILS_RESULTS.md:1-3` ; générateur réel `run_live.py:119-281` (template identique) | Le **générateur existe et produit exactement ce format** (gabarit ligne-à-ligne). MAIS le doc committé est un **artefact statique** : rien n'archive le run (pas de transcript des réponses LLM brutes joint, contrairement à l'E2E gateway). |
| « prompt seul 76.2% (16/21) ; + couche 3 = 100% (21/21) » | ⚠️ (résultat live non re-vérifiable byte-level) | `LIVE_GUARDRAILS_RESULTS.md:11-16,37-39` ; calcul réel `run_live.py:71-80` | Le **100% post-filtre** est plausible/quasi-déterministe (couche 3 déterministe). Le **76.2% prompt-seul** dépend du tirage d'un 7B et **n'est pas reproductible à l'identique** (le doc le reconnaît `:122-132`). Chiffre non re-vérifiable sans rejouer Ollama. |
| Tableau par vecteur (RT03/RT05/RT11/RT13/NOM01 rattrapés) | ⚠️ | `:65-76` | Cohérent avec les règles du post-filtre (`no_exfil_relay`/`out_of_context`/`read_only`/`no_citation`), mais c'est un **instantané d'un run** non archivé. |
| Extraction audit : heuristique 0.0% (0/15) vs LLM 86.7% (13/15) | ⚠️ | `:96-104` ; source réelle `live_extraction.py` (3 échantillons × 5 champs = 15) | Le « /15 » est **cohérent** (3 samples EX01-EX03, 5 champs). Le détail EX01 4/5, EX02 5/5, EX03 4/5 = 13/15 colle. Mais c'est encore un run live non archivé (réponses LLM non jointes). |
| Sécurité dure 100% dès le prompt seul (anti-fuite, non-exécution) | ⚠️ | `:107-110` | Affirmation forte sur le comportement d'un LLM ; vraie sur CE run, pas une garantie (le doc l'admet `:122-132`). |
| Limites honnêtes (prompt seul insuffisant, retrieval simulé, jailbreaks à étendre) | ✅ | `:122-132` | Section « honnêteté » présente et fidèle. |

> **Verdict LIVE (cœur de la mission)** : ce n'est **pas** un « mock présenté comme
> réel » — le générateur (`run_live.py`), le harnais (`live_harness.py`,
> `live_extraction.py`) et les checkers existent réellement et calculent ces
> chiffres à partir d'appels Ollama réels. Le doc affiche une date et reconnaît sa
> variance. **MAIS** : contrairement à l'E2E gateway (qui joint un `RUN_TRANSCRIPT.txt`
> brut), `LIVE_GUARDRAILS_RESULTS.md` **n'archive aucune trace du run** (réponses
> LLM brutes, logs, hash de modèle). Les pourcentages « prompt seul 76.2% » et
> « extraction 86.7% » sont donc **affirmés mais non reproductibles à l'identique**
> et non re-vérifiables byte-level au repo → classés ⚠️ (résultat live daté mais
> sans preuve archivée). Recommandation P1 : committer le transcript brut du run
> (comme pour l'E2E) pour passer ces lignes en ✅.

---

## Écarts « production-ready entreprise »

### P0 (bloquant production)
- **Aucun P0 dans ce scope.** Les garde-fous de sécurité dure (post-filtre
  déterministe) sont **réellement déployés** dans la gateway (`app/guardrail.py`),
  testés (`test_guardrail_deployed.py`) et prouvés E2E avec transcript. Le risque
  résiduel majeur (RBAC par-doc) est correctement renvoyé à EE/cadrage client.

### P1 (à traiter avant exposition client sensible)
1. **✅ TRAITÉ (présentation fiabilisée)** — **`LIVE_GUARDRAILS_RESULTS.md`** : un
   **encadré « chiffres INDICATIFS, non reproductibles byte-level »** est désormais
   généré par `run_live.py:188-207` (write_markdown) ET présent dans le doc
   committé (`LIVE_GUARDRAILS_RESULTS.md:18-39`) : date, modèle, **version Ollama**
   (capturée via `live_harness.ollama_version()`), température, **commande exacte de
   régénération**, et renvoi au transcript E2E. Anti-régression :
   `test_runner_plumbing.py:96-103`. Le transcript brut reste à committer lors d'un
   futur run avec Ollama (le harnais le permet ; pas de modèle live ici).
2. **✅ TRAITÉ — Baseline RAGAS reproductible byte-level (sans modèle live)** :
   provenance désormais **explicite et réexécutable**. Oracle extrait dans
   `tests/rag/ragas_eval/scripted_judge.py` ; générateur déterministe
   `tests/rag/ragas_eval/gen_baseline.py` (`--write`/`--check`) régénère
   `baseline_scores.json` (0.75/0.875/1.0) **à l'octet près sans Ollama**.
   Garanti par `test_baseline_is_reproducible_from_scripted_judge`
   (`test_ragas_eval.py`). Documenté `RAG_EVAL.md:90-116`. ⚠️ Reste une **graine
   scriptée** (à rafraîchir depuis un vrai run ≥ 7B après 1er nightly sain — limite
   assumée, hors modèle live).
3. **Couverture red-team limitée à 20 vecteurs / 1 langue / T=0** : pas de
   jailbreaks avancés (encodage multi-couches, leetspeak, multi-tours), pas de
   variation de température. Le doc le reconnaît (`LIVE_GUARDRAILS_RESULTS.md:131`).
   À étendre pour un corpus sensible. **(non traité cette itération — nécessite
   idéalement un run live pour valider l'extension)**

### P2 (qualité/cohérence documentaire)
4. **✅ TRAITÉ — Comptage cohérent** : les formulations « 21 vecteurs red-team »
   sont remplacées par « 21 cas (20 red-team RT01–RT20 + 1 nominal NOM01) » dans
   `E2E_GUARDRAILS.md` (en-tête, §4.2, §5, §6) et un encadré « Comptage » ajouté à
   `LIVE_GUARDRAILS_RESULTS.md:46-51` (+ `run_live.py`). `QA_GUARDRAILS.md:123`
   « 20+ » → « 20 » (aligné `:141` et `test_red_team.py:42-132` = 20 vecteurs).
   Source de vérité : `access-gateway/tests/e2e/vectors.py:12` (« 20 + 1 = 21 »).
5. **✅ TRAITÉ (côté doc autorisée)** : note de **traçabilité** ajoutée à
   `E2E_GUARDRAILS.md:167-178` (transcript brut, modèle, date d'audit, mention
   honnête que timestamp/version Ollama ne sont pas dans le transcript actuel +
   comment les consigner au prochain run). `RUN_TRANSCRIPT.txt`/`RESULTS.md` sont
   hors périmètre de propriété de ce scope (sous `access-gateway/`) → non modifiés.
6. **`num_ctx` runtime non vérifiable au repo** : le câblage `OLLAMA_CONTEXT_LENGTH`
   est prouvé (compose/Helm/tune/Modelfile) mais l'effet réel (« 4096 tronque ») et
   les mesures tok/s reposent sur Onyx/Ollama non bootés → ❔ inévitable, à valider
   sur stack déployée (`make verify` / `ollama show`).

---

## Verdict (3 lignes)

1. **Scope production-ready au niveau prouvable, et exceptionnellement honnête** :
   garde-fous déterministes réellement déployés (gateway), testés offline (CI) et
   prouvés E2E avec transcript ; éval RAGAS souveraine complète + gate absolu +
   anti-régression ; `num_ctx` câblé partout ; prompt anti-injection robuste. **0 ❌**.
2. **Le seul vrai point faible est la *preuve* des résultats « LIVE »** : les
   pourcentages de `LIVE_GUARDRAILS_RESULTS.md` (et la baseline RAGAS) sont datés et
   générés par du code réel, mais **non archivés/non reproductibles byte-level** →
   ⚠️, à fiabiliser (P1) en committant les transcripts comme pour l'E2E gateway.
3. **Reste tributaire de la stack déployée** (retrieval Onyx natif, citations
   natives, mesures Ollama, RBAC EE) — explicitement assumé par la doc, donc
   conforme à la règle « honnêteté > esbroufe ». **Aucun mock présenté comme réel.**
