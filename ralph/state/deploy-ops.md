<!-- Pour clore le scope, écrire `RALPH_DONE` comme TOUTE PREMIÈRE ligne. -->
# État Ralph — deploy-ops

## Backlog (source : docs/audit-reality/deploy-ops.md)
| ID | Prio | Écart | Axe | Statut |
|---|---|---|---|---|
| D0 | P1 | `ENCRYPTION_KEY_SECRET` jamais posé (compose/values/templates) | A3 | ✅ |
| D1 | P1 | Ingress Azure chat→gateway + anti-spoofing non templatisé (`templates/ingress.yaml`) | A3/A6 | ◑ (route OPT-IN templatisée ; forward-auth/anti-spoofing = TODO recette documenté) |
| D2 | P1 | TLS Redis/PG Onyx non livrés (`values-azure.yaml`, `configmap.yaml`) | A3 | ✅ |
| D3 | P1 | `scripts/backup.sh` ignore la surcouche prod (`-f deploy/prod/...`) | A5/A6 | ✅ |
| D4 | P2 | Durcissement Helm partiel (non-root/seccomp seulement gateway) | A5 | ✅ (seccomp partout ; non-root où l'image le permet) |
| D4b | P2 | Durcissement Helm restant : NetworkPolicy OPT-IN + readOnlyRootFS OPT-IN | A3/A5 | ✅ (itér. 2 ; OPT-IN défaut OFF, rendu inchangé) |
| D5 | P2 | RUNBOOK §7 : `inference_` vs `indexing_model_server` | A1 | ✅ |
| D6 | P0 | **#10 OOM** : `make tune` fixait `OLLAMA_MEM_LIMIT=12g` < empreinte réelle 14B (~20 Go) → SIGKILL en génération | A4/A6 | ✅ (itér. 3 ; 14B → ≥24g, prouvé hors-runtime) |
| D7 | P0 | **#9 provider** : `llm_provider` Onyx vide après déploiement → chat « No default LLM model found » | A1/A6 | ✅ (itér. 3 ; `seed-provider.sh` + cible `seed-provider`, idempotent fail-closed) |
| D8 | P1 | **#6 résilience** : `restart: always` n'a pas rattrapé un kill api_server PENDANT l'init | A4/A6 | ◑ (itér. 3 ; invariants restart/healthcheck assertés en test + documentés ; reprise Docker = runtime only) |

## Journal
| Itér. | Date | Item | Correctif | Gates | SHA |
|---|---|---|---|---|---|
| 1 | 2026-06-18 | D0 | ENCRYPTION_KEY_SECRET câblé (Helm helper + compose base + gen-secrets + env templates + values-kind-smoke) | lint/tpl×3 OK ; rendu 11× ; compose config OK ; gitleaks 0 | da739a8 |
| 1 | 2026-06-18 | D2 | REDIS_SSL/REDIS_PORT + POSTGRES_PORT/POSTGRES_SSLMODE conditionnels (configmap) ; values-azure (6380/require) | lint/tpl×3 OK ; rendu Azure expose les 4 ; default n'en rend aucun | 45c448a |
| 1 | 2026-06-18 | D3 | backup.sh/restore.sh : PROFILE base/prod/local-prod (empile compose prod) ; `-p onix` | bash -n OK ; jeu compose prod empilé ; gitleaks 0 | a160f00 |
| 1 | 2026-06-18 | D4 | helper onix.podSecurityContext : seccomp partout ; non-root actions/worker (UID 10001) ; pas de régression Onyx/Ollama root | lint/tpl×3 OK ; rendu vérifié par workload | 8951309 |
| 1 | 2026-06-18 | D5 | RUNBOOK §7 → indexing_model_server (make up PERF=1) | doc | df5e8bf |
| 1 | 2026-06-18 | D1 | ingress chat→gateway OPT-IN (route Exact gated) ; forward-auth/anti-spoofing = TODO documenté | lint/tpl×3 OK ; route gated (gw+chat) ; pas de route orpheline ; gitleaks 0 | 326da5e |
| 2 | 2026-06-18 | D4b | NetworkPolicy OPT-IN (`templates/networkpolicy.yaml`, default-deny ingress + allow par composant) ; `networkPolicy.enabled=false` défaut | lint OK ; tpl×3 défaut → NetworkPolicy=0 ; `--set …enabled=true` → 8 (9 si gateway) ; docs défaut=36 inchangé ; gitleaks 0 | 58ba627 |
| 2 | 2026-06-18 | D4b | readOnlyRootFS OPT-IN access-gateway (rootfs RO + emptyDir /tmp) ; `accessGateway.readOnlyRootFilesystem=false` défaut ; PAS sur Onyx/Ollama/actions | lint OK ; OFF→rendu inchangé ; ON→securityContext+emptyDir rendus | 58ba627 |
| 3 | 2026-06-22 | M7 | **Preuve de transit proxy (compose prod)** : `nginx.prod.conf` injecte `X-OIDC-Proxy-Secret` = `GATEWAY_PROXY_SHARED_SECRET` (monté en TEMPLATE + `NGINX_ENVSUBST_FILTER` pour ne substituer QUE cette variable ; `$vars` nginx intacts) + strip `X-OIDC-Proxy-Secret` sur `/api` et `/` ; `Caddyfile` strip l'en-tête au bord ; `docker-compose.prod.yml` câble l'env (nginx + access-gateway, `:?` requis) ; `env.prod.template` + `gen-secrets.sh` (case prod) génèrent le secret. Couvre le volet **compose/proxy** de l'anti-spoofing évoqué en D1 (le volet code gateway est dans scope access-gateway, M7). | `docker compose -f … config` **exit 0** (clés rendues) ; gitleaks attendu 0 (secret en env, pas en repo) | _voir commits_ |
| 3 | 2026-06-22 | D6 | `detect-hardware.sh`+`.ps1` : empreinte modèle = PIC réel (KV+prompt-cache), pas poids Q4 ; 14B→24g/48-64 Go ; seuils sélection alignés (22/12/5) ; avert. fail-closed petite RAM ; surcharges `ONIX_FORCE_*`/`ONIX_SKIP_DOCKER` (test) | `test_detect_hardware_mem.py` (6 tests OK) ; 64 Go→24g, 32→14g, 16→5g, somme<RAM partout | (worktree) |
| 3 | 2026-06-22 | D7 | `scripts/seed-provider.sh` + cible Make `seed-provider` (idempotent, fail-closed, auth admin par env) ; intégré au flux up-local-prod (message) | `test_seed_provider.py` (5 tests OK : idempotence, création, force, fail-closed, modèle chat) | (worktree) |
| 3 | 2026-06-22 | D8 | Invariants restart/healthcheck des services critiques assertés (`docker-compose.prod-local.yml`) + documentés (#6) | `test_restart_policy.py` (4 tests OK) | (worktree) |
| 4 | 2026-06-22 | #12-modèle | **Famille GEMMA3 par défaut** (chat) + **embeddinggemma** (Ollama-natif/RAGAS) à la place de qwen2.5/nomic dans `detect-hardware.sh`+`.ps1`+`env.template`. Empreintes PIC re-mappées (gemma3:12b≈18 · 4b≈7 · 1b≈3 · 27b≈30) ; machinerie anti-OOM #10 **INCHANGÉE**. Motif : gemma3 répond sourcé sur CPU (prouvé live #12) là où qwen ratait le tool-calling. | `test_detect_hardware_mem.py` re-aligné **6 tests OK** (64Go→gemma3:12b≥18g, somme<RAM partout) | (branche prod) |
| 4 | 2026-06-22 | BKP-02/03 | **Backup chiffré fail-closed** : `backup.sh` chiffre chaque archive (openssl AES-256-CBC PBKDF2, passphrase `ONIX_BACKUP_PASSPHRASE` env) et REFUSE de produire du clair sans passphrase (sauf `ONIX_BACKUP_ALLOW_PLAINTEXT=1` DEV) ; `restore.sh` déchiffre (pipe, pas de clair sur disque) + refuse si `.enc` sans passphrase. | `test_backup_encrypt.py` (round-trip openssl + fail-closed sans passphrase) | (branche prod) |

## Runtime-only (dit honnêtement — pas « testé »)
- **#10** : que le 14B se charge ET génère SANS OOM avec `OLLAMA_MEM_LIMIT=24g` —
  prouvé une fois sur la VM (40g) ; le calcul corrigé donne 24g (≥ empreinte 20 Go)
  mais le non-OOM à 24g précis reste à reconfirmer sur pile réelle.
- **#9** : le CONTRAT exact de l'API admin Onyx (`/admin/llm/provider` champs/forme,
  `/auth/login`) — le seed est codé d'après l'évidence runtime + l'API Onyx 4.x,
  mais la persistance réelle en base `llm_provider` n'est validable qu'en live.
- **#6** : la REPRISE Docker après un kill ciblé pendant l'init (course étroite) —
  comportement du démon, non simulable sans démarrer la pile. On verrouille les
  INVARIANTS de config (restart always + start_period + ordered start) en statique.

## Questions bloquantes / décisions structurantes
- **D1 (ingress AKS)** : le forward-auth oauth2-proxy + l'anti-usurpation (strip
  X-OIDC-Claims) n'ont PAS été templatisés — c'est trop structurel/cluster-dépendant
  sans cluster : (1) oauth2-proxy n'est pas un template du chart (à déployer hors-chart) ;
  (2) le strip d'en-tête exige un snippet propre au contrôleur (ingress-nginx
  `configuration-snippet`/`more_clear_input_headers`), souvent désactivé par défaut.
  → Décision prise : route chat→gateway templatisée (OPT-IN, validée `helm template`) +
  TODO recette honnête (DEPLOY_AZURE.md §Ingress, values.yaml chatViaGateway). Activation
  Azure laissée OFF tant que le forward-auth n'est pas câblé (sinon chat sans identité
  vérifiée). À confirmer/compléter sur AKS réel (annotations contrôleur).
- Coordination Helm : cette vague = seul agent à toucher `deploy/**` ; ENCRYPTION_KEY +
  securityContext pris en charge ici (security-governance ne touche pas le Helm).

## Critères de sortie A1–A7
- [x] A1 (doc↔code réconciliés ; D1 requalifié honnêtement) - [x] A2 (n/a tests code ; rendus helm vérifiés)
- [x] A3 (ENCRYPTION_KEY + TLS managé câblés ; D1 forward-auth = TODO documenté, pas faux-acquis)
- [~] A4 (runbook §7 corrigé ; observabilité Helm = scope monitoring) - [x] A5 (securityContext, backup cohérent)
- [x] A6 (helm lint/template×3 verts, scripts bash -n OK, gitleaks 0) - [ ] A7 (n/a)
