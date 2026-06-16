# =============================================================================
# onix — Stack IA privée (Onyx + Ollama). Pilotage en une commande.
#   make detect    diagnostic matériel (CPU/RAM/GPU) — lecture seule
#   make tune      AUTO-TUNING : écrit les réglages optimaux dans .env
#   make secrets   génère les secrets forts dans .env
#   make up        démarre tout (GPU=1 = profil GPU NVIDIA ; PERF=1 = haut débit)
#   make models    (pré)télécharge les modèles Ollama
#   make verify    contrôle de bout en bout (santé + câblage + génération)
#   make logs / ps / stats / down / restart / update / backup / restore / destroy
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
.PHONY: help detect tune secrets up down restart ps stats logs models verify config update backup restore destroy

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

# --- WS1 ---
# Recette QA/garde-fous de l'agent commercial RAG (cf. docs/QA_GUARDRAILS.md).
#   make rag-test       recette hors-LLM (anti-régression prompt + red-team + dataset)
#   make rag-test-live  recette live contre une vraie API Onyx (ONIX_API_URL requis)
#   make rag-deps       installe les dépendances de test (pytest, PyYAML, requests)
.PHONY: rag-test rag-test-live rag-deps

rag-deps:
	@pip install -r tests/rag/requirements.txt

# Mode contrat : aucun LLM ni réseau requis. C'est le gate CI.
rag-test:
	@python -m pytest tests/rag -q

# Mode live : rejoue dataset + red-team contre l'API Onyx (citations/refus réels).
# Pré-requis : export ONIX_RAG_LIVE=1 ONIX_API_URL=... [ONIX_API_KEY ONIX_PERSONA_ID]
rag-test-live:
	@ONIX_RAG_LIVE=1 python -m pytest tests/rag -q

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

.PHONY: test lint pytest bandit pip-audit trivy gitleaks compose-validate sbom \
        monitor-up monitor-down monitor-config monitor-logs

# Barrière unique : tout ce que la CI vérifie, en local, dans l'ordre.
test: lint compose-validate pytest bandit pip-audit gitleaks trivy
	@echo "✓ WS6 : toutes les barrières qualité/sécurité/supply-chain sont VERTES."

lint:
	@command -v yamllint >/dev/null 2>&1 || pip install --quiet yamllint
	@yamllint -d "{extends: relaxed, rules: {line-length: disable}}" \
	  .github/workflows monitoring
	@echo "✓ YAML valide (workflows + monitoring)."

compose-validate:
	@docker compose -f docker-compose.yml config -q
	@docker compose -f docker-compose.yml -f docker-compose.performance.yml config -q
	@docker compose -f docker-compose.yml -f docker-compose.gpu.yml config -q
	@$(MONITORING_COMPOSE) config -q
	@echo "✓ Tous les fichiers compose (base/PERF/GPU/monitoring) sont valides."

pytest:
	@command -v pytest >/dev/null 2>&1 || pip install --quiet pytest
	@pip install --quiet -r actions/requirements.txt
	@pytest -q actions/tests
	@[ -d tests/rag ] && pytest -q tests/rag || echo "  (tests/rag absent — ignoré)"
	@[ -d access-gateway/tests ] && pytest -q access-gateway/tests || echo "  (access-gateway/tests absent — ignoré)"

bandit:
	@command -v bandit >/dev/null 2>&1 || pip install --quiet bandit
	@bandit -r actions --exclude '**/tests/**,**/.venv/**' \
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
	@$(MONITORING_COMPOSE) up -d
	@P=$$(sed -n 's/^GRAFANA_HOST_PORT=//p' .env 2>/dev/null | head -n1); P=$${P:-3001}; \
	  echo "✓ Observabilité démarrée. Grafana : http://localhost:$$P (admin / GRAFANA_ADMIN_PASSWORD)."

monitor-down:
	@$(MONITORING_COMPOSE) down

monitor-config:
	@$(MONITORING_COMPOSE) config -q && echo "✓ monitoring/docker-compose.monitoring.yml valide."

monitor-logs:
	@$(MONITORING_COMPOSE) logs -f --tail=200
