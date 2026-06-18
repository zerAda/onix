# Pitfalls Research

**Domain:** Self-hosted regulated RAG — productionisation mono-poste (Onyx FOSS + onix overlay)
**Researched:** 2026-06-19
**Confidence:** HIGH — derived from byte-level codebase audit + domain-specific regulatory constraints

---

## Critical Pitfalls

### Pitfall 1: Sauvegardes non testées / restoration jamais exercée

**What goes wrong:**
`scripts/backup.sh` existe et produit des archives `.tgz` pour les quatre volumes (db_volume, opensearch-data, minio_data, file-system). L'équipe présume que les sauvegardes fonctionnent car le script ne lève pas d'erreur. La première restauration réelle (après incident, après vol de machine) révèle : archives incomplètes (volume mal nommé), incompatibilité de version Postgres entre l'archive et le conteneur reconstruit, ou — plus grave — OpenSearch refuse de démarrer sur un index restauré sans ses métadonnées internes. Résultat : données RGPD potentiellement perdues, indisponibilité prolongée, et impossibilité de prouver la continuité de l'audit trail après restauration.

**Why it happens:**
La procédure `backup.sh` arrête la stack, tar les volumes montés, redémarre. C'est correct pour la cohérence physique, mais elle ne valide pas que Postgres peut relire le cluster (pg_controldata, checksum) ni qu'OpenSearch indexe correctement après restauration. Les volumes Docker ont un nommage préfixé par le nom du projet Compose (`onix_db_volume`). Si le répertoire de travail ou le nom du projet change à la restauration, `docker volume create` crée un volume vide et la cible est silencieusement ignorée.

**How to avoid:**
1. Ajouter un script `scripts/restore-verify.sh` qui : restaure dans un environnement isolé (stack avec des ports différents), lance `docker compose exec db psql -U onyx -c "SELECT count(*) FROM users"`, vérifie l'intégrité OpenSearch via son API (`/_cluster/health?wait_for_status=yellow`), et confirme que `audit_log.verify_chain()` retourne `ok: true` sur la base restaurée.
2. Planifier une **répétition de restauration mensuelle** en cible de make (`make restore-drill`).
3. Vérifier le nom de projet Compose (`COMPOSE_PROJECT_NAME=onix` dans `.env`) est stable et documenté dans le RUNBOOK.
4. Archiver les sauvegardes hors machine (montage NAS, copie distante chiffrée) — une sauvegarde locale ne protège pas contre la perte physique de la machine.

**Warning signs:**
- Aucune restauration n'a jamais été testée depuis la création du script.
- Le répertoire `backups/` n'existe pas ou est vide.
- `COMPOSE_PROJECT_NAME` n'est pas fixé dans `.env` (risque de nom de projet auto-dérivé différent selon l'utilisateur).
- Les archives ont une taille anormalement petite (volume mal monté = tar d'un répertoire vide).

**Phase to address:**
Phase "Fiabilisation opérationnelle mono-poste" — avant le go-live, critère de sortie : restoration drill réussie et documentée.

---

### Pitfall 2: Credentials par défaut persistant en mode production

**What goes wrong:**
`docker-compose.yml` base contient `POSTGRES_PASSWORD=password`, `MINIO_ROOT_PASSWORD=minioadmin`, Redis sans auth. La protection existante (`${VAR:?error}` dans `docker-compose.prod-local.yml`) force la déclaration dans `.env`, mais elle ne vérifie pas que la valeur fournie n'est pas la valeur par défaut. Si un opérateur copie le `.env.example` sans exécuter `make secrets`, les secrets par défaut passent la validation `${VAR:?error}` (la variable est définie, non vide) et la stack démarre en production avec des credentials publiquement connus.

**Why it happens:**
`gen-secrets.sh` génère des secrets forts, mais rien n'empêche de court-circuiter l'étape. Le `Makefile` documente `make secrets` mais n'impose pas l'exécution. Le préflight check (`make preflight-local`) n'inclut pas de validation de la force des secrets.

**How to avoid:**
Implémenter la garde anti-credentials par défaut identifiée dans les requirements actifs : au démarrage du container ou dans le preflight, vérifier que `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD`, `SECRET_KEY`, et `ONIX_ACTIONS_AUDIT_HMAC_KEY` ne correspondent pas à une liste de valeurs bannies (`password`, `minioadmin`, `secret`, `changeme`, chaîne vide). Bloquer le démarrage si la vérification échoue (`exit 1` dans un entrypoint script ou healthcheck de type guard).

Concrètement : ajouter dans `scripts/preflight.sh` (ou équivalent) :
```bash
BANNED="password minioadmin secret changeme"
for VAR in POSTGRES_PASSWORD MINIO_ROOT_PASSWORD SECRET_KEY; do
  VAL="${!VAR:-}"
  for B in $BANNED; do
    [ "$VAL" = "$B" ] && { echo "FATAL: $VAR est une valeur par défaut interdite"; exit 1; }
  done
done
```

**Warning signs:**
- `.env` présent mais `gen-secrets.sh` n'a jamais été exécuté (vérifiable par `grep -c "minioadmin" .env`).
- `make preflight-local` passe sans vérification des valeurs de secrets.
- `POSTGRES_PASSWORD` ou `MINIO_ROOT_PASSWORD` apparaissent dans des logs de démarrage (si Compose les trace).

**Phase to address:**
Phase "Durcissement et preuves de sécurité" — critère bloquant go-live.

---

### Pitfall 3: Fenêtre de staleness ACL entre cycles de sync — accès résiduel après révocation

**What goes wrong:**
Le module `graph_acl.py` synchronise les ACL SharePoint avec un intervalle configurable (`GATEWAY_DOC_ACL_REFRESH_SECONDS`, défaut 3600s). Si un utilisateur est révoqué sur SharePoint à 09h00 et que la sync suivante est à 10h00, il peut continuer à recevoir des citations de documents confidentiels pendant 60 minutes. Pour des données de santé ou de prévoyance, cette fenêtre est une violation RGPD potentielle. Le risque est aggravé si la sync échoue silencieusement (Graph API down, token expiré) : la fenêtre s'étend indéfiniment.

**Why it happens:**
C'est une limitation architecturale documentée (CONCERNS.md). Le poll est le seul mécanisme implémenté. En FOSS, perm-sync temps-réel = EE uniquement. L'absence d'alerte sur échec de sync fait que l'opérateur ne sait pas que l'ACL est périmée.

**How to avoid:**
1. Réduire le défaut de `GATEWAY_DOC_ACL_REFRESH_SECONDS` à 300s (5 min) pour le déploiement GEREP.
2. Implémenter une alerte Prometheus/Loki sur l'échec de sync ACL (un counter `onix_acl_sync_failures_total` avec alerte si > 0 sur 10 min).
3. Documenter la fenêtre résiduelle maximale dans le dossier de preuves sécurité (modèle de menace) : "risque résiduel accepté = accès résiduel max 5 min après révocation SharePoint en cas de fonctionnement normal ; en cas d'échec de sync : alerte sous 10 min et gel conservatoire de l'ACL".
4. En cas d'échec de sync persistant : politique fail-secure = conserver l'ACL précédente (déjà le cas) mais déclencher une alerte immédiate.
5. L'intégration de webhooks SharePoint (push notifications sur changement de permission) reste la solution robuste mais hors scope go-live immédiat.

**Warning signs:**
- `GATEWAY_DOC_ACL_REFRESH_SECONDS` non défini ou > 600 en prod.
- Aucune alerte sur échec de sync ACL dans les règles Prometheus.
- Les tests d'intégration ACL utilisent uniquement le mock Graph (HIGH risk selon CONCERNS.md).
- Aucun test avec révocation puis vérification que le résultat suivant la sync est bien filtré.

**Phase to address:**
Phase "Prouver la sécurité pour le go-live" — HIGH priority (chemin sécurité critique).

---

### Pitfall 4: Évaluation des guardrails biaisée vers les petits modèles Ollama

**What goes wrong:**
Les 21/21 tests red-team passent sur `qwen2.5:7b`. Si le modèle de production est changé (ou si on passe à un modèle plus grand/différent), le profil d'attaque change. Des jailbreaks qui échouent sur `qwen2.5:7b` (modèle instruction-tuned restrictif) peuvent réussir sur Mistral, Llama 3 70B, ou d'autres modèles avec des refus moins agressifs. Comme les guardrails `access-gateway/app/guardrail.py` sont déterministes (hors LLM) et testés contre des patterns connus, une nouvelle catégorie d'attaque ou une variation de prompt peut contourner la détection.

**Why it happens:**
Le red-team a été construit autour du modèle disponible au moment du développement. Les patterns de jailbreak évoluent rapidement. La contrainte de souveraineté (inférence 100% locale) limite le nombre de modèles testables sans investissement GPU/temps significatif.

**How to avoid:**
1. Avant chaque changement de modèle LLM : re-lancer la suite complète `tests/rag/` en mode live avec le nouveau modèle et valider 21/21 avant de promouvoir en production.
2. Ajouter au RUNBOOK une section "changement de modèle" listant les pré-requis : rag-eval-ci vert + red-team vert + validation manuelle d'un échantillon.
3. Compléter la suite red-team avec des catégories de vecteurs récents (indirect injection via document, multi-turn attacks, character roleplay).
4. Documenter explicitement l'hypothèse : "les guardrails sont heuristiques, non cryptographiques — ils réduisent le risque sans l'éliminer" dans le dossier de preuves sécurité.

**Warning signs:**
- Changement de `ONIX_LLM_MODEL` dans `.env` sans re-validation de la suite red-team.
- `make rag-test` passe (tests hors-LLM contractuels) mais `make rag-test-live` n'a pas été re-exécuté avec le nouveau modèle.
- La suite red-team couvre moins de 30 vecteurs d'attaque distincts (trop peu pour valider un nouveau modèle).

**Phase to address:**
Phase "Prouver la sécurité pour le go-live" — critère de sortie : rag-eval-ci vert sur le modèle de production cible.

---

### Pitfall 5: CVE drift cassant le gate pip-audit

**What goes wrong:**
`pip-audit --strict` est un bloquant CI. `cryptography==46.0.7` porte GHSA-537c-gmf6-5ccf (CVSS 7.5), `pypdf==6.10.2` porte des DoS. Si ces pins ne sont pas mis à jour avant le go-live, soit le gate est cassé (bloque la release), soit — pire — quelqu'un monte temporairement un `--ignore-vuln` pour débloquer et oublie de le retirer. Le risque secondaire est la compatibilité : upgrader `cryptography` 46→48 peut casser des modules qui compilent contre son API C native.

**Why it happens:**
Les dépendances sont fixées par pin dans les requirements des sous-projets. Onyx upstream fixe aussi ses pins indépendamment. Un upgrade `cryptography` dans `onix` peut entrer en conflit avec le pin Onyx upstream si les deux sont dans le même venv/image.

**How to avoid:**
1. Upgrader `cryptography` → `>=48.0.1` et `pypdf` → `>=6.12.0` dans les requirements concernés, puis vérifier la compatibilité avec Onyx upstream (l'image `onyxdotapp/onyx-backend:4.1.1` embarque ses propres pins — aucun conflit possible car c'est un conteneur séparé).
2. Pour les dépendances de `access-gateway/` et `actions/` : ces services ont leurs propres images Docker et requirements — upgrade direct sans risque de conflit avec l'image Onyx.
3. Ajouter un job CI nightly `pip-audit --strict` indépendant du gate de release, avec notification sur première CVE détectée.
4. Ne jamais utiliser `--ignore-vuln` sans une issue de tracking et une date d'expiration.

**Warning signs:**
- `make test` échoue sur `pip-audit --strict` en CI.
- `cryptography<48` ou `pypdf<6.12` dans un requirements.txt.
- Un `# noqa` ou `--ignore-vuln` apparu récemment sans commentaire de justification.

**Phase to address:**
Phase "Remédiation CVE" — prérequis bloquant avant go-live (gate CI doit être vert).

---

### Pitfall 6: Observabilité qui fuit des PII dans les labels de métriques / explosion de cardinalité

**What goes wrong:**
Prometheus collecte des métriques depuis `access-gateway/metrics.py` et `actions`. Si un label de métrique utilise un identifiant utilisateur, un nom de document, ou un paramètre de requête dynamique comme valeur (plutôt qu'un template de route), deux problèmes surviennent : (1) explosion de cardinalité (des millions de time-series distinctes → OOM sur Prometheus sur machine unique), et (2) fuite PII dans le backend de métriques, qui est souvent moins sécurisé que les logs et ne dispose pas de politique de rétention RGPD.

**Why it happens:**
L'anti-pattern est documenté dans ARCHITECTURE.md mais facile à réintroduire lors d'ajout de métriques (un `path=request.url.path` au lieu de `path=route_template`). En mode debug ou monitoring ponctuel, des développeurs ajoutent des labels fins "pour voir", oublient de les retirer.

**How to avoid:**
1. Règle non-négociable : les labels Prometheus ne peuvent contenir que des valeurs issues d'un ensemble fini et connu à l'avance (codes HTTP, noms d'endpoints normalisés, noms de services). Jamais de valeur dynamique libre.
2. Dans `access-gateway/app/metrics.py` : utiliser le template de route FastAPI (`request.scope["route"].path`) et non `request.url.path`.
3. Ajouter un test unitaire de cardinalité : après 100 requêtes synthétiques avec des UPN/documents distincts, vérifier que `REGISTRY.get_sample_value(...)` ne retourne pas plus de N time-series.
4. Configurer Prometheus avec `metric_relabel_configs` pour dropper tout label contenant un pattern de PII (email, NIR, IBAN).
5. Loki : utiliser des labels structurés (`level`, `service`, `component`) — jamais de `user_id` ou `document_id` dans les labels Loki (à mettre en champ de log, pas en index label).

**Warning signs:**
- Prometheus consomme > 2 GB RAM sur machine unique avec < 100 utilisateurs actifs.
- `curl -s http://localhost:9090/api/v1/label/__name__/values | jq '.data | length'` retourne des milliers de métriques.
- Un label comme `user`, `document`, `query`, ou `path` contient des valeurs avec des slashes, des UUID, ou des emails dans les métriques exposées.
- Grafana timeout sur les dashboards.

**Phase to address:**
Phase "Observabilité opérationnelle" — design des métriques à valider avant activation Prometheus/Loki.

---

### Pitfall 7: OpenSearch vm.max_map_count insuffisant + durabilité single-node

**What goes wrong:**
OpenSearch requiert `vm.max_map_count >= 262144` sur l'hôte. En Docker Compose, si ce paramètre kernel n'est pas positionné, OpenSearch échoue au démarrage avec un message d'erreur peu clair, ou démarre mais OOM-kill lors du premier index rebuild. Sur machine unique, le noeud OpenSearch est SPOF : aucune réplique de shard (`numberOfReplicas: 0` par défaut en single-node), donc une corruption de l'index (mauvais arrêt, disque plein) = perte totale du corpus vectoriel.

**Why it happens:**
`make preflight-local` devrait vérifier ce paramètre mais la vérification peut manquer ou être non-bloquante. `vm.max_map_count` est un paramètre kernel global de la machine hôte, non configurable dans Docker. En production `restart:always` résout les crashes transitoires mais pas la corruption d'index.

**How to avoid:**
1. Dans `scripts/preflight.sh` : `[ "$(sysctl -n vm.max_map_count)" -ge 262144 ] || { echo "FATAL: vm.max_map_count insuffisant"; exit 1; }` — erreur bloquante, pas un warning.
2. Dans `deploy/local-prod/` (unit systemd) : ajouter `ExecStartPre=/sbin/sysctl -w vm.max_map_count=262144` avant `docker compose up`.
3. Planifier des sauvegardes OpenSearch via le volume `opensearch-data` (déjà dans `backup.sh`) et tester la restauration (voir Pitfall 1).
4. Documenter la limitation single-node dans le dossier de preuves : "en cas de corruption index, délai de réindexation = [X heures selon corpus]" — cette information est nécessaire pour le PCA/RPO.
5. Activer le `_cat/shards` health check dans `make verify` pour détecter les shards UNASSIGNED immédiatement après démarrage.

**Warning signs:**
- OpenSearch ne démarre pas ou démarre puis crashe sous charge.
- `docker logs onix-opensearch-1 | grep "max virtual memory areas"` retourne des lignes.
- `vm.max_map_count` < 262144 sur l'hôte : `sysctl vm.max_map_count`.
- `GET /_cluster/health` retourne `status: red`.
- Index rebuild en cours depuis > 30 min sans progression.

**Phase to address:**
Phase "Chemin de production mono-poste fiable" — preflight bloquant obligatoire.

---

### Pitfall 8: Audit trail présent mais non démontrablement tamper-evident

**What goes wrong:**
`audit_log.py` implémente le chaînage HMAC correctement. Mais si `ONIX_ACTIONS_AUDIT_HMAC_KEY` est absent au démarrage, le code se replie sur SHA-256 sans clé — comportement documenté dans le code mais potentiellement invisible pour un auditeur. Un attaquant qui a accès à la base de données SQLite/Postgres peut rejouer la chaîne SHA-256 avec n'importe quels enregistrements (sans clé secrète, l'algorithme est public). La chaîne est intègre mais pas tamper-proof. Par ailleurs, si la clé change (rotation), les entrées écrites avec l'ancienne clé échoueront à `verify_chain()` si le champ `algo` n'est pas correctement pris en compte.

De plus, l'audit trail couvre les actions de `onix-actions`, mais les requêtes chat qui passent par `access-gateway` sont loguées dans un fichier de log séparé (ou stdout) — pas dans la même chaîne HMAC. Un auditeur peut demander "qui a posé cette question à 14h37 ?" et la réponse sera dans les logs Loki, pas dans la chaîne HMAC.

**Why it happens:**
La clé HMAC est optionnelle pour faciliter le développement. Le dual-path (HMAC vs SHA-256) est une régression silencieuse en production si la clé n'est pas configurée. La séparation gateway/actions est une contrainte architecturale (deux microservices distincts).

**How to avoid:**
1. `ONIX_ACTIONS_AUDIT_HMAC_KEY` doit être obligatoire en mode production : ajouter dans le preflight check une validation que la variable est non-vide et d'une longueur minimale (>= 32 chars).
2. Ajouter un endpoint d'auto-vérification `/admin/audit/integrity` (déjà partiellement implémenté via `verify_chain()`) et l'exposer dans `make verify` — le résultat `ok: true / count: N / head_hash: ...` doit être tracé et archivé.
3. Dans le dossier de preuves sécurité : documenter explicitement la portée de l'audit trail (actions onix-actions = chaîne HMAC ; accès RAG gateway = logs Loki pseudonymisés) et la politique de rétention/accès à ces logs.
4. Pour la démonstration à un auditeur : maintenir un script `make audit-verify` qui : appelle `verify_chain()`, retourne le résultat, et compare `head_hash` à la valeur archivée la veille. Tout écart = alerte.
5. FOSS vs EE : Onyx FOSS n'a aucun audit trail natif — la couche onix compense entièrement. Documenter cette distinction pour l'auditeur.

**Warning signs:**
- `ONIX_ACTIONS_AUDIT_HMAC_KEY` absent du `.env` de production.
- Les logs `onix.actions.audit` contiennent le message "repli SHA-256" au démarrage.
- `verify_chain()` n'est pas appelé dans le pipeline de santé (`make verify`).
- Aucun script ou runbook ne documente comment un auditeur peut vérifier l'intégrité de l'audit trail.

**Phase to address:**
Phase "Dossier de preuve sécurité" — critère de sortie : `make audit-verify` vert + démonstration documentée.

---

### Pitfall 9: Lacunes RGPD — effacement incomplet et rétention non appliquée automatiquement

**What goes wrong:**
L'endpoint `/erasure` de `onix-actions` efface les données utilisateur dans la base actions. Mais les données PII d'un utilisateur Onyx sont stockées dans Postgres (email, nom, historique de chat). La suppression Onyx FOSS est cassée (contraintes FK NOT NULL, documenté dans CONCERNS.md). Si l'endpoint `/erasure` onix n'est pas chaîné à la suppression Onyx, il reste des traces PII dans la base Onyx principale. Par ailleurs, la rétention des données de chat n'est pas appliquée automatiquement : si un utilisateur a des échanges contenant des PII de clients assurance, ils peuvent persister indéfiniment sans politique de purge active.

**Why it happens:**
Le `/erasure` de onix-actions couvre le périmètre actions (audit log, tâches, usage). Le périmètre Onyx (chat history, user accounts) requiert une intervention séparée sur Postgres Onyx — soit via l'API admin Onyx, soit directement en SQL. La coordination entre les deux n'est pas automatisée. La rétention n'est pas configurée dans le compose de base (aucun job cron de purge).

**How to avoid:**
1. Documenter (dans `docs/RGPD.md`) le processus complet d'effacement Art.17 : étapes manuelles sur la base Onyx (SQL ou API admin) + endpoint `/erasure` onix-actions + vérification dans les deux bases.
2. Créer un script `scripts/erasure.sh <user_email>` qui orchestre les deux étapes et log l'exécution (avec timestamp, user visé, exécutant) dans l'audit trail.
3. Pour la rétention : configurer un job cron (crontab ou systemd timer) appelant l'API admin Onyx pour archiver/supprimer les chats > N mois. Documenter la durée de rétention retenue et son fondement légal dans le registre de traitements.
4. Vérifier que le MinIO (fichiers indexés) contient également des métadonnées permettant de tracer et supprimer les documents d'un utilisateur donné.
5. FOSS vs EE : la suppression cassée est un bug upstream Onyx non corrigé en FOSS. Contournement obligatoire = script manuel SQL ou `/erasure` étendu + tests de non-régression.

**Warning signs:**
- Aucun script d'effacement documenté pour une demande Art.17 RGPD.
- La base Postgres Onyx contient des lignes `users` avec email en clair après un effacement supposé complet.
- Aucune politique de rétention configurée (les tables `chat_message`, `chat_session` croissent indéfiniment).
- Le registre de traitements ne mentionne pas la durée de rétention des échanges RAG.

**Phase to address:**
Phase "RGPD et rétention" — obligatoire avant go-live (données de santé + prévoyance).

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Actions SQLite en développement, Postgres en prod sans test d'intégration Postgres | Simplicité locale | Bug de sérialisation ou de migration silencieux uniquement visible en prod | Jamais en production — ajouter un test CI sur Postgres |
| `--ignore-vuln` sur pip-audit | Débloquer la release | CVE oubliée indéfiniment, gate sans valeur | Jamais sans issue trackée + date d'expiration |
| `ONIX_ACTIONS_AUDIT_HMAC_KEY` absente = repli SHA-256 | Facilité de dev | Audit trail non tamper-proof en production | Développement local uniquement |
| Sync ACL par poll (3600s défaut) | Simplicité d'implémentation | Fenêtre de résiduel accès 1h après révocation | Uniquement si durée documentée et alertée |
| Sauvegardes locales uniquement (pas de copie distante) | Zéro config réseau | Perte totale sur vol/incendie machine | Jamais pour données RGPD |
| Red-team sur modèle unique (qwen2.5:7b) | Couverture rapide | Garantie non transférable aux autres modèles | Acceptable seulement si le modèle de prod = le modèle testé |

---

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Microsoft Graph ACL sync | Token expiré silencieusement → sync ne s'exécute plus, ancienne ACL reconduite indéfiniment | Ajouter alerte sur `onix_acl_sync_failures_total > 0` + log explicite de l'erreur d'authentification Graph |
| Ollama service name | Accès via `localhost:11434` depuis un autre conteneur | Utiliser le nom de service interne Docker : `http://onix-ollama-1:11434` (ou le service name Compose configuré) — voir règle AGENTS.md §7 |
| OpenSearch restart | Données écrites en cours de flush → corruption partielle si kill -9 | Utiliser `docker compose stop` (SIGTERM + attente) jamais `docker compose kill` sans grace period |
| Postgres sauvegarde | `pg_dump` sur un volume live sans arrêt = snapshot potentiellement incohérent | `backup.sh` arrête la stack avant l'archivage — ne pas dériver vers une sauvegarde à chaud sans WAL archiving |
| SharePoint connector + FOSS | Perm-sync EE : les permissions SharePoint ne s'appliquent PAS à l'indexation | Le cloisonnement est assuré par le filtre de sortie gateway — ne jamais supposer qu'Onyx FOSS filtre à l'indexation |
| Entra ID OIDC groups claim | Groupes non inclus dans le token si `groupMembershipClaims` n'est pas configuré dans l'app registration | Vérifier que les claims groups sont présents dans le JWT avant de déployer — sinon tous les utilisateurs arrivent sans groupes → 403 systématique |

---

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Redis sans persistence (`appendonly: no`) | Tâches Celery perdues au redémarrage Redis (planifiées non exécutées, locks perdus) | Activer `appendonly yes` + `save "60 1000"` en prod | Premier redémarrage Redis en production |
| OpenSearch single shard sans réplique | Toute requête ralentit proportionnellement à la taille du corpus — pas de parallélisme cross-shard | Pour > 10M chunks : augmenter le shard count (rebuild index) | ~10–50M chunks selon RAM |
| Cache mémoire LRU gateway sans Redis | Cache perdu au redémarrage du gateway → spike LLM (toutes les requêtes miss) | Configurer Redis comme backend cache gateway pour persistance inter-redémarrage | Premier redémarrage gateway en production avec trafic |
| `vm.max_map_count` insuffisant | OpenSearch démarre puis OOM-kill sous charge | Bloquer le preflight si valeur < 262144 | Premier index rebuild ou ingestion massive |
| MinIO local sur même disque que les logs | Disque plein → MinIO writes échouent → indexation bloquée | Séparer les partitions (ou alerte disque < 20%) | Indexation de gros corpus (> quelques dizaines de GB) |

---

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| `ENCRYPTION_KEY_SECRET` absent en production | Secrets connecteurs (OAuth tokens, API keys) stockés en clair dans Postgres Onyx FOSS | Obligatoire dans `.env` ; vérifier dans preflight ; documenter comme non-optionnel |
| Métriques Prometheus exposées sans authentification | Fuite de volumétrie opérationnelle (nombre de requêtes par endpoint, taux d'erreur, charge) | Configurer BasicAuth ou réseau interne uniquement sur le port Prometheus/Grafana |
| Logs Loki contenant des queries RAG en clair | PII (nom de client, numéro de contrat) dans les logs → violation RGPD | Activer la redaction PII dans promtail avant ingestion Loki ; ne logger que les métadonnées (longueur, endpoint, status) |
| Accès admin Onyx sans MFA | Admin UI Onyx expose la gestion des connecteurs (credentials SharePoint) | Onyx FOSS : pas de MFA natif sur l'UI admin — mitiger par réseau (accès UI admin uniquement depuis l'hôte local ou VPN) |
| Audit trail exporté sans chiffrement | Export de l'audit log SQLite = PII des UPN hashés + timestamps d'accès | Chiffrer les exports d'audit avant transmission à l'auditeur ; ne jamais envoyer par email non chiffré |
| `SSRF_PROTECTION_LEVEL` != `VALIDATE_ALL` | Web connector peut atteindre `169.254.169.254` (IMDS) ou réseau interne | Ne jamais dégrader ce niveau en production ; documenter que seule la valeur `VALIDATE_ALL` est autorisée |

---

## "Looks Done But Isn't" Checklist

- [ ] **Backup :** script `backup.sh` existe → vérifier que `restore-drill` a été exécuté et documenté avec résultat OK.
- [ ] **Secrets forts :** `.env` contient `POSTGRES_PASSWORD` → vérifier que la valeur n'est pas `password`, `minioadmin`, ou la valeur du `.env.example`.
- [ ] **Audit HMAC :** `audit_log.py` est déployé → vérifier que `ONIX_ACTIONS_AUDIT_HMAC_KEY` est présent et que les logs ne contiennent pas "repli SHA-256".
- [ ] **ACL sync :** `graph_acl.py` tourne → vérifier que le refresh interval est ≤ 300s et qu'une alerte sur échec de sync est active.
- [ ] **Red-team vert :** `make rag-test` passe (contrats) → vérifier que `make rag-test-live` avec le modèle de production cible a aussi passé.
- [ ] **CVE gate vert :** `pip-audit --strict` ne retourne pas 0 CVE → vérifier que `cryptography >= 48` et `pypdf >= 6.12` sont pinned.
- [ ] **OpenSearch healthy :** `docker compose ps` montre les containers up → vérifier `GET /_cluster/health` retourne `status: green` ou `yellow` (pas `red`).
- [ ] **vm.max_map_count :** `make preflight-local` passe → vérifier que `sysctl vm.max_map_count` retourne `>= 262144` sur l'hôte.
- [ ] **Effacement RGPD :** `/erasure` endpoint existe → vérifier que le processus complet (actions + Postgres Onyx) est documenté et testé.
- [ ] **Métriques propres :** Prometheus actif → vérifier que les labels ne contiennent pas de valeurs dynamiques libres (query count < 1000 time-series pour < 100 utilisateurs actifs).
- [ ] **ONIX_ACTIONS_AUDIT_HMAC_KEY :** présente dans `.env` → vérifier que `verify_chain()` retourne `ok: true` après 24h de production.
- [ ] **Modèle Ollama disponible :** `make up` passe → vérifier que `make models` a été exécuté et que le modèle est présent localement (sinon première requête timeout).

---

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Sauvegarde corrompue / non restaurable | CRITICAL | Reconstruire depuis les sources SharePoint (re-indexation complète) ; durée : proportionnelle au corpus ; exécuter `make verify` après ; accepter perte de l'historique de chat |
| Credentials par défaut en prod | HIGH | Arrêter immédiatement la stack, regénérer tous les secrets via `gen-secrets.sh`, reconstruire les containers avec les nouvelles variables, auditer les logs d'accès sur la période d'exposition |
| ACL staleness → accès résiduel détecté | MEDIUM | Forcer une sync immédiate (`make sync-doc-acl`) ; documenter l'incident ; évaluer si des données ont été exposées (requêtes dans les logs Loki pendant la fenêtre) ; notifier si exposition confirmée (RGPD art.33) |
| CVE gate cassé | LOW-MEDIUM | Upgrader le package concerné ; si upgrade bloque le build : ouvrir une issue avec justification, appliquer `--ignore-vuln` avec date d'expiration J+30, planifier le fix |
| Audit trail chaîne cassée (`verify_chain` = false) | HIGH | Ne pas modifier la base ; isoler l'intervalle cassé ; vérifier les logs Postgres/SQLite pour mutation externe ; notifier le DPO |
| OpenSearch index corrompu | HIGH | Restaurer depuis la dernière sauvegarde OpenSearch (volume `opensearch-data.tgz`) ; si sauvegarde absente : re-indexation complète depuis MinIO |
| Effacement RGPD incomplet | HIGH | Exécuter le script d'effacement complémentaire manuellement ; documenter l'exécution dans l'audit trail ; si délai Art.17 dépassé, notifier la CNIL |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Sauvegardes non testées | Fiabilisation opérationnelle mono-poste | `make restore-drill` retourne 0 et documente résultat |
| Credentials par défaut | Durcissement et preuves de sécurité | `make preflight-local` rejette `.env` avec valeurs bannies |
| Staleness ACL | Prouver la sécurité pour le go-live | Alerte `onix_acl_sync_failures_total` active + intervalle ≤ 300s en prod |
| Guardrails biaisés | Prouver la sécurité pour le go-live | `make rag-test-live` vert sur le modèle de production cible |
| CVE drift pip-audit | Remédiation CVE (prérequis bloquant) | `pip-audit --strict` retourne 0 CVE |
| PII dans métriques | Observabilité opérationnelle | Test cardinalité < 1000 time-series pour < 100 utilisateurs |
| vm.max_map_count / OpenSearch durabilité | Chemin de production mono-poste fiable | `make preflight-local` bloque si valeur < 262144 |
| Audit trail non démontrable | Dossier de preuve sécurité | `make audit-verify` vert + script de démonstration auditeur documenté |
| RGPD effacement / rétention | RGPD et rétention | Script d'effacement testé + politique de rétention documentée |

---

## Sources

- Audit byte-level `docs/audit-onyx/00-VERDICT.md` (onix repo) — limites FOSS vs EE d'Onyx 4.1.1
- `.planning/codebase/CONCERNS.md` (onix repo, 2026-06-18) — analyse codebase complète
- `scripts/backup.sh` + `scripts/restore.sh` (onix repo) — analyse des scripts de sauvegarde
- `actions/app/audit_log.py` (onix repo) — comportement de dégradation SHA-256 sans clé
- `monitoring/prometheus/rules/onix-alerts.yml` (onix repo) — couverture d'alerte actuelle
- OpenSearch documentation — requirements `vm.max_map_count` (valeur 262144 documentée officiellement)
- RGPD Art.17 (droit à l'effacement), Art.33 (notification de violation) — exigences légales
- OWASP ASVS V7 — intégrité des logs et tamper-evidence requirements

---
*Pitfalls research for: self-hosted regulated RAG mono-poste (Onyx FOSS + onix overlay)*
*Researched: 2026-06-19*
