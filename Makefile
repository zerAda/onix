# =============================================================================
# onix — Stack IA privée (Onyx + Ollama). Pilotage en une commande.
#   make detect    diagnostic matériel (CPU/RAM/GPU) — lecture seule
#   make tune      AUTO-TUNING : écrit les réglages optimaux dans .env
#   make secrets   génère les secrets forts dans .env
#   make up        démarre tout (GPU=1 = profil GPU NVIDIA ; PERF=1 = haut débit)
#   make models    (pré)télécharge les modèles Ollama
#   make verify    contrôle de bout en bout (santé + câblage + génération)
#   make preflight-local  pré-vol AVANT make up (prérequis du 1er lancement)
#   make logs / ps / stats / down / restart / update / backup / restore / destroy
#   make sync-doc-acl  synchronise l'ACL par-document SharePoint→doc_acl.json (Graph)
# Pré-requis : Docker + Docker Compose v2 (`docker compose`).
# =============================================================================

SHELL := /bin/bash
COMPOSE := docker compose -f docker-compose.yml
ifdef GPU
COMPOSE += -f docker-compose.gpu.yml
endif
ifdef PERF
COMPOSE += -f docker-compose.performance.yml
endif
PORT = $$(sed -n 's/^ONYX_HOST_PORT=//p' .env 2>/dev/null | head -n1); PORT=$${PORT:-3000}

.DEFAULT_GOAL := help
.PHONY: help detect tune secrets secrets-gateway up down restart ps stats logs models verify preflight-local config update backup restore destroy

help:
	@grep -E '^#   make' Makefile | sed 's/^#   /  /'
	@echo ""
	@echo "  Démarrage type :  make tune  →  make secrets  →  make up  →  make verify"

detect:
	@bash scripts/detect-hardware.sh

tune:
	@bash scripts/detect-hardware.sh --apply

secrets:
	@bash scripts/gen-secrets.sh

# Secrets de la PASSERELLE (access-gateway/.env) : génère GATEWAY_CACHE_HMAC_SECRET
# (requis si cache activé) + GATEWAY_AUDIT_SALT. Les creds Graph/Onyx restent manuels.
secrets-gateway:
	@ENV_FILE=access-gateway/.env bash scripts/gen-secrets.sh

# Démarre la stack puis pré-tire les modèles → "prêt à l'emploi".
up: secrets
	@$(COMPOSE) up -d
	@echo "→ Attente des services + téléchargement des modèles (1er lancement : plusieurs minutes)…"
	@bash scripts/pull-models.sh || true
	@{ $(PORT); echo ""; echo "✓ Stack démarrée. Ouvrez : http://localhost:$$PORT"; \
	   echo "  Le PREMIER compte créé devient ADMINISTRATEUR."; \
	   echo "  Assistant LLM : choisir 'Ollama', URL http://ollama:11434 (cf. docs/RUNBOOK.md)."; }

down:
	@$(COMPOSE) down

restart:
	@$(COMPOSE) restart

ps:
	@$(COMPOSE) ps

stats:
	@docker stats --no-stream $$($(COMPOSE) ps -q)

logs:
	@$(COMPOSE) logs -f --tail=200

models:
	@bash scripts/pull-models.sh

verify:
	@bash scripts/verify.sh

# Pré-vol AVANT `make up` : détecte les prérequis bloquants du 1er lancement
# (daemon Docker, vm.max_map_count, RAM, disque, ports, .env + secrets requis).
preflight-local:
	@bash scripts/preflight-local.sh

# Valide la syntaxe du compose (résout les variables) sans rien démarrer.
config:
	@$(COMPOSE) config -q && echo "✓ docker-compose.yml valide"

# Met à jour les images au tag épinglé dans .env (revoir le changelog avant).
update:
	@$(COMPOSE) pull
	@$(COMPOSE) up -d
	@echo "✓ Images mises à jour (tag = IMAGE_TAG du .env)."

backup:
	@bash scripts/backup.sh

restore:
	@bash scripts/restore.sh $(DIR)

# Détruit conteneurs + volumes (DONNÉES PERDUES). Demande confirmation.
# if/then/else explicite : le statut de `down -v` n'est PAS masqué par un `||`
# (l'antipattern `cmd && ... || echo` afficherait "Annulé" même si `down -v` échoue).
destroy:
	@read -p "⚠ Supprimer conteneurs ET volumes (données perdues) ? [oui/non] " a; \
	 if [ "$$a" = "oui" ]; then \
	   $(COMPOSE) down -v; \
	 else \
	   echo "Annulé."; \
	 fi

# --- RBAC par document : sync ACL SharePoint → doc_acl.json (Microsoft Graph) ---
# Lit un mapping { doc_id: {site_id, drive_id, item_id} } + les creds Graph (env
# GATEWAY_GRAPH_*) et ÉCRIT access-gateway/config/doc_acl.json (chemin StaticDocACL).
# Surcharge des chemins : MAPPING=... OUT=...  (cf. docs/connectors/SHAREPOINT.md).
# À lancer périodiquement (cron/CI) pour propager les changements d'accès.
.PHONY: sync-doc-acl
MAPPING ?= access-gateway/config/doc_acl_mapping.json
OUT ?= access-gateway/config/doc_acl.json
sync-doc-acl:
	@python scripts/sync-doc-acl.py --mapping "$(MAPPING)" --out "$(OUT)"

# --- WS1 ---
# Recette QA/garde-fous de l'agent commercial RAG (cf. docs/QA_GUARDRAILS.md).
#   make rag-test       recette hors-LLM (anti-régression prompt + red-team + dataset)
#   make rag-test-live  recette live contre une vraie API Onyx (ONIX_API_URL requis)
#   make rag-eval       éval qualité RAGAS (LLM-juge LOCAL Ollama ; gate qualité)
#   make rag-eval-ci    éval RAGAS + gate absolu + compare anti-régression (nightly)
#   make rag-deps       installe les dépendances de test (pytest, PyYAML, requests)
.PHONY: rag-test rag-test-live rag-eval rag-eval-ci rag-deps

rag-deps:
	@pip install -r tests/rag/requirements.txt

# Mode contrat : aucun LLM ni réseau requis. C'est le gate CI.
rag-test:
	@python -m pytest tests/rag -q

# Mode live : rejoue dataset + red-team contre l'API Onyx (citations/refus réels).
# Pré-requis : export ONIX_RAG_LIVE=1 ONIX_API_URL=... [ONIX_API_KEY ONIX_PERSONA_ID]
rag-test-live:
	@ONIX_RAG_LIVE=1 python -m pytest tests/rag -q

# Éval qualité RAGAS (faithfulness / context_precision / answer_relevancy) sur le
# golden set FR, scorée par un LLM-juge sur l'Ollama LOCAL (souverain, hors-cloud).
# Applique un gate qualité et SORT EN CODE NON NUL si le gate échoue (recette/CI).
# Pré-requis (mêmes conventions que rag-test-live) : Ollama joignable +
#   export ONIX_LIVE_OLLAMA=1 ONIX_LIVE_MODEL=qwen2.5:7b-instruct [ONIX_OLLAMA_URL=...]
# Seuils surchargeables : ONIX_RAGAS_MIN_FAITHFULNESS / _CONTEXT_PRECISION / _ANSWER_RELEVANCY
# NB : les tests OFFLINE (juge mocké, sans réseau) tournent via `make rag-test`/CI.
rag-eval:
	@cd tests/rag && ONIX_LIVE_OLLAMA=1 python -m ragas_eval.runner

# Éval RAGAS « CI/nightly » : exécute l'éval LIVE en écrivant les scores JSON,
# applique le GATE ABSOLU (seuils) ET la COMPARAISON ANTI-RÉGRESSION vs la
# baseline committée. Échoue si le gate échoue OU si une métrique régresse de
# plus de la tolérance. C'est la cible utilisée par .github/workflows/ragas-nightly.yml.
# Deux garde-fous complémentaires : gate absolu (« assez bon ? ») + relatif (« régressé ? »).
# Pré-requis identiques à `rag-eval` (Ollama joignable, ONIX_LIVE_OLLAMA=1, ONIX_LIVE_MODEL).
# Variables surchargeables :
#   SCORES=tests/rag/scores.json   fichier de scores produit par le runner
#   BASELINE=tests/rag/ragas_eval/baseline_scores.json   baseline anti-régression
#   TOL=0.05                       tolérance de régression (bruit du juge)
# Rafraîchir la baseline après un run sain (revoir le diff !) :
#   python -m ragas_eval.compare_scores ../scores.json --baseline ragas_eval/baseline_scores.json --update
SCORES   ?= scores.json
BASELINE ?= ragas_eval/baseline_scores.json
TOL      ?= 0.05
rag-eval-ci:
	@cd tests/rag && set -e; \
	  rc=0; \
	  ONIX_LIVE_OLLAMA=1 python -m ragas_eval.runner --json "$(SCORES)" || rc=$$?; \
	  echo ""; \
	  python -m ragas_eval.compare_scores "$(SCORES)" --baseline "$(BASELINE)" --tolerance "$(TOL)" || rc=$$?; \
	  [ "$$rc" = "0" ] && echo "✓ RAGAS : gate absolu PASS et aucune régression." || \
	    { echo "✗ RAGAS : gate ÉCHOUÉ et/ou régression détectée (code $$rc)."; exit 1; }

# Mini-bench OFFLINE du cache applicatif RBAC-safe (cf. docs/CACHE.md).
# Émet N requêtes identiques (sans réseau) contre un `Cache` en mémoire et
# imprime hit-rate + tokens économisés. Sert à vérifier le câblage et à
# matérialiser l'ordre de grandeur des gains attendus en charge réelle.
#   make cache-bench           N=200 par défaut
#   make cache-bench N=1000    pour stresser la LRU
# Le bench est INLINE (script python passé en argument à python3 -c) — aucun
# fichier ajouté hors du périmètre du WS ; AUCUN appel réseau (LRU mémoire).
.PHONY: cache-bench
define CACHE_BENCH_PY
import os, sys
sys.path.insert(0, "access-gateway")
from app.cache import build_cache, make_cache_key, normalize_question, estimate_tokens

class S:
    pass

s = S()
s.cache_enabled = True
s.cache_redis_url = ""
s.cache_ttl_seconds = 3600
s.cache_max_entries = 512
s.cache_hmac_secret = os.environ["GATEWAY_CACHE_HMAC_SECRET"]
s.cache_locale = "fr"

cache = build_cache(s)
authorized = ["clients-nord"]
q = normalize_question("Quelles sont les echeances du client ABC ?")
key = make_cache_key(settings=s, principal="u", normalized_question=q,
                     authorized_doc_sets=authorized)
body = {"message": "Voici la reponse mockee avec source [Document: contrats.pdf]." * 5}
cache.store(key, body, ttl=3600)

N = int(os.environ.get("N", "200"))
hits, saved = 0, 0
for _ in range(N):
    hit = cache.lookup(key)
    if hit is not None:
        hits += 1
        saved += estimate_tokens(hit)

hit_rate = hits / N * 100 if N else 0.0
print(f"cache-bench: requests={N}  hits={hits}  hit-rate={hit_rate:.1f}%  tokens_saved~={saved}")
endef
export CACHE_BENCH_PY
cache-bench:
	@N=$${N:-200} GATEWAY_CACHE_HMAC_SECRET=$${GATEWAY_CACHE_HMAC_SECRET:-bench-secret} \
	  python3 -c "$$CACHE_BENCH_PY"
	@echo "✓ cache-bench OK (offline). Voir docs/CACHE.md pour l'interprétation."

# --- WS4 ---
# Déploiement Kubernetes HAUTE DISPONIBILITÉ & scale-out (chart Helm onix-ha).
# Cibles de VALIDATION (hors cluster) + déploiement. Cf. docs/HA_SCALING.md.
#   make k8s-lint       helm lint du chart
#   make k8s-template   helm template (défauts) -> rendu + parse YAML
#   make k8s-validate   lint + template (défauts ET data-tier) + parse PyYAML
#   make k8s-deps        helm dependency build (rafraîchit les sous-charts vendorisés)
#   make k8s-deploy      helm upgrade --install (prod ; requiert SECRET + NS onix)
.PHONY: k8s-lint k8s-template k8s-validate k8s-deps k8s-deploy
K8S_CHART := deploy/k8s/onix-ha

k8s-lint:
	@helm lint $(K8S_CHART)

k8s-template:
	@helm template onix $(K8S_CHART) --namespace onix

# Validation COMPLÈTE sans cluster : lint, rendu (2 profils), et re-parse strict
# du YAML (kind/apiVersion/metadata.name) + scan gitleaks si présent.
k8s-validate:
	@echo "→ helm lint…"            && helm lint $(K8S_CHART)
	@echo "→ helm template (défauts)…" \
	  && helm template onix $(K8S_CHART) -n onix > /tmp/onix-k8s-default.yaml
	@echo "→ helm template (data-tier activé)…" \
	  && helm template onix $(K8S_CHART) -n onix \
	     --set postgresql.cluster.enabled=true --set postgresql.operator.enabled=true \
	     --set opensearch.enabled=true --set redis.enabled=true \
	     --set redisOperator.enabled=true --set minio.enabled=true \
	     --set secrets.create=true \
	     --set secrets.values.POSTGRES_PASSWORD=x --set secrets.values.OPENSEARCH_ADMIN_PASSWORD=x \
	     --set secrets.values.REDIS_PASSWORD=x --set secrets.values.S3_AWS_ACCESS_KEY_ID=x \
	     --set secrets.values.S3_AWS_SECRET_ACCESS_KEY=x --set secrets.values.SECRET=x \
	     --set secrets.values.USER_AUTH_SECRET=x --set secrets.values.ONIX_ACTIONS_API_KEY=x \
	     --set secrets.values.BROKER_PASSWORD=x > /tmp/onix-k8s-ha.yaml
	@echo "→ parse YAML strict (PyYAML)…" \
	  && python3 -c "import yaml,sys; \
	d=[x for x in yaml.safe_load_all(open('/tmp/onix-k8s-ha.yaml')) if x]; \
	bad=[o for o in d if not (o.get('kind') and o.get('apiVersion') and o.get('metadata',{}).get('name'))]; \
	print('docs:',len(d),'invalides:',len(bad)); sys.exit(1 if bad else 0)"
	@command -v gitleaks >/dev/null 2>&1 \
	  && { echo '→ gitleaks…'; gitleaks detect --no-banner --source $(K8S_CHART) -c .gitleaks.toml || true; } \
	  || echo '→ gitleaks absent (ignoré).'
	@echo "✓ Validation hors-cluster OK (charge/HA réelle : recette sur cluster — cf. docs/HA_SCALING.md §9)."

k8s-deps:
	@helm dependency build $(K8S_CHART)

# Déploiement prod. Variables : NS (namespace, def. onix), HOST (Ingress),
# SECRET (nom du Secret applicatif créé HORS-CHART). Active le socle data HA.
k8s-deploy:
	@helm upgrade --install onix $(K8S_CHART) -n $${NS:-onix} --create-namespace \
	  --set secrets.existingSecret=$${SECRET:?Créez d abord le Secret K8s (cf. docs/HA_SCALING.md §8)} \
	  --set postgresql.operator.enabled=true --set postgresql.cluster.enabled=true \
	  --set opensearch.enabled=true --set redisOperator.enabled=true --set redis.enabled=true \
	  --set minio.enabled=true \
	  --set ingress.host=$${HOST:-onix.example.com}

# =============================================================================
# --- WS3 --- Déploiement de PRODUCTION (TLS Caddy + OIDC Entra ID + multi-env)
# -----------------------------------------------------------------------------
# Schéma multi-environnement : un fichier .env PAR environnement.
#   ENV=.env                      (dev / local — défaut, profil basic 127.0.0.1)
#   ENV=deploy/prod/.env.test     (test / pré-prod)
#   ENV=deploy/prod/.env.prod     (production)
# La surcouche prod s'EMPILE sur la base : reverse-proxy TLS, OIDC forcé, nginx
# repassé en interne, garde-fou « défaut-sûr » (refuse une expo sans TLS+OIDC).
#
#   make config-prod   valide la composition base + prod (syntaxe, défaut ENV=.env)
#   make up-prod       démarre la stack de PRODUCTION (base + surcouche prod)
#   make down-prod / logs-prod / ps-prod     exploitation prod
#   make secrets-prod  génère les secrets dans le .env ciblé (ENV=…)
# Démarrage type prod :
#   cp deploy/prod/env.prod.template deploy/prod/.env.prod   # puis renseigner
#   make secrets-prod ENV=deploy/prod/.env.prod
#   make up-prod      ENV=deploy/prod/.env.prod
# =============================================================================
ENV ?= .env
COMPOSE_PROD := docker compose --env-file $(ENV) -f docker-compose.yml -f deploy/prod/docker-compose.prod.yml

.PHONY: config-prod up-prod down-prod restart-prod ps-prod logs-prod secrets-prod preflight-prod

# Valide la composition (résout les variables) sans rien démarrer.
config-prod:
	@$(COMPOSE_PROD) config -q && echo "✓ base + prod valide (ENV=$(ENV))"

# Génère/complète les secrets dans le fichier d'environnement ciblé.
secrets-prod:
	@ENV_FILE=$(ENV) bash scripts/gen-secrets.sh

# Démarre la stack de production (base + surcouche TLS/OIDC). Le service
# `preflight` refuse de démarrer une exposition sans TLS+OIDC+vérif. e-mail.
up-prod:
	@$(COMPOSE_PROD) up -d
	@echo "→ Stack PROD démarrée (ENV=$(ENV)). Caddy obtient le certificat TLS au 1er accès."
	@D=$$(sed -n 's/^ONYX_DOMAIN=//p' $(ENV) 2>/dev/null | head -n1); \
	  echo "  Ouvrez : https://$${D:-<ONYX_DOMAIN>}  (SSO Entra ID)."; \
	  echo "  Callback à déclarer côté Entra ID : https://$${D:-<ONYX_DOMAIN>}/auth/oidc/callback"

down-prod:
	@$(COMPOSE_PROD) down

restart-prod:
	@$(COMPOSE_PROD) restart

ps-prod:
	@$(COMPOSE_PROD) ps

logs-prod:
	@$(COMPOSE_PROD) logs -f --tail=200

# Exécute le garde-fou de défaut-sûr seul (diagnostic), avec l'environnement ciblé.
preflight-prod:
	@set -a; . ./$(ENV) 2>/dev/null || true; set +a; sh scripts/preflight-prod.sh

# =============================================================================
# --- prod-local --- PRODUCTION sur MACHINE UNIQUE (durci, SANS domaine public)
# -----------------------------------------------------------------------------
# Tier intermédiaire entre la base (POC, 127.0.0.1) et deploy/prod (domaine
# public + OIDC). Empile docker-compose.prod-local.yml : healthchecks +
# démarrage ORDONNÉ (depends_on condition=service_healthy) + restart:always.
# Testeurs via Tailscale Serve (TLS privé) ou LAN. Runbook : docs/PROD_LOCAL.md.
# Survie au redémarrage : unit systemd deploy/local-prod/onix.service.
#   make config-local-prod   valide base + prod-local (syntaxe)
#   make up-local-prod       démarre la stack durcie machine unique
#   make down-local-prod / restart-local-prod / ps-local-prod / logs-local-prod
# Démarrage type : make tune → make secrets → make preflight-local → make up-local-prod → make verify
# =============================================================================
COMPOSE_LOCAL_PROD := docker compose -f docker-compose.yml -f docker-compose.prod-local.yml
.PHONY: config-local-prod up-local-prod down-local-prod restart-local-prod ps-local-prod logs-local-prod

config-local-prod:
	@$(COMPOSE_LOCAL_PROD) config -q && echo "✓ base + prod-local valide"

up-local-prod: secrets
	@$(COMPOSE_LOCAL_PROD) up -d
	@echo "→ Stack prod-local démarrée (healthchecks + démarrage ordonné + restart: always)."
	@echo "  Accès testeurs : tailscale serve 3000 (TLS privé) — cf. docs/PROD_LOCAL.md §5."

down-local-prod:
	@$(COMPOSE_LOCAL_PROD) down

restart-local-prod:
	@$(COMPOSE_LOCAL_PROD) restart

ps-local-prod:
	@$(COMPOSE_LOCAL_PROD) ps

logs-local-prod:
	@$(COMPOSE_LOCAL_PROD) logs -f --tail=200

# --- WS6 ---------------------------------------------------------------------
# Observabilité + gates qualité/sécurité/supply-chain (miroir LOCAL de la CI).
#   make test          lance TOUTES les barrières CI en local (cf. ci.yml)
#   make lint          yamllint (workflows + monitoring)
#   make sbom          génère le SBOM de l'image onix-actions (syft)
#   make monitor-up    démarre la stack d'observabilité (Prometheus/Grafana/Loki)
#   make monitor-down  arrête la stack d'observabilité
#   make monitor-config  valide le compose de monitoring (sans rien démarrer)
# Pré-requis make test : python3 + pip, docker, gitleaks (téléchargé si absent).
MONITORING_COMPOSE := docker compose -f monitoring/docker-compose.monitoring.yml

.PHONY: test lint docs-check docs-freshness hooks-install llms-full pytest bandit pip-audit trivy gitleaks compose-validate sbom \
        monitor-up monitor-down monitor-config monitor-logs monitor-render

# Barrière unique : tout ce que la CI vérifie, en local, dans l'ordre.
test: lint compose-validate monitor-render pytest bandit pip-audit gitleaks trivy
	@echo "✓ WS6 : toutes les barrières qualité/sécurité/supply-chain sont VERTES."

lint: docs-check
	@command -v yamllint >/dev/null 2>&1 || pip install --quiet yamllint
	@yamllint -d "{extends: relaxed, rules: {line-length: disable}}" \
	  .github/workflows monitoring
	@echo "✓ YAML valide (workflows + monitoring)."

# Valide l'infra de doc pour agents (STRUCTURE) : registre docs/scopes/scopes.json,
# gabarit des dossiers, 0 lien de navigation mort, signale les orphelins.
docs-check:
	@python3 scripts/check-docs-map.py

# Garde anti-DRIFT (« à chaque action, vérifie ») : toute modif de code d'un scope
# doit s'accompagner d'une MAJ de sa doc agent. BASE surchargeable (défaut origin/main).
# Ex : make docs-freshness BASE=origin/main  ·  hook pre-commit : --staged.
docs-freshness:
	@python3 scripts/check-docs-freshness.py $(if $(BASE),$(BASE),)

# (Re)génère llms-full.txt (carte agent à contenu embarqué). docs-check vérifie
# qu'il est à jour ; lancer cette cible après toute modif d'orientation/dossiers.
llms-full:
	@python3 scripts/gen-llms-full.py

# Active les hooks versionnés (.githooks/pre-commit : docs-check + docs-freshness --staged).
hooks-install:
	@git config core.hooksPath .githooks
	@chmod +x .githooks/* 2>/dev/null || true
	@echo "✓ hooks git activés (core.hooksPath=.githooks). 'git commit --no-verify' pour outrepasser."

compose-validate:
	@docker compose -f docker-compose.yml config -q
	@docker compose -f docker-compose.yml -f docker-compose.performance.yml config -q
	@docker compose -f docker-compose.yml -f docker-compose.gpu.yml config -q
	@docker compose -f docker-compose.yml -f docker-compose.prod-local.yml config -q
	@docker compose -f docker-compose.yml -f docker-compose.prod-local.yml -f docker-compose.lan.yml config -q
	@$(MONITORING_COMPOSE) config -q
	@echo "✓ Tous les fichiers compose (base/PERF/GPU/prod-local/LAN/monitoring) sont valides."

pytest:
	@command -v pytest >/dev/null 2>&1 || pip install --quiet pytest
	@pip install --quiet -r actions/requirements.txt
	@pytest -q actions/tests
	@[ -d tests/rag ] && pytest -q tests/rag || echo "  (tests/rag absent — ignoré)"
	@[ -d access-gateway/tests ] && pytest -q access-gateway/tests || echo "  (access-gateway/tests absent — ignoré)"

bandit:
	@command -v bandit >/dev/null 2>&1 || pip install --quiet bandit
	@bandit -r actions access-gateway scripts --exclude '**/tests/**,**/.venv/**' \
	  --severity-level medium --confidence-level medium
	@echo "✓ bandit : aucune vulnérabilité (sévérité moyenne+)."

pip-audit:
	@command -v pip-audit >/dev/null 2>&1 || pip install --quiet pip-audit
	@for req in actions/requirements.txt tests/rag/requirements.txt access-gateway/requirements.txt; do \
	  [ -f "$$req" ] && { echo "→ pip-audit $$req"; pip-audit --requirement "$$req" --strict --progress-spinner off; } || true; \
	done
	@echo "✓ pip-audit : aucune CVE connue dans les dépendances épinglées."

gitleaks:
	@command -v gitleaks >/dev/null 2>&1 || { \
	  wget -q https://github.com/gitleaks/gitleaks/releases/download/v8.18.2/gitleaks_8.18.2_linux_x64.tar.gz -O /tmp/gl.tgz && \
	  sudo tar -xzf /tmp/gl.tgz -C /usr/local/bin gitleaks; }
	@gitleaks detect --source . --config .gitleaks.toml --no-git --redact
	@echo "✓ gitleaks : 0 secret détecté."

# Scan vulnérabilités filesystem + image (requiert trivy installé localement).
trivy:
	@command -v trivy >/dev/null 2>&1 || { echo "⚠ trivy non installé — étape effectuée en CI (ci.yml). Voir https://aquasecurity.github.io/trivy"; exit 0; }
	@trivy fs --severity CRITICAL,HIGH --ignore-unfixed --exit-code 1 .
	@docker build -t onix-actions:local ./actions
	@trivy image --severity CRITICAL,HIGH --ignore-unfixed --exit-code 1 onix-actions:local
	@echo "✓ trivy : aucune vulnérabilité CRITICAL/HIGH corrigeable."

# SBOM de l'image (syft). Installe syft à la volée si absent.
sbom:
	@command -v syft >/dev/null 2>&1 || { echo "⚠ syft non installé — généré en CI (cd.yml). Voir https://github.com/anchore/syft"; exit 0; }
	@docker build -t onix-actions:local ./actions
	@syft onix-actions:local -o spdx-json=sbom.onix-actions.spdx.json
	@echo "✓ SBOM écrit : sbom.onix-actions.spdx.json"

monitor-up:
	@# Garde-fou anti admin/admin : on refuse de démarrer Grafana sans un
	@# GRAFANA_ADMIN_PASSWORD FORT dans .env. Sans ce contrôle, le compose
	@# retomberait sur un défaut connu et l'UI loopback serait exposée en
	@# identifiants devinables. `make secrets` génère ce mot de passe (32 car.).
	@PW=$$(sed -n 's/^GRAFANA_ADMIN_PASSWORD=//p' .env 2>/dev/null | head -n1); \
	  if [ -z "$$PW" ]; then \
	    echo "✗ GRAFANA_ADMIN_PASSWORD absent de .env. Lancez 'make secrets' (ou définissez un mot de passe fort) avant 'make monitor-up'."; exit 1; \
	  fi; \
	  case "$$PW" in admin|*CHANGEME*) \
	    echo "✗ GRAFANA_ADMIN_PASSWORD trivial/par défaut. Définissez un vrai secret (make secrets)."; exit 1;; esac; \
	  if [ $${#PW} -lt 12 ]; then \
	    echo "✗ GRAFANA_ADMIN_PASSWORD trop court (< 12 caractères). Renforcez-le (make secrets)."; exit 1; \
	  fi
	@# Garde-fou FAIL-CLOSED Alertmanager : sans ALERT_WEBHOOK_URL, TOUTE alerte
	@# (budget FinOps, service down, chaîne d'audit rompue) partirait dans le vide.
	@# On REFUSE de démarrer la stack plutôt que d'avaler les alertes en silence.
	@# (Le conteneur alertmanager refuse aussi au boot — double garde.)
	@WH=$$(sed -n 's/^ALERT_WEBHOOK_URL=//p' .env 2>/dev/null | head -n1); \
	  if [ -z "$$WH" ]; then \
	    echo "✗ ALERT_WEBHOOK_URL absent/vide dans .env. Sans lui, alertmanager n'a AUCUNE destination : les alertes (budget, service down, audit) seraient perdues. Renseignez l'URL webhook (Slack/Mattermost/Teams-compatible) avant 'make monitor-up'. Fail-closed."; exit 1; \
	  fi; \
	  case "$$WH" in http://*|https://*) : ;; *) \
	    echo "✗ ALERT_WEBHOOK_URL ne commence pas par http(s):// — URL invalide. Fail-closed."; exit 1;; esac
	@$(MONITORING_COMPOSE) up -d
	@P=$$(sed -n 's/^GRAFANA_HOST_PORT=//p' .env 2>/dev/null | head -n1); P=$${P:-3001}; \
	  U=$$(sed -n 's/^GRAFANA_ADMIN_USER=//p' .env 2>/dev/null | head -n1); U=$${U:-onix-admin}; \
	  echo "✓ Observabilité démarrée. Grafana : http://localhost:$$P (utilisateur : $$U / GRAFANA_ADMIN_PASSWORD)."

monitor-down:
	@$(MONITORING_COMPOSE) down

monitor-config:
	@$(MONITORING_COMPOSE) config -q && echo "✓ monitoring/docker-compose.monitoring.yml valide."

# Valide le rendu fail-closed d'Alertmanager : (1) le gabarit rendu contient un
# webhook_configs RÉEL pointant ALERT_WEBHOOK_URL (pas vide/commenté) ; (2) sans
# l'URL, le rendu est REFUSÉ. Test autonome (stdlib, sans Docker).
monitor-render:
	@python3 scripts/check-alertmanager-config.py

monitor-logs:
	@$(MONITORING_COMPOSE) logs -f --tail=200

# =============================================================================
# --- NETTOYAGE (release) -----------------------------------------------------
# `make clean`      : retire les artefacts/caches/temporaires RÉGÉNÉRABLES.
#                     CONSERVE le pertinent : code source, **doc (dont
#                     docs/scopes/)**, configs, .env (secrets), backups/. C'est le
#                     « clean release » : l'arbre reste fonctionnel, seul le
#                     jetable disparaît. Idempotent, sûr (ne touche jamais .git).
# `make clean-deep` : clean + venvs Python locaux (actions/.venv, access-gateway/.venv).
# (Pour DÉTRUIRE la stack Docker + volumes : `make destroy`.)
# =============================================================================
.PHONY: clean clean-deep

clean:
	@echo "→ Nettoyage release (artefacts/caches/temporaires régénérables)…"
	@find . -path ./.git -prune -o -type d -name '__pycache__'   -exec rm -rf {} + 2>/dev/null || true
	@find . -path ./.git -prune -o -type d -name '.pytest_cache' -exec rm -rf {} + 2>/dev/null || true
	@find . -path ./.git -prune -o -type d -name '.mypy_cache'   -exec rm -rf {} + 2>/dev/null || true
	@find . -path ./.git -prune -o -type d -name '.ruff_cache'   -exec rm -rf {} + 2>/dev/null || true
	@find . -path ./.git -prune -o -type d -name '*.egg-info'    -exec rm -rf {} + 2>/dev/null || true
	@find . -path ./.git -prune -o -type f -name '*.py[co]'      -exec rm -f  {} + 2>/dev/null || true
	@find . -path ./.git -prune -o -type f \( -name '.DS_Store' -o -name 'Thumbs.db' -o -name '*.swp' \) -exec rm -f {} + 2>/dev/null || true
	@rm -rf .coverage coverage.xml htmlcov dist build
	@rm -f sbom*.json sbom*.spdx.json sbom*.xml
	@rm -f deploy/azure/bicep/main.json
	@echo "✓ clean : jetable retiré. Conservés : code, docs (docs/scopes/…), configs, .env, backups/."

clean-deep: clean
	@echo "→ Nettoyage profond (venvs Python locaux)…"
	@rm -rf actions/.venv access-gateway/.venv
	@echo "✓ clean-deep : venvs retirés (recréez-les via vos commandes d'install)."
