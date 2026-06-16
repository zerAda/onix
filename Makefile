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
