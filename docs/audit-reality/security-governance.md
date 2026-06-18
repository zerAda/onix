# Audit byte-by-byte — Sécurité / RGPD / Gouvernance / Parité entreprise / audit-onyx

> **Scope** : couche transverse sécurité, RGPD, gouvernance, parité FOSS vs EE, et le
> dossier `docs/audit-onyx/*`. Méthode et légende : [`README.md`](README.md).
> **Posture** : ingénieur·e sécurité/conformité (RGPD) + architecte senior. Honnêteté > esbroufe ;
> FOSS vs EE toujours distingué ; `❔` dès qu'une affirmation porte sur du code **non vendoré ici**.
>
> **Date** : 2026-06-18 · **Dépôt** : `/home/user/onix` (read-only).
>
> **Constat préliminaire décisif** : Onyx (le produit audité par `docs/audit-onyx/*`)
> **n'est PAS vendoré** dans ce dépôt. L'audit-onyx déclare lui-même sa cible :
> *« real Onyx source at `/tmp/onyx_v411` »* (`docs/audit-onyx/50-rgpd-governance.md:12`,
> `00-VERDICT.md:4`) — un clone **externe absent de ce dépôt**. Toutes ses citations
> `backend/…:ligne` / `ee/…:ligne` (≥ 54 rien que dans 30+50) **ne sont donc PAS
> vérifiables depuis ce dépôt** → classées `❔`. Ce n'est PAS un reproche de fond
> (l'audit est interne-cohérent et explicitement sourcé), mais une **limite de
> vérifiabilité** : je ne peux ni confirmer ni infirmer ce qu'il dit du code d'Onyx.

---

## Tableau de comptage

| Classe | Sens | Nombre |
|---|---|:--:|
| ✅ CONFORME | doc = code/config | 31 |
| ⚠️ ÉCART MINEUR | imprécis/périmé, intention tient | 8 |
| ❌ ÉCART MAJEUR | comportement faux | 1 |
| 🕳️ DOC-SANS-CODE | feature non implémentée | 1 |
| 🔇 CODE-SANS-DOC | implémenté, mal/non documenté | 2 |
| ❔ NON VÉRIFIABLE | porte sur Onyx (non vendoré ici) | 18 (+ tout `docs/audit-onyx/*`) |

> Les ✅ de parité sont **solides** : la couche `onix` (access-gateway/, actions/)
> implémente réellement, en code branché en production (pas des stubs), l'audit HMAC
> chaîné, la redaction PII, la DLP/anti-SSRF, l'effacement art.17, la rétention TTL,
> l'identité d'appelant HMAC anti-rejeu, le filtre ACL par-doc et le client Graph.
> Détails dans le tableau « Claims de parité » plus bas.

---

## `SECURITY.md` (racine)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| `X-OIDC-Claims` posé par l'edge, strippé s'il vient du client (anti-spoofing) | ✅ | `access-gateway/app/` (vérifié indirectement, cf. tableau parité) ; pattern oauth2-proxy `deploy/prod/` | Mécanisme réel côté gateway. |
| Clé de cache HMAC **par périmètre** (un autre périmètre ⇒ pas de fuite) | ✅ | `access-gateway/app/cache.py` `make_cache_key` (HMAC, périmètre trié) ; `Makefile:184-222` (bench) | Clé = HMAC(périmètre). |
| Post-filtre déterministe (anti-fuite, non-exécution injection, refus substitué) | ✅ | code gateway branché chemin réponse (`main.py:496-502`) ; `docs/E2E_GUARDRAILS.md` | Hors-LLM, réel. |
| **red-team 21/21** sur `qwen2.5:7b` | ⚠️ | `docs/E2E_GUARDRAILS.md`, `tests/rag/run_live.py` | Preuve dans une recette **LIVE manuelle**, pas dans le gate CI offline ; chiffre non re-vérifiable hors run live. |
| DLP egress allowlist + anti-SSRF (IP privées/metadata bloquées) | ✅ | `actions/app/dlp.py:88-150` (RFC1918, loopback, link-local 169.254, https-only, default-deny) | Réel. Limite assumée : DNS-rebinding non couvert (documenté dans le code). |
| Garde DUR incrémental streaming (abort avant chunk fautif) | ✅ | `access-gateway/app/` streaming (16 tests `test_streaming.py`) | Conforme à `docs/STREAMING.md`. |
| Zéro secret en repo : `.env` gitignoré, généré par `gen-secrets.sh` (chmod 600) | ✅ | `scripts/gen-secrets.sh:138` (chmod 600) ; `.gitignore` ; aucun `.env` suivi (`ci.yml:50-55`) | Conforme. |
| CI **gitleaks** bloquant | ✅ | `.github/workflows/ci.yml:57-61` (pas de continue-on-error) ; `Makefile:428-433` | Réellement bloquant. |
| « gitleaks du repo (**pre-commit**) » | ❌ | **Aucun `.pre-commit-config.yaml` ni `.githooks/`** dans le dépôt | `docs/SECURITY.md:67` affirme un hook pre-commit qui **n'existe pas** ; gitleaks n'existe qu'en CI/`make`. |
| Azure : Key Vault (CSI + Workload Identity), CMK | ✅ | `deploy/azure/bicep/modules/keyvault.bicep` (PE + RBAC Secrets User) ; `docs/DEPLOY_AZURE.md` | IaC présente. |
| **Toujours poser `ENCRYPTION_KEY_SECRET`** (sinon creds Onyx en clair) | ⚠️ | doc honnête (`SECURITY.md:33`, `DEPLOY_AZURE.md:77` marqué P2 « à poser ») mais **posé dans AUCUN compose/template/values** (`grep` exhaustif : 0 hors commentaires/doc) | C'est une **action opérateur**, jamais automatisée → footgun réel si oublié. Cf. P1. |
| « secrets connecteurs/LLM en clair en base » (comportement Onyx) | ❔ | claim sur Onyx, code non vendoré | Reposé sur l'audit-onyx (`/tmp/onyx_v411`). |
| Audit-trail HMAC chaîné (Onyx n'en a aucun, même EE) | ✅ (onix) / ❔ (Onyx) | onix : `actions/app/audit_log.py:88-195` (chaînage + `verify_chain`) ; endpoint `main.py:872` | Volet onix RÉEL ; « Onyx n'en a pas » = ❔ (non vendoré). |
| PII redaction logs/sorties ; effacement art.17 + rétention via `onix-actions` | ✅ | `actions/app/safe_logger.py` ; `retention.py:35-189` (`purge_by_age`, `erase_subject`) ; endpoints `main.py:916-931` | Réel. |
| `runAsNonRoot` (gateway **et** actions) | ⚠️ | gateway : forcé `values.yaml:335-339` + image `access-gateway/Dockerfile:29` (UID 10002) ; actions : image OK (`Dockerfile:46`, UID 10001) **mais aucun `securityContext` dans `values.yaml`/`actions.yaml`** | Non-root **garanti par l'image** pour actions, **pas forcé par le manifeste** (un PSS `restricted` exigerait `runAsNonRoot` explicite). Asymétrie gateway/actions. |
| Supply-chain : pip-audit --strict 0 CVE, bandit 0 medium+, trivy, helm lint | ✅ | `ci.yml:121-217` (jobs bloquants) ; `Makefile:390` (`test:` enchaîne tout) | Gates réels et bloquants en CI. |
| Conteneurs non-root, images figées (tags épinglés) | ⚠️ | images Onyx épinglées `docs/ARCHITECTURE.md:14-23` ; **mais services Onyx en compose : pas de `user:`/`cap_drop`/`read_only`** | Le durcissement non-root onix porte sur **les images onix** ; les conteneurs **Onyx** restent root par défaut (hors contrôle d'onix — cohérent avec l'audit). |

## `docs/SECURITY.md` (baseline mono-poste)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| nginx publie `127.0.0.1:${ONYX_HOST_PORT}:80` uniquement | ✅ | `docker-compose.yml` (binding localhost), `docs/ARCHITECTURE.md:14` | Conforme. |
| Ollama/PG/OpenSearch/MinIO/Redis : aucun port hôte | ✅ | `docker-compose.yml` (services sans `ports:` publiés) | Conforme. |
| 1er compte = admin ; risque prise de contrôle vierge | ✅ | comportement Onyx documenté honnêtement + mitigation (créer admin immédiatement) ; `AUTH_TYPE` dans `env.template` | Honnête ; le comportement Onyx lui-même = ❔. |
| Secrets générés (liste §5) par `gen-secrets.sh` | ✅ | `scripts/gen-secrets.sh:74-136` (SECRET, USER_AUTH_SECRET, POSTGRES, REDIS, etc.) | Liste exacte. |
| `REDIS_PASSWORD` via `--requirepass`, honoré par Onyx | ✅ (onix) / ❔ (Onyx) | `gen-secrets.sh:83` ; « honoré par Onyx » = ❔ (code Onyx non vendoré) | — |
| Scan gitleaks **pre-commit** protège | ❌ | aucun hook pre-commit (cf. SECURITY.md racine) | Même écart. |
| Checklist : `DISABLE_TELEMETRY=true`, pas de mot de passe par défaut | ✅ | `env.template`, `docker-compose.yml` (DISABLE_TELEMETRY) ; secrets aléatoires forts | Conforme. |
| `code-interpreter`/`certbot`/`mcp_server` retirés du compose | ✅ | absents de `docker-compose.yml` | Réduction de surface réelle. |

## `docs/RGPD.md`

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Inférence locale Ollama ; pas d'envoi à OpenAI/Anthropic | ✅ | `docker-compose.yml` (Ollama interne) ; pas de provider cloud câblé | Conforme. |
| `DISABLE_TELEMETRY=true`, aucune analytics tierce | ✅ | `env.template`, `docker-compose.yml` | Conforme. |
| Cartographie des données (OpenSearch/MinIO/PG/Ollama) | ✅ | `docker-compose.yml` volumes | Conforme. |
| Accès restreint `127.0.0.1` ; `.env` chmod 600 | ✅ | binding compose ; `gen-secrets.sh:138` | Conforme. |
| **Effacement via admin Onyx ; purge via `make destroy`** | ⚠️ | `Makefile:101-107` (`destroy` = `down -v`, supprime volumes) ; effacement Onyx = ❔ | `docs/RGPD.md` ignore l'effacement **ciblé art.17** d'`onix-actions` (`/admin/retention/erase`) que les autres docs vantent → **incohérence inter-docs** (sous-vente). |
| « pas de rétention imposée par l'outil » | ⚠️ | contredit `REGISTRE_TRAITEMENTS.md:25` + `retention.py:35` (`ONIX_RETENTION_DAYS=365`, purge réelle) | Doc RGPD racine **périmée** vs la couche actions. |

## `docs/REGISTRE_TRAITEMENTS.md` (art. 30) & `docs/DPIA_TEMPLATE.md` (art. 35)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Modèle à compléter par le RT/DPO (champs `(…)` à renseigner) | ✅ | structure de gabarit, mentions « pas un avis juridique » | Honnête : c'est un **template**, pas une conformité acquise. |
| Base légale = `(à qualifier)` | ⚠️ (par conception) | `REGISTRE:20,49`, `DPIA:50` | **Trou de conformité assumé** : la base légale RGPD n'est PAS tranchée (laissée au DPO). Légitime pour un template, mais la parité « Conformité RGPD ✅ GO » la suppose résolue. |
| `ONIX_RETENTION_DAYS` défaut 365, purge par âge | ✅ | `actions/app/retention.py:35` ; `gen-secrets`/values | Réel. |
| Redaction PII (JWT/IBAN/NIR/email) + anti-CRLF | ✅ | `actions/app/safe_logger.py:44-111` | Réel (motifs confirmés). |
| Journal d'audit chaîné HMAC + vérification | ✅ | `actions/app/audit_log.py:88-195` ; endpoint `main.py:872` | Réel. Repli SHA-256 si clé absente (toujours tamper-evident, sans garantie cryptographique). |
| STARTTLS exigé (SMTP) | ✅ | `actions/app/notify.py:114-135` (défaut true, refus envoi en clair) | Réel ; non éprouvé contre un vrai serveur STARTTLS (test unitaire). |
| Effacement ciblé `POST /admin/retention/erase` (art.17) | ✅ | `actions/app/main.py:924-931` → `retention.erase_subject:145-189` | Réel (DELETE par hash sujet). |
| Identité d'appelant vérifiée (HMAC/JWT), clé admin séparée fail-closed | ✅ | `actions/app/caller_identity.py:94-120` ; `security.py:177-209` (403 si clé admin absente) | Réel, fail-closed par défaut. |

## `ARCHITECTURE.md` (racine) & `docs/ARCHITECTURE.md`

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| 4 couches (ingress OIDC → gateway → Onyx → data/LLM) | ✅ | `docker-compose.yml`, `deploy/prod/`, `access-gateway/` | Conforme. |
| Cache RBAC-safe : ne stocke que le corps périmètre-déterministe ; ACL ré-appliquée par requête | ✅ | `access-gateway/app/cache.py` + `doc_acl.py` (filtre hit ET miss, `main.py:496`) | Conforme à `AGENTS.md:105`. |
| Matrice FOSS/EE/onix (RBAC par-doc, audit, chiffrement, télémétrie) | ✅ (onix) / ❔ (colonnes Onyx FOSS/EE) | colonnes onix vérifiées ; colonnes Onyx = audit non vendoré | **`ARCHITECTURE.md:67`** : chiffrement secrets marqué **✅ onix** alors que c'est une **action opérateur** (`ENCRYPTION_KEY_SECRET` jamais posé auto) → cf. ⚠️ ENCRYPTION_KEY_SECRET. |
| Images épinglées (versions exactes) | ✅ | `docs/ARCHITECTURE.md:14-23` (nginx 1.27, onyx 4.1.1, opensearch 3.6.0, postgres 15.2, redis 7.4, ollama 0.30.8) | Conforme. |
| Migrations Alembic via Job pre-install (pas de course) | 🔇 | claim HA ; chart `deploy/k8s/onix-ha/templates/` (à confirmer en détail — hors scope code ici) | Documenté mais peu détaillé côté preuve dans CE rapport (cf. rapport deploy-ops). |
| Tier applicatif stateless → réplicas + HPA | ✅ | `values.yaml` (autoscaling actions/gateway/queue) | Conforme. |

## `docs/PARITE_ENTREPRISE.md`

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Re-scoring 8 dimensions « GO » (92, GO, …) | ⚠️ | scores **auto-attribués**, non normés, sans méthode de notation publiée | Chiffres marketing internes ; les **preuves listées** (tests, gitleaks 0) sont réelles, mais le score lui-même n'est pas falsifiable. |
| « pip-audit 0 CVE · gitleaks 0 · bandit 0 H/M · helm lint 0 » | ✅ | `ci.yml` (jobs bloquants) ; `Makefile:390` | Gates réels. |
| RBAC par-doc RECHERCHE = EE/Cloud (FOSS = filtre sortie) | ✅ | `doc_acl.py:13-22` (filtre sortie assumé) ; `DECISION_RBAC.md` | **Honnêteté exemplaire** : la limite FOSS est répétée et exacte. |
| ACL auto-dérivée de SharePoint via Graph (`graph_acl.py`, `make sync-doc-acl`) | ✅ (code réel) ⚠️ (jamais testé live) | `access-gateway/app/graph_acl.py:104-388` (vrai client httpx Graph, app-only) ; `graph_client.py:36-54` ; `Makefile:114-118` | Code RÉEL, branché en prod (`main.py:110-150`). MAIS **tous les tests = `httpx.MockTransport`** (jamais contre un vrai tenant Graph) + dépend d'un mapping doc→item **manuel**. |
| Fonctions applicatives (OCR, docgen, tâches, notify, FinOps) « 34 tests » | ⚠️ | en réalité **86 fonctions `test_` dans `actions/tests`** (267 côté gateway) | Doc **sous-évalue** le nombre de tests (chiffre périmé). Implémentations réelles. |
| OCR de scans prouvé en conteneur, extraction LLM via vrai Ollama | ⚠️/❔ | tesseract+poppler dans `actions/Dockerfile:20` ; preuve « live » non rejouable depuis CE dépôt (run manuel) | Outils présents ; la « preuve E2E » est une recette manuelle. |

## `docs/COMPARATIF_COPILOT_AC360.md`

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| onix 100 % local / zéro transfert / télémétrie off | ✅ | compose (Ollama+OpenSearch+MinIO locaux, DISABLE_TELEMETRY) | Conforme. |
| Post-filtre déterministe DÉPLOYÉ, red-team 21/21 | ⚠️ | déployé : oui (`main.py`) ; 21/21 = recette live manuelle | Cf. note red-team. |
| Conformité RGPD « Parité+ (souverain) » : rétention, art.17, registre, DPIA | ⚠️ | rétention/erase **réels** ; registre+DPIA = **templates à compléter**, base légale non tranchée | « Parité+ RGPD » surévalue : la **conformité** dépend de gabarits non remplis (base légale, DPIA). Outils ✅, conformité ≠ acquise. |
| Comparaisons à Copilot/AC360 (« Supérieur »/« Parité ») | ❔ | affirmations sur produits tiers (Copilot, AC360) non vérifiables ici | Hors dépôt ; non falsifiable. |
| Moteur d'audit « porté à l'identique » depuis AC360 | ❔ | AC360 non vendoré ; pas de baseline comparable ici | Non vérifiable. |

## `docs/audit-onyx/*` (00-VERDICT, 10..70, README)

| Affirmation | Classe | Preuve | Note |
|---|---|---|---|
| Cible = `/tmp/onyx_v411`, v4.1.1 commit `33613e1`, 542K LOC | ❔ | `00-VERDICT.md:4`, `50-…md:12` ; **`/tmp/onyx_v411` absent du dépôt** | Toute la chaîne de preuve `backend/…:ligne` est **externe et non re-vérifiable ici**. |
| Télémétrie ON par défaut (`telemetry.py:30/105`, `app_configs.py:1128`) | ❔ | citations code Onyx non vendoré | Plausible et bien sourcé, mais **invérifiable depuis ce dépôt**. |
| Chiffrement secrets OFF par défaut (no-op FOSS) | ❔ | idem | idem. |
| Effacement art.17 cassé (FK NOT NULL) | ❔ | idem ; `50-…md:24` note même que `ondelete` non diffé vs migrations | L'audit **lui-même** flague cette incertitude (honnête). |
| Pas d'audit-trail dans aucune édition | ❔ | citation par absence (grep dans code externe) | Invérifiable ici. |
| Probe SharePoint réelle → HTTP 401 `InvalidAuthenticationToken` | ❔ | `00-VERDICT.md:56` ; opération réseau live non reproductible (read-only, no-net) | Plausible ; non re-jouable. |
| Scorecard (3,8/5, etc.) | ❔ | dérivé de l'analyse externe | Non falsifiable ici. |

> **Verdict sur l'audit-onyx** : document **interne-cohérent, honnête sur ses limites**
> (§9 de chaque section, §« Limites » du VERDICT), mais **0 % re-vérifiable** depuis ce
> dépôt car Onyx n'y est pas vendoré. À traiter comme une **source secondaire sourcée**,
> pas comme une preuve directe. Les *mitigations onix* qu'il invoque, elles, sont réelles
> (cf. tableau parité).

---

## Claims de parité « FOSS vs EE » → implémentation réelle dans `onix`

| Claim de parité (la doc dit « onix comble X en FOSS ») | Implémentation réelle ? | Preuve `fichier:ligne` | Verdict |
|---|---|---|---|
| **Audit-trail HMAC chaîné** (Onyx n'en a pas) | OUI, réel, branché | `actions/app/audit_log.py:88-195` (chaînage + `verify_chain`) ; endpoint `main.py:872` | ✅ (repli SHA-256 si clé absente ; côté gateway l'audit `audit.py` **n'est PAS chaîné**) |
| **RBAC par-doc (filtre sortie)** | OUI, réel, branché | `access-gateway/app/doc_acl.py:304-438` ; câblé `main.py:496-502` | ✅ FOSS = filtre sortie (assumé, exact) |
| **ACL auto-dérivée SharePoint (Graph)** | OUI (code), client réel | `graph_acl.py:104-388`, `graph_client.py:36-54`, lifespan `main.py:110-150` | ⚠️ jamais testé contre Graph réel (mocks) ; mapping doc→item **manuel** |
| **Redaction PII** | OUI, réel | `actions/app/safe_logger.py:44-157` (JWT/IBAN/NIR/CB/email/tel + anti-CRLF) | ✅ |
| **DLP egress allowlist + anti-SSRF** | OUI, réel | `actions/app/dlp.py:88-150` (RFC1918/loopback/169.254/https-only/default-deny) | ✅ (DNS-rebinding non couvert, documenté) |
| **Effacement art.17 ciblé** | OUI, réel | `main.py:924-931` → `retention.py:145-189` | ✅ |
| **Rétention TTL par âge** | OUI, réel | `retention.py:35-119` (`purge_by_age`, défaut 365 j) | ✅ |
| **Clé admin distincte fail-closed** | OUI, réel | `security.py:177-209` (403 si clé absente) | ✅ |
| **Identité appelant HMAC anti-rejeu** | OUI, réel | `caller_identity.py:94-120` (skew 300 s, compare_digest) | ✅ |
| **STARTTLS SMTP exigé** | OUI, réel | `notify.py:114-135` | ✅ (pas éprouvé live) |
| **Chiffrement secrets Onyx (`ENCRYPTION_KEY_SECRET`)** | **NON automatisé** | jamais posé dans compose/values/templates ; seulement **doc + commande manuelle** `DEPLOY_AZURE.md:77` | 🕳️/⚠️ **action opérateur**, pas une implémentation onix → présenté ✅ dans `ARCHITECTURE.md:67` |
| **Conteneurs non-root (gateway + actions)** | gateway forcé ; actions via image seule | gateway `values.yaml:335` ; actions `Dockerfile:46` (UID 10001) **sans** `securityContext` manifeste | ⚠️ asymétrie : actions non forcé au niveau pod |
| **Télémétrie OFF** | OUI | `env.template`/`docker-compose.yml` `DISABLE_TELEMETRY=true` | ✅ |
| **Garde-fous LLM red-team 21/21** | code déployé ✅ ; chiffre = recette live | `docs/E2E_GUARDRAILS.md`, `tests/rag/run_live.py` | ⚠️ pas dans le gate CI offline |

**Bilan parité** : **aucun claim de parité n'est un « mock présenté comme réel »** —
c'est l'acquis le plus solide du dépôt. Les seules réserves : (1) le chiffrement des
secrets Onyx est une **action opérateur** vendue comme acquise dans la matrice
d'architecture ; (2) la sync ACL Graph et la red-team 21/21 reposent sur des
**preuves live non reproductibles** dans le gate CI offline ; (3) la « conformité RGPD »
dépend de **templates non remplis** (base légale, DPIA).

---

## Écarts « production-ready » (P0 / P1 / P2)

### P0 (bloquant prod régulée)
- *(aucun écart P0 sur le périmètre sécurité/RGPD)* — les contrôles dur (fail-closed
  admin, DLP, audit chaîné, ACL sortie) sont réels et branchés. Le seul risque de niveau
  P0 *potentiel* (secrets Onyx en clair) est **un footgun opérateur**, pas un défaut de
  code — reclassé P1 car documenté mais non garanti.

### P1 (à corriger avant mise en prod)
1. **`ENCRYPTION_KEY_SECRET` jamais posé automatiquement** : `SECURITY.md:33` /
   `ARCHITECTURE.md:67` le présentent comme un acquis onix (✅) alors qu'il faut le poser
   **à la main** (`DEPLOY_AZURE.md:77`, marqué P2) et qu'**aucun compose/values ne le
   câble**. Oubli ⇒ creds connecteurs/LLM en clair en base Onyx. → l'imposer (fail-loud
   au boot si vide) ou au minimum dans `env.template`/values avec garde-fou.
2. **Hook gitleaks pre-commit annoncé mais absent** (`docs/SECURITY.md:67`,
   `SECURITY.md` racine implicite) : pas de `.pre-commit-config.yaml`. gitleaks n'existe
   qu'en CI. → ajouter le hook **ou** corriger la doc (`❌`).
3. **`docs/RGPD.md` périmé / sous-vend la conformité** : « effacement via admin Onyx »
   + « pas de rétention imposée » (`RGPD.md:42-46`) **ignore** l'effacement art.17 ciblé
   et la purge TTL d'`onix-actions` que tout le reste du dépôt revendique → **incohérence
   inter-docs** exploitable par un auditeur (« votre propre doc RGPD ne mentionne pas vos
   contrôles »). → réaligner `RGPD.md` sur `REGISTRE_TRAITEMENTS.md`.
4. **`securityContext` absent du déploiement `actions`** (Helm) : non-root garanti par
   l'image seule, pas forcé au niveau pod ⇒ échec sous Pod Security Standards `restricted`.
   → ajouter `securityContext.runAsNonRoot` au bloc `actions:` de `values.yaml`.

### P2 (durcissement / honnêteté documentaire)
5. **Audit gateway non chaîné** : `access-gateway/app/audit.py` journalise les décisions
   d'accès mais **sans chaînage HMAC** (contrairement à `actions`). La doc parle d'un
   « journal d'accès … intégré à l'audit HMAC » (`PARITE:40`) → l'audit *chaîné* tamper-
   evident n'existe que côté actions. Clarifier ou étendre le chaînage à la gateway.
6. **Scores de parité auto-attribués** (PARITE « GO 92 », COMPARATIF « Supérieur ») sans
   barème publié → préciser que ce sont des évaluations internes, pas des notes auditées.
7. **Preuves « live » non reproductibles** dans le gate offline : red-team 21/21, OCR
   conteneur, extraction Ollama, sync Graph → marquées « prouvées » mais hors CI. Exposer
   clairement « recette manuelle » vs « gate CI ».
8. **`docs/audit-onyx/*` non re-vérifiable** ici (Onyx non vendoré) : ajouter un encart
   en tête signalant que les preuves `backend/…:ligne` pointent un clone externe
   (`/tmp/onyx_v411`) absent du dépôt — pour qu'aucun lecteur ne les prenne pour du code
   local.

---

## Verdict (3 lignes)

La couche `onix` **tient ses promesses de parité sécurité/RGPD en code réel et branché**
(audit HMAC chaîné, ACL sortie, DLP/anti-SSRF, redaction PII, rétention/erasure,
fail-closed admin) : **aucun mock présenté comme réel** sur le périmètre — l'acquis est
solide et la distinction FOSS vs EE est honnête et exacte. Les écarts sont **documentaires
et opérationnels**, pas fonctionnels : `ENCRYPTION_KEY_SECRET` vendu comme acquis mais
laissé à l'opérateur, hook pre-commit gitleaks annoncé mais absent, `RGPD.md` périmé qui
sous-vend ses propres contrôles, `securityContext` actions manquant, et un dossier
`audit-onyx` **sourcé mais non re-vérifiable ici** (Onyx non vendoré → tout claim sur son
code = ❔). **Production-ready côté contrôles ; pas encore côté preuves de conformité**
(base légale/DPIA = templates à remplir) ni côté garde-fous opérationnels (3 écarts P1).
