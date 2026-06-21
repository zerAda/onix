# Runtime Evidence — pile complète bootée sur Azure (2026-06-21)

**Environnement (jetable)** : VM Azure **Standard_D16as_v5** (16 vCPU / 64 Go), Ubuntu 22.04, **France Central**, sub `IA GEREP`. Pile `docker compose -f docker-compose.yml -f docker-compose.prod-local.yml` (`onyx-backend:4.1.1`), modèles `nomic-embed-text` + `qwen2.5:14b-instruct`. Pilotée par `az vm run-command` (aucun port ouvert). **Première fois qu'onix est réellement bootée de bout en bout** (impossible en local : Docker verrouillé sans admin).

## Verdicts runtime — ce que seul le live révèle

| # | Constat | Verdict | Preuve |
|---|---------|---------|--------|
| 1 | **Boot complet ordonné** | ✅ | 11/11 conteneurs `Up (healthy)` ; `make verify` = **25 OK / 0 échec** ; healthchecks + `depends_on: service_healthy` convergent dans l'ordre → **HARD-04 prouvé au runtime** (l'audit le notait « non vérifié ») |
| 2 | **Backup → restore round-trip** | ✅ | `make backup` (31 s, stop→tar→restart) ; `make restore` → Postgres **3 bases intactes**, `verify` vert post-restore. **Réfute le pire de BKP-02** (« cluster corrompu ») : le tar est froid mais sur une pile *arrêtée* = cohérent. BKP-01 **prouvé**. |
| 3 | **Front HTTP sous charge** | ✅ | 200/200 requêtes `HTTP 200` sur `:3000` (20 //), `/nginx-health` en **0,5 ms** |
| 4 | **RAM** | ✅ | 50 Gi libres sous charge — la 64 Go est **sur-dimensionnée RAM** ; la RAM n'est jamais la contrainte |
| 5 | **Plafond débit LLM** | ⚠️ **LIMITE #1** | `qwen2.5:14b` en CPU 16 cœurs : **~0,1 req/s**, latence **14–60 s/réponse**, la concurrence n'aide PAS (Ollama sérialise). 1→8 gens : tokens ×8 mais temps ×4. → **GPU obligatoire pour du multi-utilisateur** (sinon ~7 réponses RAG/min max) |
| 6 | **Résilience `restart: always`** | ⚠️ **TROU RÉEL** | `api_server` tué **pendant son redémarrage** (juste après le backup) : resté `exited` (code 137, **restarts=0**) — `restart: always` n'a **pas** rattrapé ; reprise seulement après `docker start` manuel (→ `running/healthy` en 25 s). Fenêtre de vulnérabilité au démarrage sur le service le plus critique. |
| 7 | **Édition Onyx** | 🔍 nuance | Image **EE-capable** (`/app/ee/` présent, module EE chargé au boot) mais **non licenciée** (`LICENSE_ENFORCEMENT_ENABLED` défaut `true`, aucune clé) → fonctions payantes **license-gated**. « On déploie l'image FOSS » est **imprécis** (c'est l'image EE en mode non-licencié), mais l'effet pratique ≈ FOSS **confirme** le récit FOSS-vs-EE de l'audit (dont M20-F1). |

## Impact sur le verdict production ([PROD-READINESS.md](PROD-READINESS.md))
- **Dim 1 (Fonctionnalités up)** 🟡 → la pile **boote saine et sert** (UI 200, Ollama câblé). Reste non prouvé : une **vraie requête RAG E2E** (retrieval+réponse+citation) et la qualité (RAGAS baseline réelle).
- **Dim 4 (Fiabilité)** : backup/restore **prouvé sain** (réfute le pire) ; MAIS **résilience restart edge-case** (#6) + cold-tar/downtime/non-chiffré/no-WAL restent → toujours pas GO, mais le tableau s'éclaircit.
- **Inchangé (non testé runtime)** : sécurité M1/M3/M7, observabilité M4 (alertes no-op), compliance M20, supply-chain `pip-audit` ROUGE — ce sont des défauts de *code/config*, pas révélés par le boot.

## Limite #1 à retenir pour la prod
Le **LLM en CPU est le goulot** : `qwen2.5:14b` ≈ 0,1 req/s. Pour un go-live multi-utilisateur → **GPU** (VM N-series) ou un modèle plus petit + budget de latence assumé. La RAM/IO/HTTP ne sont PAS les limites ; le calcul d'inférence l'est.

---
*Preuves collectées sur VM jetable (az run-command). VM à détruire après lecture (`az group delete -n onix-test-rg`).*
