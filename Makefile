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
