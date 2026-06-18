# onix

## What This Is

**onix** est un assistant RAG d'entreprise **100 % souverain et auto-hébergé**, bâti sur **Onyx 4.1.1** (plateforme RAG FOSS, MIT) + **Ollama** (LLM local), avec une **couche de compensation `onix`** qui ajoute ce qu'Onyx FOSS ne fournit pas pour un usage régulé : passerelle RBAC + ACL par-document, audit HMAC chaîné, garde-fous déterministes, redaction PII, DLP egress, rétention/effacement RGPD, télémétrie OFF, déploiement durci.

Cible immédiate : **déploiement de production sur machine unique (Docker Compose)** pour un usage **interne GEREP** (courtier en assurance / prévoyance-santé), des **dizaines d'utilisateurs**, sur des **documents sensibles RGPD** (PII, données assurance).

## Core Value

**La sécurité et la gouvernance doivent être *prouvables*.** Si tout le reste échoue, le cloisonnement (RBAC par groupe → Document Set + ACL par-document), les garde-fous déterministes et la traçabilité inviolable (audit HMAC chaîné) doivent tenir et être démontrables à un auditeur. Aucune fuite d'information entre périmètres, aucune décision d'accès non journalisée.

## Requirements

### Validated

<!-- Inféré de la base de code existante (cf. .planning/codebase/). Verrouillé : modifier requiert discussion explicite. -->

- ✓ Passerelle RBAC FastAPI : mapping groupes Entra → Document Sets, `enforce_document_sets` écrase tout choix client — existant (`access-gateway/`)
- ✓ ACL par-document : filtre de sortie post-récupération (statique JSON + Graph SharePoint), refus si zéro citation autorisée — existant (`access-gateway/app/doc_acl.py`, `graph_acl.py`)
- ✓ Cache RBAC-safe : clé HMAC déterministe incluant le périmètre autorisé trié, ACL ré-appliquée par requête — existant (`access-gateway/app/cache.py`)
- ✓ Garde-fous déterministes post-filtre (hors LLM, anti-injection) : citations, hallucination, exfil, injection — existant (`access-gateway/app/guardrail.py`)
- ✓ Audit HMAC chaîné inviolable (append-only, vérification par rejeu) — existant (`actions/app/audit_log.py`)
- ✓ Microservice `onix-actions` : moteur d'audit OCR, génération .docx, tâches/notify, suivi usage/coût, kill-switch admin, PII/DLP/rétention/effacement — existant (`actions/`)
- ✓ Inférence LLM 100 % locale (Ollama, aucun appel cloud), télémétrie OFF — existant (`docker-compose.yml`)
- ✓ Stack mono-poste Docker Compose durcie + overlays (`.gpu`/`.performance`/`.prod-local`/`.lan`) — existant
- ✓ Portes de qualité vertes en CI : pytest (actions/gateway/rag), bandit, pip-audit `--strict`, gitleaks, trivy, helm lint, compose config — existant
- ✓ Génération de secrets hors-repo (`scripts/gen-secrets.sh`, `.env` gitignoré) — existant
- ✓ Éval RAG RAGAS + red-team garde-fous (21/21 sur `qwen2.5:7b`) — existant (`tests/rag/`)

### Active

<!-- Scope de ce cycle : durcir + prouver la sécurité + fiabiliser le go-live mono-poste. Hypothèses jusqu'à livraison & validation. -->

- [ ] Prouver la sécurité pour le go-live : combler les lacunes de couverture **HIGH** (tests ACL SharePoint live/staging, rotation red-team sur modèle plus large)
- [ ] Garde anti-secrets-par-défaut au démarrage prod (rejeter `POSTGRES_PASSWORD=password`, MinIO `minioadmin`, etc. en mode production)
- [ ] Chemin de production mono-poste fiable : preflight, healthchecks + ordre de démarrage, `restart:always`, sauvegarde/restauration vérifiées, runbook opérationnel
- [ ] Observabilité opérationnelle : Prometheus + Grafana + Loki + alertes (échec de sync ACL, échec garde-fou, santé services)
- [ ] Remédier les CVE corrigeables sans casse : `cryptography` → 48.x, `pypdf` → 6.12.x (gate pip-audit `--strict` vert)
- [ ] Pipeline CI/CD de release reproductible (gate qualité vert → image taguée publiable)
- [ ] Dossier de preuve sécurité : modèle de menace vérifié contre le code, démonstration de l'audit trail et du fail-closed

### Out of Scope

<!-- Frontières explicites avec justification (empêche la ré-introduction). -->

- **AKS / Kubernetes HA** (`deploy/k8s/onix-ha/`) — explicitement hors scope : la cible est la machine unique ; le chart Helm existe mais n'est pas ce cycle
- **SAML SSO** — reporté à un milestone ultérieur : l'OIDC/Entra existant suffit pour l'interne GEREP ; ajouter SAML = risque planning sur une fenêtre de quelques semaines
- **Admin UI self-service** — reporté : opérable en CLI/config pour le go-live ; non nécessaire pour prouver la sécurité
- **Nouveaux connecteurs** — reporté : le périmètre documentaire du go-live est couvert par l'existant
- **Multi-tenancy FOSS** — exclu par conception : isolation = instances séparées (non requis pour usage interne mono-client)
- **Chiffrement des secrets at-rest Onyx & perm-sync par-document à l'indexation** — fonctions **EE/Cloud** : en FOSS, mitigées par durcissement infra + filtre de sortie passerelle (cf. CONCERNS.md)
- **Cloud LLM / API externes** — contraire à la souveraineté : tout reste local (Ollama)

## Context

- **Brownfield** : base de code mature et fonctionnelle, cartographiée dans `.planning/codebase/` (STACK, ARCHITECTURE, STRUCTURE, CONVENTIONS, TESTING, INTEGRATIONS, CONCERNS).
- **Pourquoi la couche onix existe** : l'audit byte-level d'Onyx v4.1.1 (`docs/audit-onyx/00-VERDICT.md`) conclut « premium prod-ready » mais les fonctions entreprise décisives sont **payantes (EE/Cloud) ou absentes** (RBAC par-doc, audit-trail, chiffrement secrets, SSO complet). onix comble en FOSS.
- **Concerns prioritaires connus** (cf. `.planning/codebase/CONCERNS.md`) : lacune de tests ACL SharePoint live (HIGH, chemin sécurité critique) ; secrets par défaut dans le compose de base ; CVE corrigeables (`cryptography` 46.0.7, `pypdf` 6.10.2) ; red-team biaisé vers petits modèles Ollama.
- **Domaine** : GEREP = courtage assurance / prévoyance-santé → documents PII et potentiellement données de santé → exigence RGPD/CNIL forte, cohérente avec l'obsession souveraineté/sécurité du dépôt.
- **État Git** : la branche `main` locale est en retard de ~73 commits sur `origin/main` (fast-forward possible) — à synchroniser avant le go-live. Identité de commit auto-dérivée (`a.zeriri@gerep.fr`) — à confirmer.

## Constraints

- **Timeline** : go-live visé en **quelques semaines (< 1 mois)** — favoriser le chemin le plus court vers une mise en production sûre.
- **Déploiement** : **machine unique Docker Compose uniquement** — pas de Kubernetes/AKS ce cycle.
- **Souveraineté** : inférence 100 % locale, aucun appel cloud, télémétrie OFF — non négociable.
- **Sécurité** : zéro secret en repo (`.env` gitignoré, généré par `gen-secrets.sh`) ; fail-closed ; `runAsNonRoot` ; egress allowlisté.
- **Qualité** : les portes (`pytest`, `pip-audit --strict` 0 CVE, `gitleaks` 0 secret, `bandit` 0 medium+, `helm lint`, `compose config`) **doivent rester vertes**.
- **Style** : commentaires/docstrings **en français**, **stdlib-first**, code qui ressemble au code voisin ; **zéro mock présenté comme réel**.
- **FOSS vs EE** : toujours distinguer ; ne pas présupposer qu'une feature « entreprise » est gratuite.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| AKS / Kubernetes HA hors scope ce cycle | Cible = machine unique ; raccourcir le chemin vers le go-live sécurisé | — Pending |
| SAML + Admin UI reportés à un milestone ultérieur | OIDC/Entra suffit en interne ; éviter le risque planning sur < 1 mois | — Pending |
| RBAC par-document = passerelle FOSS (filtre de sortie) | perm-sync à l'indexation = EE + certificat ; le filtre passerelle cloisonne en FOSS | ✓ Good (existant) |
| Garde-fous déterministes HORS LLM | Une sécurité LLM-based serait injectable ; heuristiques déterministes = anti-injection | ✓ Good (existant) |
| Priorité absolue = sécurité *prouvable* (pas seulement présente) | Client régulé, données sensibles ; « honnêteté > esbroufe » | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-18 after initialization*
