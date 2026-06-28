# Scope `actions` — dossier agent

> **Mission** : microservice `onix-actions` (FastAPI) qui porte les **fonctions
> applicatives** (OCR, génération `.docx`, tâches asynchrones, notifications,
> usage/coût, admin/kill-switch) **et** la couche **sécurité/RGPD** qu'Onyx ne
> fournit pas : audit-trail **HMAC chaîné**, redaction **PII**, **DLP** egress,
> **rétention/effacement**.
> **Sous-agent** : backend + RGPD. **État** :
> [`../../ralph/state/actions.md`](../../ralph/state/actions.md).
>
> 👤 **Owner** : Backend + RGPD · 🗓️ **Dernière revue** : 2026-06-18 · 🔁 **Cadence de revue** : 120 j (cf. [registre](scopes.json)).

Routeur : [`README.md`](README.md) · Projet : [`../../AGENTS.md`](../../AGENTS.md).

## 1. Mission & frontière FOSS/EE

| | |
|---|---|
| **Apporte (FOSS)** | OCR/docgen/tasks/notify ; **audit-trail vérifiable** (HMAC chaîné — absent partout dans Onyx) ; redaction PII ; DLP egress ; rétention/effacement RGPD ; comptage tokens **réels** + coûts (FinOps) ; admin/kill-switch ; mode stateless (Postgres/Redis/S3) pour la HA. |
| **Reste EE/absent** | l'audit-trail entreprise d'Onyx est **absent** (FOSS et EE) → c'est `actions` qui le fournit. Cf. [`../audit-onyx/30-security.md`](../audit-onyx/30-security.md). |

## 2. Carte du code — [`../../actions/`](../../actions/)

| Fichier | Rôle |
|---|---|
| [`app/main.py`](../../actions/app/main.py) | **Point d'entrée** FastAPI (endpoints actions + `/metrics`, rate-limit, identité d'appelant). |
| [`app/ocr.py`](../../actions/app/ocr.py) | OCR (pytesseract/pdf2image) sur documents. |
| [`app/audit_engine.py`](../../actions/app/audit_engine.py) | Moteur de comparaison document↔référence (champs canoniques, verdict CONFORME/ECART/INCERTAIN). |
| [`app/fabric_reference.py`](../../actions/app/fabric_reference.py) | **[POC réconciliation AC360]** `fetch_client_reference` : référence client lue dans le **SI Fabric (OneLake)**, fail-closed, lecteur injectable. Alimente l'audit (`POST /audit/reconcile/file`) → verdict d'écarts contrat↔SI. `reconcile_batch` : réconciliation de **portefeuille** (lot de contrats → fiches + synthèse par verdict), fail-closed / lecture seule, exposé par `POST /audit/reconcile/batch` (lot borné à 200, gate `audit`, usage `reconcile_batch_*`). `batch_to_csv` : export tableur des fiches (`?format=csv` → `text/csv`), échappement CSV natif + **anti-injection de formule** (`= + - @` TAB CR neutralisés, BOM Excel). `client_360` : synthèse **Assistant Client 360** (agrège réf SI + tâches ouvertes + volume d'usage par HASH, RGPD-safe, lecture seule, sources injectables), exposée par `POST /client/360` (gate `audit`, usage `client_360_viewed`, fail-closed clé vide → 400). `portfolio_360` : tableau de bord **360 de portefeuille** (résumé slim par client + totaux, dédoublonné, traitement borné 500 avec **troncature signalée** `totaux.nb_demandes`/`tronque` — jamais muette, data-minimisé) + `portfolio_360_to_csv` (export, anti-injection), exposés par `POST /portfolio/360` (gate `audit`, `?format=csv` BOM Excel + en-têtes `X-Portfolio-Requested`/`X-Portfolio-Truncated`, usage `portfolio_360_viewed`). |
| [`app/guardrail_core.py`](../../actions/app/guardrail_core.py) | **Home production du garde-fou déterministe** (post-filtre OWASP LLM01 « couche 3 ») : constantes de refus (`REFUSAL_*`), dataclass `FilterResult`, tous les détecteurs (`has_citation`, `claims_write_action`, `leaks_prompt_or_persona`, `relays_exfil_link`, `asserts_a_fact`, …), fonction `post_filter`. 100 % hors-LLM, stateless, stdlib-only. **Source unique** partagée avec les tests RAG (ré-exportée depuis `tests/rag/guardrail_postfilter.py`). |
| [`app/agentic_local.py`](../../actions/app/agentic_local.py) | **Couche agentique souveraine** (tool-calling natif Ollama `/api/chat`, contourne le mur #12) : `run_agent` STATELESS borné, `REGISTRY` d'outils **lecture-seule** whitelistés (client_360, portfolio_360, reconcile_batch, rag_ask, list_tasks, audit ; compartiment write vide), défense prompt-injection 6 couches (whitelist, scan L2 `_scan_injection`, garde-fou L3 `guardrail_core`, confinement egress, audit), gate+audit par appel. Exposé par `POST /agent/ask` (gate `agent`, fail-closed). |
| [`app/rag_local.py`](../../actions/app/rag_local.py) | **RAG non-agentique souverain** (`retrieve`→`generate`, contourne le mur #12 Onyx 4.1.1↔Ollama) : `answer` + générateur Ollama par défaut, injectable. Timeout Ollama tunable `ONIX_OLLAMA_TIMEOUT` (défaut 120 s — le 1er appel recharge le modèle à froid). Endpoint `POST /rag/ask` ; fail-closed (aucune source ⇒ refus). Validé LIVE (gemma4, run réel) : récupération accent-folded + génération grounded/sourcée + fail-closed. |
| [`app/rag_local.py`](../../actions/app/rag_local.py) | **RAG non-agentique** souverain (`retrieve`→`build_rag_prompt`→`answer`) : récupération **insensible aux accents** (repli NFKD `_fold`, robuste FR/OCR) + génération locale Ollama, lecteur/générateur injectables, fail-closed. Contourne la boucle agentique Onyx 4.1.1 cassée avec les modèles locaux (#12). |
| [`app/docgen.py`](../../actions/app/docgen.py) | Génération `.docx` (python-docx). |
| [`app/tasks.py`](../../actions/app/tasks.py) · [`app/celery_app.py`](../../actions/app/celery_app.py) | Tâches (synchrones + file Celery opt-in). |
| [`app/notify.py`](../../actions/app/notify.py) | Notifications. |
| [`app/audit_log.py`](../../actions/app/audit_log.py) · [`app/audit_engine.py`](../../actions/app/audit_engine.py) | **Audit-trail HMAC chaîné** (intégrité vérifiable). |
| [`app/security.py`](../../actions/app/security.py) · [`app/caller_identity.py`](../../actions/app/caller_identity.py) | Auth d'appelant (JWT OIDC), durcissement. |
| [`app/dlp.py`](../../actions/app/dlp.py) | **DLP** egress (filtrage des sorties sensibles). |
| [`app/retention.py`](../../actions/app/retention.py) | Rétention / effacement (RGPD). |
| [`app/usage_tracker.py`](../../actions/app/usage_tracker.py) · [`app/cost_tracker.py`](../../actions/app/cost_tracker.py) | Usage + **coûts** (tokens réels). |
| [`app/llm.py`](../../actions/app/llm.py) | Accès LLM (Ollama local). |
| [`app/admin_state.py`](../../actions/app/admin_state.py) | Admin / **kill-switch**. |
| [`app/db.py`](../../actions/app/db.py) · [`app/objstore.py`](../../actions/app/objstore.py) | Persistance opt-in (Postgres) + stockage objet (S3/MinIO) — mode stateless HA. |
| [`app/safe_logger.py`](../../actions/app/safe_logger.py) | Logs sans fuite (PII/secrets). |
| [`tests/`](../../actions/tests/) | Suite **offline** (audit HMAC, PII, DLP, rétention, FinOps…). |

## 3. Commandes

```bash
pytest actions/tests                 # suite offline
make bandit                          # bandit sur actions/ (0 medium+)
pip-audit --requirement actions/requirements.txt --strict
```

## 4. Tests & preuves

`pytest actions/tests` — couvre l'intégrité de l'audit HMAC (chaînage), la redaction
PII, le DLP egress, la rétention/effacement, le comptage de coûts. Offline.

## 5. Invariants & pièges

- **Audit-trail = intégrité** : le chaînage HMAC ne doit jamais être cassé (toute
  écriture chaîne le hash précédent). Secret HMAC en env, **jamais** en repo.
- **[M1] Audit anti-downgrade fail-closed** : `verify_chain()` impose l'algo selon
  la présence d'une clé (clé ⇒ chaîne 100 % `hmac-sha256`), **jamais** selon l'algo
  stocké par ligne. Une ligne `sha256` quand la clé est présente = downgrade keyless
  (recalculable sans la clé) ⇒ rupture (`audit_log.py:187-207`). Migration
  keyless→HMAC : repartir d'une base d'audit vierge (mélange = downgrade refusé).
- **[HARD-03] Préflight clé d'audit** : `audit_log.preflight_audit_key()` (appelé
  dans `main.py:_lifespan`) **refuse de démarrer** sans `ONIX_ACTIONS_AUDIT_HMAC_KEY`,
  sauf override DEV `ONIX_ACTIONS_AUDIT_KEY_OPTIONAL=true`. Sans clé, le journal est
  forgeable (cf. M1) — fail-closed en prod, jamais « inviolable » mensonger.
- **PII/DLP fail-safe** : en cas de doute, **rédiger/bloquer** plutôt que laisser fuir.
- **Stateless opt-in** : imports paresseux (`db.py`/`objstore.py`) — le mode mono-poste
  SQLite ne doit **pas** exiger Postgres/S3. Ne pas régresser.
- **stdlib-first** : pas de dépendance lourde sans raison (cf. `requirements.txt` épinglé).

> 🔒 **Sécurité (scope)** : applique [`SECURITY.md`](../../SECURITY.md) + le scope gardien
> [`security-governance`](security-governance.md) ; **fail-closed**, audit HMAC intègre,
> PII/DLP fail-safe, zéro secret loggé ; gates `make bandit gitleaks pip-audit trivy` **verts**.

## 6. Observabilité

`/metrics` Prometheus (instrumentation HTTP + FinOps + kill-switch). Dashboard
[`../../monitoring/grafana/dashboards/onix-actions.json`](../../monitoring/grafana/dashboards/onix-actions.json).
Détail : [`../OBSERVABILITY.md`](../OBSERVABILITY.md).

## 7. Docs de fond

[`../ACTIONS.md`](../ACTIONS.md) · [`../FINOPS.md`](../FINOPS.md) ·
[`../SECURITY_RGPD_ACTIONS.md`](../SECURITY_RGPD_ACTIONS.md) ·
[`../STATELESS_ACTIONS.md`](../STATELESS_ACTIONS.md) · [`../RGPD.md`](../RGPD.md).

## 8. Audit & journal

[`../audit-reality/actions.md`](../audit-reality/actions.md) ·
[`../../ralph/state/actions.md`](../../ralph/state/actions.md) ·
[`../../ralph/scopes/actions.md`](../../ralph/scopes/actions.md).

## 9. Sous-agent

| | |
|---|---|
| Discipline | Backend + RGPD |
| Skills | `/security-review`, `/code-review`, `/verify` |
| MCP | `Context7` (fastapi, pydantic, python-docx, pytesseract) ; `github` |
| Cibles de preuve | `pytest actions/tests`, audit HMAC, PII, DLP |

## 10. Maintenir cette fiche

Touche au code `actions/` ⇒ mets à jour §2 + §4, reporte la preuve dans
[`../audit-reality/actions.md`](../audit-reality/actions.md) et le journal.
Vérifie : `make docs-check`.
