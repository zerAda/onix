# Sécurité applicative & conformité RGPD — `onix-actions` (WS2)

Ce document décrit la couche **sécurité applicative** et **conformité RGPD** du
microservice `onix-actions` (FastAPI, `actions/`). Il vise un service
**défendable face à un audit exigeant** (OWASP ASVS, RGPD art. 5, 17, 30, 32),
**100 % local / souverain** (aucun cloud, aucun transfert).

> Périmètre : la brique applicative `onix-actions` (audit OCR, génération,
> tâches, notification, usage/FinOps, administration). La sécurité de la stack
> Onyx/Ollama (réseau, SSO, secrets d'infra) est traitée dans
> [`SECURITY.md`](SECURITY.md) ; la note RGPD générale dans [`RGPD.md`](RGPD.md).
>
> **Le RBAC fin PAR DOCUMENT** (chaque utilisateur ne voit que ses documents) est
> **hors périmètre WS2** : il relève du *permission sync* du connecteur
> SharePoint et est traité par **WS5**. WS2 fournit l'**authz par appelant**
> (qui appelle le service, quel quota, quel droit admin) — complémentaire, non
> redondant.

---

## 1. Modèle de menace (résumé)

| Menace | Avant WS2 | Mesure WS2 |
|---|---|---|
| Fuite de PII dans les logs / la base | Logs « sans corps » par convention, identifiants hashés | **Redaction PII systématique** (JWT/IBAN/NIR/email) sur tous les logs `onix.actions.*` + champs libres avant persistance |
| Injection de logs (log forging, CWE-117) | — | **Anti-CRLF** : CR/LF et caractères de contrôle échappés |
| Clé partagée = identité floue | Clé API unique (« un appelant autorisé ») | **Identité d'appelant vérifiée** (HMAC par appel ou JWT OIDC) : on sait *qui* appelle |
| Fuite de la clé d'appel ⇒ contrôle admin | Clé admin distincte *optionnelle* | **Clé admin distincte OBLIGATOIRE** (fail-closed) pour `/admin/*` |
| Abus / DoS applicatif | — | **Rate-limiting par appelant** (quota, parité AC360) |
| Altération du journal d'audit | Journal append simple | **Chaînage HMAC tamper-evident** + endpoint de vérification |
| Exfiltration via webhook (egress) / SSRF | URL webhook libre | **DLP egress allowlist** + anti-SSRF + https-only |
| Conservation illimitée (art. 5-1-e) | « définissez une politique » | **Purge par âge (TTL)** configurable |
| Droit à l'effacement (art. 17) | `make destroy` global | **Effacement ciblé par sujet** (hash) |
| Flag mal orthographié ⇒ fonction ouverte | Fail-**open** (comme AC360) | **Fail-closed** sur valeur de flag inconnue |

---

## 2. Redaction PII & journalisation durcie (ASVS V7)

**Module : [`actions/app/safe_logger.py`](../actions/app/safe_logger.py).** Porte la
logique d'AC360 `safe_logger.py`.

- `redact(value)` / `redact_text(text)` : **porte unique** de neutralisation des
  motifs sensibles — **JWT**, **IBAN**, **NIR** (sécu. sociale FR), **e-mails**,
  **clés/Bearer**, **cartes**, **téléphones** — appliquée :
  - à **tous les logs** `onix.actions.*` via un `logging.Filter`
    (`safe_logger.install("onix.actions")`, posé au démarrage) ;
  - aux **champs libres avant persistance** : `reason` (admin), `notes` (tâche),
    `action_name` (usage). Le **contenu d'une fiche `.docx`** n'est PAS redacté :
    c'est le **livrable explicitement demandé** par l'utilisateur, pas un log.
- **Anti-CRLF** (log forging, CWE-117) : `\r`/`\n`/contrôles échappés en littéraux.
- **Irréversible** : remplacement par étiquette de catégorie (`[REDACTED_EMAIL]`…),
  conforme à ASVS V7 (pas de token/PII réversible en log).
- **Fail-safe** : la redaction ne lève jamais (`[REDACTION_ERROR]` en repli).

Conforme : ASVS V7.1 (pas de données sensibles en log), V7.3 (intégrité des logs),
OWASP *Logging Cheat Sheet*.

---

## 3. Authentification & identité d'appelant

**Modules : [`actions/app/security.py`](../actions/app/security.py),
[`actions/app/caller_identity.py`](../actions/app/caller_identity.py).**

### 3.1 Clé de SERVICE (inchangée, durcie)
`ONIX_ACTIONS_API_KEY`, en-tête `X-API-Key` (ou `Authorization: Bearer`),
comparée en **temps constant**. Sans elle, le service refuse tout (503).

### 3.2 Identité d'appelant VÉRIFIÉE (nouveau)
On passe de « *un* appelant autorisé » à « *quel* appelant ». Deux mécanismes,
le plus fort présent gagne :

1. **HMAC par appel** (souverain, sans dépendance) — en-têtes :
   - `X-Onix-Caller` : identité en clair (UPN, service…) ;
   - `X-Onix-Timestamp` : epoch (s), **anti-rejeu** (`ONIX_HMAC_MAX_SKEW`, défaut 300 s) ;
   - `X-Onix-Signature` : `hex(HMAC-SHA256(secret, "caller\ntimestamp\nMETHOD\npath"))`.
   La signature **lie identité + horodatage + requête** : ni rejouable, ni
   transférable à une autre route. Secret : `ONIX_ACTIONS_CALLER_HMAC_SECRET`.
2. **JWT OIDC** (`Authorization: Bearer <jwt>`) : signature (**HS256 natif**, ou
   **RS256/ES256 via PyJWT + JWKS** — ex. Entra ID), `exp`, `iss`, `aud` vérifiés.
   Identité = `preferred_username` / `upn` / `email` / `sub`.

> **L'identité est toujours hashée (SHA-256) avant log/persistance** : on trace
> « qui a fait quoi » sans jamais stocker l'UPN en clair (RGPD : minimisation).

### 3.3 Fail-closed
- `ONIX_ACTIONS_REQUIRE_CALLER_IDENTITY=true` : une identité vérifiée (HMAC/JWT)
  est **exigée** sur les endpoints métier → sinon **401**. Défaut `false`
  (compatibilité clé de service seule) ; **`true` recommandé en multi-utilisateur**.
- Sans mécanisme configuré, l'appelant est étiqueté `service` (la clé API valide
  reste exigée) : choix explicite et documenté, pas une ouverture silencieuse.

### 3.4 Clé ADMIN distincte OBLIGATOIRE
`/admin/*` exige `X-Admin-Key` == `ONIX_ACTIONS_ADMIN_KEY`. **Fail-closed par
défaut** : si la clé admin n'est pas configurée, `/admin/*` renvoie **403**
(une fuite de la clé d'appel ne donne donc pas le kill-switch). Opt-out explicite
de compatibilité : `ONIX_ACTIONS_ADMIN_KEY_OPTIONAL=true` (déconseillé).

### 3.5 Rate-limiting par appelant (parité AC360)
Quota **par identité résolue** (`ONIX_ACTIONS_RATE_LIMIT`, défaut `60/minute`),
appliqué **dans la dépendance d'authentification** (après résolution d'identité ;
un middleware ne verrait que l'IP). Implémentation : **fenêtre glissante en
mémoire**, bornée et déterministe. Clé de quota = **identité vérifiée** si
présente, sinon **IP source** (les appels « clé de service seule » ne partagent
donc pas un unique seau). Dépassement → **429** + `Retry-After`. Mettre à
`0[/unité]`/vide pour désactiver.

- **L'administration (`/admin/*`) N'EST PAS soumise au quota** : une action de
  sécurité (kill-switch, déblocage) ne doit jamais être bloquée en 429 par un pic
  d'abus. Elle reste protégée par la double clé (service + admin).

> ### ⚠️ Limite connue (HA) : quota PAR-PROCESS — effectif `N × réplicas`
>
> L'enforcement réel est la **fenêtre glissante EN MÉMOIRE** d'`enforce_rate_limit`
> ([`security.py:254-282`](../actions/app/security.py)), **locale à chaque process**.
> **`slowapi` est déclaré en dépendance mais N'EST PAS utilisé pour l'enforcement** :
> on l'importe seulement pour le diagnostic et le format de quota (`security.py:34-40`).
> Il n'existe **aucun store partagé** (Redis) à ce stade.
>
> **Conséquence en HA / multi-réplica** : chaque réplica compte indépendamment, donc
> le quota global observé par un appelant unique est **`ONIX_ACTIONS_RATE_LIMIT × nombre de réplicas`**.
> Exemple : `60/minute` avec **4** réplicas derrière le Service K8s ⇒ jusqu'à
> **240 req/min** réellement acceptées (si l'équilibrage répartit l'appelant). Le
> rate-limit reste un garde-fou anti-abus correct **par-instance**, mais **ne
> garantit PAS un plafond global strict** quand le microservice est répliqué.
>
> **Périmètre** : c'est une limite **assumée et honnête**, pas un bug — le défaut
> mono-poste applique exactement `ONIX_ACTIONS_RATE_LIMIT`. **Aucune infra Redis
> n'est livrée ce tour-ci.** *Correctif futur* (backlog) : compteur partagé Redis
> (via `slowapi` + `RedisStorage`, ou un seau atomique `INCR`/`EXPIRE` maison) pour
> obtenir un plafond global réellement strict en HA.

---

## 4. Journalisation d'accès & audit inviolable (ASVS V7)

**Module : [`actions/app/audit_log.py`](../actions/app/audit_log.py).**

### 4.1 Journal d'accès
- `POST /access/log` + helpers `record_document_accessed` /
  `record_rag_search` émettent `document_accessed` / `rag_search_executed`
  (événements d'usage typés). **UPN et identifiants de document hashés** ; la
  **requête RAG n'est jamais stockée en clair** (seule sa longueur l'est).
- `/audit/file` et `/download/{id}` émettent automatiquement `document_accessed`.

### 4.2 Audit admin tamper-evident (chaînage HMAC)
La table `admin_audit` est **append-only** et **chaînée** : chaque entrée porte
`seq`, `prev_hash`, et `entry_hash = HMAC(secret, prev_hash || contenu_canonique)`.
Toute **modification / suppression / réordonnancement** d'une ligne casse la
chaîne en aval → **détectable**.

- Vérification : `GET /admin/audit/verify` → `{ok, count, broken_at?, reason?}`.
- Secret : `ONIX_ACTIONS_AUDIT_HMAC_KEY`. Absent → repli **SHA-256** (toujours
  détecteur d'altération naïve, garantie cryptographique réduite ; un
  avertissement est journalisé). **À définir en production.**

---

## 5. DLP egress (anti-exfiltration / SSRF)

**Module : [`actions/app/dlp.py`](../actions/app/dlp.py).** Appliqué à `/notify`
(webhook) et `/tasks(webhook_url)`.

- **Allowlist** `ONIX_EGRESS_ALLOWLIST` (hôtes/domaines, virgules) ;
  `*.corp.local` autorise les sous-domaines. Hors allowlist → **403**.
- **Fail-closed** : allowlist vide + `ONIX_EGRESS_DEFAULT_DENY=true` (défaut) →
  tout egress refusé (il faut déclarer les destinations).
- **https-only** par défaut ; `http://` toléré seulement si
  `ONIX_EGRESS_ALLOW_HTTP=true` (relais interne assumé).
- **Anti-SSRF** : cibles résolvant vers IP privée/loopback/link-local refusées,
  sauf hôte explicitement allowlisté (`ONIX_EGRESS_ALLOW_PRIVATE_IP` bascule).
- **SMTP** : **STARTTLS exigé par défaut** (cf. `notify.py` : refus d'envoi en
  clair si le serveur ne l'annonce pas, sauf `ONIX_SMTP_STARTTLS=false` assumé).

---

## 6. Rétention & droit à l'effacement (RGPD art. 5-1-e & 17)

**Module : [`actions/app/retention.py`](../actions/app/retention.py).** Réservé admin.

- **Purge par âge (TTL)** — `POST /admin/retention/purge` :
  supprime `usage_events`, tâches **terminées** et `.docx` générés au-delà de
  `ONIX_RETENTION_DAYS` (défaut 365). En mode `ONIX_OBJECT_STORE=s3`, supprime
  **aussi** les objets `jobs/…` périmés du bucket (`objstore.delete_jobs_older_than`)
  → champ `deleted_s3_objects` dans la réponse. Le **journal d'audit** n'est PAS
  purgé par âge (obligation de traçabilité + intégrité de la chaîne).
- **Effacement ciblé par sujet** — `POST /admin/retention/erase` (art. 17) :
  supprime toutes les traces d'un sujet désigné par son **identifiant en clair**
  (hashé ici) **ou** son **hash**. Opère sur les colonnes hashées
  (`user_id_hash`, `client_id_hash`, `owner_hash`) + fichiers `.docx` du sujet.
  En mode S3, supprime **aussi** les `.docx` du sujet dans le bucket
  (`objstore.delete_subject_docx`) → champ `erased_s3_objects` dans la réponse.
  Le journal d'audit chaîné est **préservé** (il ne contient que des hash).
  > Note : le rapprochement des `.docx` se fait sur le nom de fichier sanitisé
  > (best-effort, identique en local et S3) ; pour un effacement exhaustif au-delà
  > du nom, coupler à un index sujet→jobs.

---

## 7. Fail-closed sur flags inconnus

`admin_state._env_flag` retourne désormais **False** (et journalise) sur une
valeur de flag **inconnue** (`ON`, `tru`, typo…). Une coquille de configuration
**coupe** la fonction au lieu de l'ouvrir (inversion du fail-**open** d'AC360).

---

## 8. Secrets (gen-secrets.sh)

`scripts/gen-secrets.sh` génère (bloc `# --- WS2 ---`, idempotent, 48 car.) :
`ONIX_ACTIONS_ADMIN_KEY`, `ONIX_ACTIONS_CALLER_HMAC_SECRET`,
`ONIX_ACTIONS_AUDIT_HMAC_KEY`. `.env` reste **gitignoré** + `chmod 600`.
Le scan **gitleaks** (CI/pre-commit) protège contre un commit accidentel.

En **HA (Helm)**, ces trois clés sont injectées depuis le Secret K8s
(`onix.actionsSecretEnv` → `secretKeyRef`) dans le Deployment `actions` **et** le
worker Celery (cf. [`deploy/k8s/onix-ha`](../deploy/k8s/onix-ha/)). Sans elles :
`/admin/*` répond **403 fail-closed** (kill-switch, `/admin/audit/verify`,
purge/erase inaccessibles) et la chaîne d'audit retombe en **SHA-256**. Les
**valeurs** viennent de `secrets.existingSecret` (prod / Key Vault) ou
`secrets.create` (démo/CI) — **jamais du repo**.

---

## 9. Variables d'environnement (WS2)

| Variable | Rôle | Défaut |
|---|---|---|
| `ONIX_ACTIONS_ADMIN_KEY` | Clé admin distincte (`X-Admin-Key`). | — (généré) |
| `ONIX_ACTIONS_ADMIN_KEY_OPTIONAL` | `true` = clé de service fait admin (compat). | `false` (fail-closed) |
| `ONIX_ACTIONS_CALLER_HMAC_SECRET` | Secret HMAC d'identité d'appelant. | — (généré) |
| `ONIX_HMAC_MAX_SKEW` | Fenêtre anti-rejeu HMAC (s). | `300` |
| `ONIX_ACTIONS_REQUIRE_CALLER_IDENTITY` | Exiger HMAC/JWT (fail-closed). | `false` |
| `ONIX_OIDC_ISSUER` / `_AUDIENCE` / `_JWKS_URL` | Validation JWT OIDC (RS256/ES256). | vide |
| `ONIX_OIDC_HS256_SECRET` | JWT HS256 (sans JWKS). | vide |
| `ONIX_ACTIONS_RATE_LIMIT` | Quota par appelant (`N/minute`). Vide/0 = off. | `60/minute` |
| `ONIX_ACTIONS_AUDIT_HMAC_KEY` | Clé de chaînage du journal d'audit. | — (généré) |
| `ONIX_EGRESS_ALLOWLIST` | Destinations egress autorisées. | vide |
| `ONIX_EGRESS_DEFAULT_DENY` | Refuser tout egress si allowlist vide. | `true` |
| `ONIX_EGRESS_ALLOW_HTTP` | Autoriser `http://` en sortie. | `false` |
| `ONIX_EGRESS_ALLOW_PRIVATE_IP` | Autoriser cible IP privée (anti-SSRF). | `false` |
| `ONIX_RETENTION_DAYS` | TTL de purge par âge (jours). | `365` |

---

## 10. Endpoints ajoutés / modifiés (WS2)

| Endpoint | Auth | Rôle WS2 |
|---|---|---|
| `POST /access/log` | appelant | Journal d'accès (`document_accessed` / `rag_search_executed`), UPN hashés |
| `GET /admin/audit/verify` | admin | Vérifie l'intégrité du journal chaîné |
| `POST /admin/retention/purge` | admin | Purge par âge (TTL) |
| `POST /admin/retention/erase` | admin | Effacement ciblé par sujet (art. 17) |
| *(tous les endpoints métier)* | appelant | Identité vérifiée + quota + redaction + (egress DLP sur notify/tasks) |

---

## 11. Validation

```bash
cd actions
pip install -r requirements.txt pytest bandit
pytest -q                 # 90 tests collectés : 85 passed, 5 skipped (dont test_security_rgpd.py = 32)
bandit -r app             # 0 High / 0 Medium
# gitleaks detect --no-git --config ../.gitleaks.toml   # 0 fuite
```

Tests WS2 : [`actions/tests/test_security_rgpd.py`](../actions/tests/test_security_rgpd.py)
— redaction PII + anti-CRLF, identité HMAC/JWT + anti-rejeu + fail-closed, clé
admin obligatoire, rate-limit 429, DLP allowlist/SSRF/https-only, audit chaîné
(altération détectée), journal d'accès (UPN hashés), purge TTL, effacement par
sujet, fail-closed sur flag inconnu.

---

## 12. Cartographie de conformité

| Exigence | Référence | Mise en œuvre |
|---|---|---|
| Pas de données sensibles en log | ASVS V7.1 ; RGPD art. 5-1-c | `safe_logger.redact` (logs + champs libres) |
| Intégrité des logs | ASVS V7.3.3 | Chaînage HMAC `admin_audit` + `/admin/audit/verify` |
| Journalisation des décisions d'accès | ASVS V7.1 ; RGPD traçabilité | `document_accessed` / `rag_search_executed` (hashés) |
| Authentification forte / anti-rejeu | ASVS V2/V3 | HMAC signé (timestamp) ou JWT OIDC vérifié |
| Moindre privilège (admin séparé) | ASVS V1.4 | Clé admin distincte obligatoire (fail-closed) |
| Disponibilité (anti-abus) | — | Rate-limiting par appelant |
| Contrôle des flux sortants | ASVS V12/SSRF | DLP egress allowlist + anti-SSRF |
| Limitation de conservation | RGPD art. 5-1-e | Purge TTL configurable |
| Droit à l'effacement | RGPD art. 17 | Effacement ciblé par sujet (hash) |
| Mesures de sécurité (chiffrement transit) | RGPD art. 32 | STARTTLS SMTP exigé ; https-only egress |
| Registre des traitements | RGPD art. 30 | [`docs/REGISTRE_TRAITEMENTS.md`](REGISTRE_TRAITEMENTS.md) |
| Analyse d'impact | RGPD art. 35 | [`docs/DPIA_TEMPLATE.md`](DPIA_TEMPLATE.md) |

> Cette page est un appui à la conformité, **pas un avis juridique** : faites
> valider les finalités et catégories de données réelles par votre DPO.
