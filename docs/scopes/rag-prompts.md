# Scope `rag-prompts` — dossier agent

> **Mission** : **qualité et sûreté du RAG** — garde-fous red-team, post-filtre
> déterministe sur la réponse, éval **RAGAS** souveraine (juge Ollama local) avec
> gate anti-régression, et le **prompt système** de l'agent commercial (sourcé,
> anti-injection).
> **Sous-agent** : ML/RAG + prompt-engineering. **État** :
> [`../../ralph/state/rag-prompts.md`](../../ralph/state/rag-prompts.md).
>
> 👤 **Owner** : ML/RAG + prompt-engineering · 🗓️ **Dernière revue** : 2026-06-18 · 🔁 **Cadence de revue** : 120 j (cf. [registre](scopes.json)).

Routeur : [`README.md`](README.md) · Projet : [`../../AGENTS.md`](../../AGENTS.md).

## 1. Mission & frontière FOSS/EE

| | |
|---|---|
| **Apporte (FOSS)** | jeux de tests red-team + post-filtre déterministe (réponses sourcées, anti-fuite, anti-injection) ; pipeline d'éval RAGAS local (zéro cloud) + gate anti-régression ; prompt agent commercial versionné. |
| **Contexte Onyx** | la qualité RAG dépend du **réglage Onyx/Ollama** (`num_ctx`, embedding FR, reranker) — réglages câblés côté `deploy-ops`/`monitoring`. Ici on **mesure** et on **garde** la qualité. |

## 2. Carte du code — [`../../tests/rag/`](../../tests/rag/) · [`../../prompts/`](../../prompts/)

| Fichier | Rôle |
|---|---|
| [`../../prompts/agent_commercial_systeme.md`](../../prompts/agent_commercial_systeme.md) | **Prompt système** de l'agent « Commercial 360 » (sourcé, anti-injection). |
| [`../../prompts/exemples_questions.md`](../../prompts/exemples_questions.md) | Banque de questions d'exemple. |
| [`../../tests/rag/test_red_team.py`](../../tests/rag/test_red_team.py) | Vecteurs **red-team** (injection, fuite de prompt, exfiltration). |
| [`../../tests/rag/guardrail_postfilter.py`](../../tests/rag/guardrail_postfilter.py) | **Ré-export** du garde-fou déterministe — la source unique est désormais `actions/app/guardrail_core.py` (couche production) ; conservé ici pour la compatibilité des imports existants. |
| [`../../tests/rag/test_postfilter.py`](../../tests/rag/test_postfilter.py) | Tests du post-filtre déterministe (24 cas, hors-LLM). |
| [`../../tests/rag/test_prompt_contract.py`](../../tests/rag/test_prompt_contract.py) | Contrat du prompt (invariants). |
| [`../../tests/rag/test_eval_dataset.py`](../../tests/rag/test_eval_dataset.py) | Validité du dataset d'éval. |
| [`../../tests/rag/ragas_eval/`](../../tests/rag/ragas_eval/) | Pipeline **RAGAS** : `runner.py`, `judge.py`/`scripted_judge.py`, `metrics.py`, `compare_scores.py`, `gen_baseline.py`. |
| [`../../tests/rag/run_live.py`](../../tests/rag/run_live.py) · [`live_harness.py`](../../tests/rag/live_harness.py) · [`test_live_ollama.py`](../../tests/rag/test_live_ollama.py) | Harnais **LIVE** (Ollama réel). |

## 3. Commandes

```bash
make rag-deps                        # dépendances de test (pytest, PyYAML, requests)
pytest tests/rag                     # suite offline (red-team, post-filtre, contrat)
make rag-eval                        # éval RAGAS LIVE (juge Ollama local)
make rag-eval-ci                     # + gate anti-régression (compare au baseline)
```

## 4. Tests & preuves

- **Offline** : `pytest tests/rag` — red-team (l'attaque est refusée/neutralisée),
  post-filtre déterministe, contrat de prompt, validité dataset.
- **LIVE** : `make rag-eval` / `-ci` — scores RAGAS vs baseline (anti-régression).
  Preuves garde-fous : [`../E2E_GUARDRAILS.md`](../E2E_GUARDRAILS.md) ·
  [`../LIVE_GUARDRAILS_RESULTS.md`](../LIVE_GUARDRAILS_RESULTS.md).

## 5. Invariants & pièges

- **Post-filtre déterministe** : pas de dépendance à l'aléatoire du LLM pour la
  décision de blocage (sinon non reproductible). Garde DUR incrémental côté streaming.
- **Anti-injection** : le prompt système doit rester **sourcé** ; toute consigne
  utilisateur qui tente de l'écraser est neutralisée (couverte par red-team).
- **Souveraineté** : éval **100 % locale** (juge Ollama) — aucun appel cloud.
- **Embedding déterministe** : un embedding non déterministe casse le cache sémantique
  et l'éval — ne pas changer de modèle sans rejouer le baseline.

> 🔒 **Sécurité (scope)** : applique [`SECURITY.md`](../../SECURITY.md) + le scope gardien
> [`security-governance`](security-governance.md) ; **anti-injection** prouvé (red-team),
> post-filtre déterministe, souveraineté (zéro cloud) ; gates `make bandit gitleaks pip-audit` **verts**.

## 6. Observabilité

Qualité suivie par l'éval RAGAS (scores vs baseline) ; les métriques runtime du chat
passent par la passerelle (`/metrics`). Cf. [`../RAG_EVAL.md`](../RAG_EVAL.md).

## 7. Docs de fond

[`../RAG_OPTIMIZATION.md`](../RAG_OPTIMIZATION.md) ·
[`../PLAYBOOK_ONYX_RAG.md`](../PLAYBOOK_ONYX_RAG.md) · [`../RAG_EVAL.md`](../RAG_EVAL.md) ·
[`../QA_GUARDRAILS.md`](../QA_GUARDRAILS.md) · [`../AGENT_COMMERCIAL.md`](../AGENT_COMMERCIAL.md) ·
[`../PERFORMANCE.md`](../PERFORMANCE.md) ·
[**#12 Onyx 4.1.1 ↔ Ollama (tool-calling) → RAG non-agentique**](../audit-onyx/41-onyx411-ollama-toolcalling.md).

## 8. Audit & journal

[`../audit-reality/rag-prompts.md`](../audit-reality/rag-prompts.md) ·
[`../../ralph/state/rag-prompts.md`](../../ralph/state/rag-prompts.md) ·
[`../../ralph/scopes/rag-prompts.md`](../../ralph/scopes/rag-prompts.md).

## 9. Sous-agent

| | |
|---|---|
| Discipline | ML/RAG + prompt-engineering |
| Skills | `/code-review`, `/verify`, `claude-api` |
| MCP | `Context7` (ragas, ollama) ; `github` |
| Cibles de preuve | `pytest tests/rag`, `make rag-eval`, anti-injection |

## 10. Maintenir cette fiche

Touche aux prompts/garde-fous/éval ⇒ mets à jour §2 + §4, rejoue le baseline RAGAS si
besoin, reporte dans [`../audit-reality/rag-prompts.md`](../audit-reality/rag-prompts.md)
et le journal. Vérifie : `make docs-check`.
