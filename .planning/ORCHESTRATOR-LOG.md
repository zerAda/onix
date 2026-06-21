# Orchestrator Log — boucle d'auto-amélioration

Journal du chief-orchestrator : stats par cycle + **méta-critique qui améliore le cycle suivant** (la boucle s'améliore elle-même).

---

## Cycle 1 — 2026-06-21

**Pipeline** : 6 scouts (dimensions) → 24 candidats → Verify adversarial → 14 confirmés / 9 non-vérifiés / 1 rejeté → (Spec authored par l'orchestrateur). Sortie : `MISSIONS.md` (16 missions dédupliquées).

**Incident** : le workflow background a **calé 2 fois** (≈ 3,5 h puis une nuit) — les workflows background **ne progressent pas quand la session est idle**. Récupéré en parsant `journal.jsonl` (scouts + verdicts y sont sérialisés) et en finissant Spec inline. → **Leçon structurelle #1.**

**Valeur livrée** : la boucle a trouvé des défauts réels **au-delà** du backlog de 23 items, dont 4 nouveaux à fort impact :
- **M1** downgrade d'algo audit (défait l'inviolabilité HMAC même clé posée) — *nouveau, P0*.
- **M7** passerelle fait confiance aveugle à `X-OIDC-Claims` (spoof RBAC si accès direct :8200 ; base compose sans oauth2-proxy) — *nouveau, candidat P0, non-vérifié*.
- **M3/M4** ACL Fabric morte dans le hot-path ; alertes livrées dans le vide + doc « ✅ conforme » fausse — *nouveaux, sapent « provably secure »*.
- **M14** les docs `audit-reality` certifient « ✅ conforme » un gate (RAGAS) qui ne démarre pas — *méta-honnêteté*.

Les vérificateurs ont aussi **honnêtement dégradé** des items (credential-guard surévalué : défaut sûr ; doc-ACL fail-open non atteignable par les formes Onyx connues) — c'est leur rôle adversarial qui paie.

### Méta-critique → améliorations Cycle 2

1. **Tenir dans une fenêtre active (priorité absolue).** Les workflows background gèlent à l'idle. Cycle 2 : **≤ 12 agents**, ou Verify **en pipeline** (pas barrière) pour que les résultats à forte valeur tombent tôt, ou piloté inline. Ne plus lancer un fan-out de 30+ agents en background sur cette session intermittente.
2. **Dédup amont.** ~25 % des candidats étaient des doublons inter-dimensions (RAGAS trouvé ×3, Fabric ×2, Ralph ×2, cosign/SHA ×2). Cycle 2 : passer aux scouts une **liste d'exclusion** (missions du cycle précédent + RALPH-HANDOFF) + un pré-pass de dédup par similarité (pas seulement par id exact) avant Verify.
3. **Vérifier par valeur décroissante.** La barrière Verify a laissé **M7 (HIGH, potentiellement le pire)** non vérifié quand ça a calé, pendant que des MED passaient. → Verify en pipeline trié par `value` scout.
4. **Affûter les prompts scout.** La dimension `test-honesty` (doc↔code, mock-as-real) a été la plus rentable → la renforcer. La dimension `supply-chain-ci` a re-sorti des items déjà au backlog (DEP-03/CICD-01) → préfixer « cosign et sbom-action sont DÉJÀ connus ; trouve du NOUVEAU (SHA-pin systémique, faux "attached to release", checksums) ».
5. **Angles morts non couverts (à ajouter Cycle 2)** : (a) **correction du cache RBAC-safe** (clé de périmètre, ré-application ACL par requête) ; (b) **RGPD côté Onyx Postgres** (effacement chat/comptes — RGPD-01) ; (c) **performance mono-poste** (num_ctx, pools, OCR) ; (d) **Onyx web/UI** (branding, XSS avatar). Remplacer une dimension redondante par « data-correctness: cache + RGPD ».
6. **Prochaine mission la plus haute valeur** : **vérifier M7** (gateway trusted-header) en premier ; si confirmée → fix in-app fail-closed.

### Édits de prompt concrets pour Cycle 2
- *Scout* : ajouter « Items DÉJÀ connus (ne pas re-proposer sauf affûtage matériel) : RAGAS-dead-gate, Fabric-ACL-unwired, cosign/CICD-01, sbom-action-pin, backup-pg_dump, restore-drill, HARD-01/02/03, OBS-03/04/05. Privilégie le NOUVEAU. Lis et cite file:line réel. »
- *Verify* : passer en `pipeline()` trié par `value` (HIGH d'abord) — garantit que les items critiques sont jugés même si la session se ferme.
- *Spec* : conserver, mais ne lancer Spec que si Verify a tenu ; sinon l'orchestrateur rédige les specs inline (fait au Cycle 1).
- *Taille* : `parallel` cappé à ~10 ; viser une complétion < 5 min de session active.

**État** : MISSIONS.md livré.

### Exécutions livrées entre Cycle 1 et Cycle 2 (mergées sur `main`, CI verte)
- **M5a / M12 / M14** (`3298729`) — 3 mensonges doc corrigés (pg_dump, faux « SBOM attached to release », fausses « ✅ conforme ») + toutes les actions GitHub SHA-pinnées + Dependabot.
- **M2** (`b4d3a4e`) — gate RAGAS ressuscité (collision `conftest` → `prompt_loader.py`), **vérifié localement** (`pytest tests/rag` = 158 passed ; runner exécute l'éval), + test anti-régression.
- **M13** (`798cde1`) — boucle Ralph durcie : gates de sécurité (bandit/gitleaks/pip-audit) avant commit, commit scopé (plus de `git add -A`), disjoncteurs, refus arbre sale + verrou ; README réconcilié. `bash -n` OK.

---

## Cycle 2 — 2026-06-21

**Pipeline** : 3 scouts **synchrones** (appels `Agent` directs, PAS de workflow background) → blind spots Cycle 1 : cache RBAC-safe, RGPD-Onyx, FinOps/streaming. → **3 trouvailles NOUVELLES** (M17–M19) + 1 secondaire (streaming exfil). Dédup via liste d'exclusion : **zéro re-signalement** d'item Cycle 1.

**Validation des méta-améliorations Cycle 1** :
- ✅ #1 (survivre à l'idle) — scouts synchrones : **aucun stall** (le background avait calé 2×).
- ✅ #2 (dédup) — la liste d'exclusion a marché : aucune redite.
- ✅ #5 (blind spots) — les 3 dimensions ajoutées ont chacune produit du neuf (incohérence clé-cache/portée ; doc DPO fausse vs audit-onyx ; budget tronqué + non-enforced).

**Trouvailles** : M17 cache↔portée (justesse, MED) ; M18 doc RGPD DPO contredite par l'audit du dépôt (HIGH honnêteté) + chemin d'effacement Onyx réalisable ; M19 budget fenêtre-1000 + observe-only (events morts).

### Méta-critique → améliorations Cycle 3
1. **Coût** : chaque scout a consommé ~100–135k tokens (lectures profondes). Cycle 3 : borner les lectures (cibler 4–6 fichiers/scout) ou un 1er passage `Explore` (moins cher) avant lecture profonde.
2. **Classe d'amélioration révélée par M18** : « honnêteté des docs compliance vs `audit-onyx/` ». Lancer un **balayage dédié** : croiser CHAQUE affirmation de `DPIA_TEMPLATE.md` / `RGPD.md` / `REGISTRE_TRAITEMENTS.md` contre `docs/audit-onyx/` — d'autres contradictions DPO-facing probables.
3. **M7 toujours non vérifié** (gateway X-OIDC trusted-header) — le vérifier reste la plus haute valeur sécurité ouverte ; le faire en tête de Cycle 3.
4. **Passer du « trouver » au « livrer offline »** : M17 (cache) et M19 (budget) sont **vérifiables offline** (pytest) — un cycle « fix-and-verify » sur l'un d'eux (PR scopée + scope-docs) prouverait la boucle de bout en bout sur du code de scope, pas seulement des docs/CI.

**Bilan boucle (2 cycles)** : 19 missions cataloguées (dont ~10 nouvelles hors backlog) ; **5 corrections réellement livrées + CI-vertes** (M2, M5a, M12, M13, M14) ; 0 collision Ralph ; honnêteté tenue (sévérités bornées, claims non vérifiables marquées).

---
*Prochain : Cycle 3 (vérifier M7 ; balayage honnêteté compliance ; fix-and-verify M17/M19) — ou router M17–M19 vers Ralph.*
