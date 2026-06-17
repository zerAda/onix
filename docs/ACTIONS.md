# onix-actions — couche applicative locale

`onix-actions` est un **microservice interne** (FastAPI, `actions/`) qui donne à
onix les fonctionnalités **au-delà du RAG** : audit documentaire avec OCR,
génération de fiches `.docx`, relances/tâches, notifications, suivi d'usage,
FinOps et administration (kill-switch). **100 % local, open-source, gratuit** :
aucun Azure, M365, Graph, Planner ni Teams.

Il tourne sur le réseau Docker interne `onix-net`, **sans aucun port publié sur
l'hôte**. L'assistant Onyx l'appelle via `http://actions:8100` en tant que
**Custom Actions / Tools** (OpenAPI). Toutes les requêtes (hors `/health`) sont
authentifiées par **clé API** (`ONIX_ACTIONS_API_KEY`, en-tête `X-API-Key`) et
**gatées** par l'état d'administration (kill-switch global + flag par fonction +
blocage utilisateur).

---

## 1. Fonctionnalités & endpoints

| # | Endpoint | Rôle | Gate (flag) |
|---|---|---|---|
| 1 | `POST /audit` | Audit d'un document **déjà extrait** (JSON) **ou d'un texte brut** vs un enregistrement de référence. Verdict typé + score. | `audit` (+ `llm` si `use_llm`) |
| 1 | `POST /audit/file` | Audit à partir d'un **fichier** (PDF/image) → **OCR local** → extraction → comparaison. | `audit` + `ocr` |
| 2 | `POST /generate/fiche` | Génère une **fiche/briefing `.docx`**. | `generate` |
| 2 | `GET /download/{id}` | Récupère le `.docx` généré. | `generate` |
| 3 | `POST /tasks` | Crée une **relance/tâche locale** (SQLite). `webhook_url` optionnel pour pousser vers un système externe. | `tasks` |
| 3 | `GET /tasks` | Liste les tâches (filtre `status`). | `tasks` |
| 4 | `POST /notify` | **Notification** générique : webhook JSON (Slack/Mattermost/Teams-compatible) ou SMTP. | `notify` |
| 5 | `POST /usage` | Enregistre un **événement d'usage** (identifiants **hashés SHA-256**). | `usage` |
| 5 | `GET /usage/summary` | Agrégats d'usage (total, par type/statut, coût, tokens). | `usage` |
| 6 | `GET /cost` | **FinOps** : rate card + dépense + état budget. | `cost` |
| 6 | `POST /cost/estimate` | Estime un coût (centre de coût × quantité). | `cost` |
| 7 | `POST /admin/control` | **Kill-switch / flags / blocage utilisateur**. Mute réellement l'état. | (clé admin) |
| 7 | `GET /admin/state` | État effectif de tous les flags + utilisateurs bloqués (hashés). | (clé admin) |
| 8 | `GET /health` | Sonde de santé (+ capacités OCR détectées). | aucune |

### Détails

**Audit (porté du moteur AC360).** Extraction de **champs canoniques**
(`nom_client`, `plafond_hospitalisation`, `date_effet`, `numero_contrat`,
`motif_operation`) par **aliasing** de libellés OCR arbitraires, puis
**normalisation** typée (montant `« 1 000,50 € » → 1000.5` ; date `→ ISO` ; nom
sans accents/casse ; contrat alphanum) et **comparaison** champ par champ avec
statut `MATCH / MISMATCH / UNCERTAIN / MISSING` + confiance, et un **verdict
global** `CONFORME / ECART / INCERTAIN / CLIENT_NON_TROUVE`.

L'**enregistrement de référence** est configurable :
- inline dans la requête (`reference`: objet JSON) ;
- ou fichier monté (`reference_path` sous `/data/reference`, JSON **ou** CSV),
  filtré par `client_key` (nom du client) — voir `actions/reference/clients.example.json`.

**OCR local (`/audit/file`).** PDF texte → `pdfplumber` (repli `pypdf`) ; PDF
scanné → `pdf2image` (poppler) + `pytesseract` ; image → `pytesseract`. Si un
binaire OCR est **absent**, le service **dégrade proprement** : `extraction_mode`
vaut `unavailable`, l'endpoint renvoie `422` avec une raison explicite, et l'on
peut alors passer un texte/JSON déjà extrait ou activer l'assistance LLM.

**« En mieux » — assistance LLM locale.** `use_llm: true` extrait les champs d'un
texte brut via **Ollama** (`http://ollama:11434`), sans cloud. Le parsing de la
réponse est **robuste** (prose, fences markdown, objet imbriqué). En cas
d'indisponibilité d'Ollama, de timeout ou de réponse inexploitable, repli
**propre** sur l'extraction heuristique (jamais bloquant). Le champ
`_extraction_mode` de la réponse (`llm` / `heuristic` / `provided`) indique le
chemin **réellement** utilisé ; il est aussi journalisé (`ONIX_LOG_LEVEL`).

**Sécurité.** Clé API comparée en **temps constant** ; validation stricte des
uploads (extension allowlistée, taille ≤ `ONIX_MAX_UPLOAD_BYTES`, défaut 15 Mo) ;
**anti path-traversal** sur la génération/lecture de fichiers (confinement du
chemin résolu) ; **aucun identifiant en clair** (UPN/utilisateurs/clients hashés
SHA-256) ; aucun secret en dur ; logs sans corps de requête.

---

## 2. Enregistrer les endpoints comme Onyx Custom Actions / Tools

Onyx permet d'attacher à un Assistant des **Actions** décrites par une **spec
OpenAPI**. La spec prête à l'emploi est fournie : **`actions/openapi.json`**
(générée depuis le service, serveur `http://actions:8100`, sécurité `X-API-Key`).

### Procédure (UI Onyx)

1. Démarrer la stack : `make up` (le service `actions` est construit et lancé en
   interne ; aucun port hôte).
2. Dans Onyx : **Admin → Actions** (Custom Tools) → **New / Create Action**.
3. **Importer** le contenu de `actions/openapi.json` (copier-coller le JSON, ou
   héberger le fichier et fournir l'URL).
4. **Authentification** : type *API Key*, en-tête `X-API-Key`, valeur =
   `ONIX_ACTIONS_API_KEY` (cf. votre `.env`, généré par `scripts/gen-secrets.sh`).
   Pour `/admin/*`, ajouter l'en-tête `X-Admin-Key` si `ONIX_ACTIONS_ADMIN_KEY`
   est défini.
5. **Attacher** l'Action à l'Assistant « Assistant Commercial 360 ». L'assistant
   peut alors appeler `audit`, `generate_fiche`, `create_task`, `notify`, etc.

### Exemple minimal de spec (extrait, à titre illustratif)

```json
{
  "openapi": "3.1.0",
  "info": { "title": "onix-actions", "version": "1.0.0" },
  "servers": [{ "url": "http://actions:8100" }],
  "components": {
    "securitySchemes": {
      "ApiKeyAuth": { "type": "apiKey", "in": "header", "name": "X-API-Key" }
    }
  },
  "security": [{ "ApiKeyAuth": [] }],
  "paths": {
    "/audit": {
      "post": {
        "operationId": "audit_endpoint_audit_post",
        "requestBody": {
          "content": { "application/json": { "schema": {
            "type": "object",
            "properties": {
              "document": { "type": "object" },
              "text": { "type": "string" },
              "reference": { "type": "object" },
              "reference_path": { "type": "string" },
              "client_key": { "type": "string" },
              "use_llm": { "type": "boolean" }
            }
          } } }
        },
        "responses": { "200": { "description": "Verdict d'audit" } }
      }
    }
  }
}
```

> La spec **complète et faisant foi** est `actions/openapi.json` — préférez-la à
> cet extrait.

### Exemples d'appel (cURL, depuis le réseau interne)

```bash
# Audit d'un document déjà extrait vs une référence inline
curl -X POST http://actions:8100/audit \
  -H "X-API-Key: $ONIX_ACTIONS_API_KEY" -H "Content-Type: application/json" \
  -d '{"document":{"nom_client":"ACME SAS","plafond_hospitalisation":"5000","date_effet":"01/01/2024","numero_contrat":"CTR-2024-001"},
       "reference":{"nom_client":"ACME SAS","plafond_hospitalisation":"2000","date_effet":"2024-01-01","numero_contrat":"ctr2024001"}}'

# Génération d'une fiche .docx puis téléchargement
curl -X POST http://actions:8100/generate/fiche \
  -H "X-API-Key: $ONIX_ACTIONS_API_KEY" -H "Content-Type: application/json" \
  -d '{"client_name":"ACME SAS","summary":"Contrat standard.","alert_points":"Kbis manquant."}'
curl -OJ http://actions:8100/download/<job_id> -H "X-API-Key: $ONIX_ACTIONS_API_KEY"

# Kill-switch : couper l'audit (puis /audit renvoie 403)
curl -X POST http://actions:8100/admin/control \
  -H "X-API-Key: $ONIX_ACTIONS_API_KEY" -H "Content-Type: application/json" \
  -d '{"action":"disable_feature","scope":"audit","reason":"maintenance"}'
```

---

## 3. Configuration (variables d'environnement)

| Variable | Rôle | Défaut |
|---|---|---|
| `ONIX_ACTIONS_API_KEY` | **Clé API** (en-tête `X-API-Key`). Obligatoire. | — (généré) |
| `ONIX_ACTIONS_ADMIN_KEY` | Clé admin distincte (en-tête `X-Admin-Key`) pour `/admin/*`. | (optionnel) |
| `ONIX_GLOBAL_ENABLED` | Kill-switch global. | `true` |
| `ONIX_<FONCTION>_ENABLED` | Flag par fonction : `AUDIT`, `GENERATE`, `TASKS`, `NOTIFY`, `USAGE`, `COST`, `OCR`, `LLM`. | `true` |
| `ONIX_RATE_CARD` | Rate card FinOps (JSON `{centre: €/unité}`). | vide (0 €) |
| `ONIX_BUDGET_EUR` / `ONIX_BUDGET_WARN_PCT` | Budget + seuil d'alerte (%). | — / `80` |
| `ONIX_OLLAMA_URL` / `ONIX_LLM_MODEL` | Assistance LLM locale. | `http://ollama:11434` / `llama3.2:3b` |
| `ONIX_LLM_CONNECT_TIMEOUT` / `ONIX_LLM_TIMEOUT` | Timeouts LLM (connexion / lecture, s). | `5` / `60` |
| `ONIX_NOTIFY_WEBHOOK` | URL webhook par défaut (Slack/Mattermost/Teams). | vide |
| `ONIX_NOTIFY_TIMEOUT` | Timeout d'envoi webhook (s). | `15` |
| `ONIX_SMTP_HOST`/`PORT`/`USER`/`PASSWORD`/`FROM`/`TO`/`SSL` | Provider email SMTP. | vide |
| `ONIX_SMTP_STARTTLS` / `ONIX_SMTP_TIMEOUT` | STARTTLS (false = relais en clair) / timeout (s). | `true` / `20` |
| `ONIX_LOG_LEVEL` | Niveau de log (trace mode d'extraction + échecs notify). | `INFO` |
| `ONIX_OCR_LANG` | Langues tesseract. | `fra+eng` |
| `ONIX_MAX_UPLOAD_BYTES` | Taille max d'upload. | `15728640` |
| `ONIX_REFERENCE_DIR` | Répertoire des références montées (lecture seule). | `/data/reference` |

L'état runtime (SQLite : usage, tâches, flags admin) et les `.docx` générés sont
persistés dans le volume `actions_data` (`/data`). Les références (JSON/CSV) se
déposent dans `actions/reference/` (monté `:ro` sur `/data/reference`).

---

## 4. AC360 → onix « en mieux »

| Capacité AC360 (cloud) | Implémentation cloud d'origine | onix-actions (local) | « En mieux » |
|---|---|---|---|
| Audit documentaire | Azure Function + **Document Intelligence** (OCR cloud), comparaison vs **Fabric/OneLake** | `POST /audit` + `/audit/file` : **OCR local** (tesseract/poppler), même moteur de comparaison/verdict, référence JSON/CSV | **Zéro transfert** ; OCR & données **sur site** ; **gratuit** ; option LLM locale |
| Génération de fiche | `generate_fiche_rdv` (python-docx) côté Function | `POST /generate/fiche` (même logique, même durcissement anti-traversal) | Autonome, sans dépendance cloud |
| Relances / tâches | **Microsoft Planner** via Graph (OBO) | `POST /tasks` **SQLite local** + `webhook_url` optionnel | Pas de licence M365 ; pousse vers **n'importe quel** outil |
| Notifications | Connecteurs Power Automate / Teams | `POST /notify` : webhook **ou** SMTP, provider configurable | Agnostique (Slack/Mattermost/Teams/email) |
| Suivi d'usage | `usage_tracker` → Application Insights | `POST /usage` + `/usage/summary`, **SQLite/JSONL**, UPN **hashés** | Données d'usage **locales**, pas de télémétrie sortante |
| FinOps | `cost_tracker` (rate card Azure) | `GET /cost` + `/cost/estimate`, rate card **paramétrable** | Centres de coût **local-first** (0 € par défaut) |
| Kill-switch / flags | `admin_controls` + Entra roles | `POST /admin/control` + `/admin/state`, flags **persistés** qui **gatent** réellement | Indépendant d'Entra ; même UX de coupure d'urgence |
| Identité / authz | **Entra ID JWT + OBO** | **Clé API** (réseau interne) | Pas de dépendance IdP cloud pour la brique applicative |

---

## 5. Tests & validation

```bash
cd actions
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
pytest -q          # moteur d'audit, normalisations, gating admin (403), .docx
```

Voir `actions/tests/` : `test_audit_engine.py` (nominal + écarts + normalisations,
repris d'AC360) et `test_api.py` (santé, auth, audit JSON, génération `.docx` non
vide, **kill-switch coupé → 403**, blocage utilisateur, tâches, usage, coût).
