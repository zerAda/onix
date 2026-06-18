# Scope `actions` — dossier agent

> **Mission** : microservice `onix-actions` (FastAPI) qui porte les **fonctions
> applicatives** (OCR, génération `.docx`, tâches asynchrones, notifications,
> usage/coût, admin/kill-switch) **et** la couche **sécurité/RGPD** qu'Onyx ne
> fournit pas : audit-trail **HMAC chaîné**, redaction **PII**, **DLP** egress,
> **rétention/effacement**.
> **Sous-agent** : backend + RGPD. **État** :
> [`../../ralph/state/actions.md`](../../ralph/state/actions.md).

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
- **PII/DLP fail-safe** : en cas de doute, **rédiger/bloquer** plutôt que laisser fuir.
- **Stateless opt-in** : imports paresseux (`db.py`/`objstore.py`) — le mode mono-poste
  SQLite ne doit **pas** exiger Postgres/S3. Ne pas régresser.
- **stdlib-first** : pas de dépendance lourde sans raison (cf. `requirements.txt` épinglé).

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
