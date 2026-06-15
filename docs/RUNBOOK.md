# Runbook — onix — Stack IA souveraine (Onyx + Ollama)

Exploitation, mises à jour, dépannage et montée en charge.

## 1. Cycle de vie

| Tâche | Commande |
|---|---|
| Démarrer (+ modèles) | `make up` |
| Démarrer en GPU NVIDIA | `make up GPU=1` |
| Arrêter | `make down` |
| Redémarrer | `make restart` |
| État | `make ps` |
| Ressources (RAM/CPU live) | `make stats` |
| Journaux | `make logs` (ou `docker compose logs -f <service>`) |
| Vérifier | `make verify` |
| Détruire (⚠ données) | `make destroy` |

## 2. Première mise en route (attendu)

1. `make detect` → reporter les valeurs dans `.env`.
2. `make secrets` → `.env` + secrets.
3. `make up` → démarrage. **Premier lancement long** : OpenSearch s'initialise,
   les model servers téléchargent les modèles d'embedding, Ollama tire les
   modèles. C'est normal. Suivre avec `make logs`.
4. `make verify` → doit finir sur « Stack saine ».
5. Ouvrir `http://localhost:3000` et créer **IMMÉDIATEMENT le compte admin**
   (1er compte = administrateur). ⚠ Ne pas différer : tant qu'aucun compte
   n'existe, le premier inscrit prend l'instance (admin). En accès non-localhost,
   activer aussi `REQUIRE_EMAIL_VERIFICATION` + `VALID_EMAIL_DOMAINS` (SECURITY §6).
6. Assistant LLM : **Ollama**, URL `http://ollama:11434`, modèle recommandé par
   `make detect` (par défaut prudent : `llama3.2:3b` sur ~16 Go).

## 3. Connexion à Ollama (détail)

Onyx (en conteneur) joint Ollama par le **nom de service Docker**, pas par
`localhost` : l'URL est donc **`http://ollama:11434`**. `make verify` teste ce
chemin (`api_server` → `ollama:11434`) et exécute une génération réelle.

Changer de modèle plus tard :
```bash
# éditer OLLAMA_MODELS_TO_PULL dans .env, puis :
make models
# puis dans Onyx : Admin → Language Models → ajouter/choisir le modèle
```

## 4. Mises à jour

```bash
# 1) Lire le changelog Onyx, ajuster IMAGE_TAG dans .env (ex. 4.1.1 → 4.2.0)
# 2) Sauvegarder d'abord
make backup
# 3) Tirer + redémarrer (les migrations Alembic s'appliquent au boot)
make update
make verify
```
Mettre à jour Ollama : ajuster `OLLAMA_IMAGE_TAG` puis `make update`.

## 5. Sauvegarde / restauration

- `make backup` : arrêt bref, archive `db_volume`, `opensearch-data`,
  `minio_data`, `file-system` dans `backups/<horodatage>/`, redémarrage.
  (Les modèles Ollama ne sont pas sauvegardés : re-tirables via `make models`.)
- `make restore DIR=backups/<horodatage>` : restaure (écrase) ces volumes.

## 6. Dépannage

### OpenSearch ne démarre pas / « max virtual memory areas » (Linux)
```bash
sudo sysctl -w vm.max_map_count=262144
echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-onyx.conf
make restart
```

### Conteneur tué (OOM) / machine qui rame
- Lancer `make detect` et appliquer le profil recommandé (modèle plus petit,
  `OPENSEARCH_HEAP` réduit). Sur < 16 Go : `OLLAMA_MODELS_TO_PULL=llama3.2:1b`.
- `make stats` pour repérer le service gourmand ; ajuster les `*_MEM_LIMIT`.
- Ollama décharge le modèle après `OLLAMA_KEEP_ALIVE` (défaut 5 min) → RAM libérée.

### OpenSearch en lecture seule / indexation bloquée (« create-index blocked », disque plein)
Si les logs montrent `index_create_block_exception` ou `read-only-allow-delete`,
le **disque a dépassé 95 %** (watermark flood-stage d'OpenSearch). Libérez de
l'espace (images Docker inutiles : `docker image prune -af`) **puis** levez les
blocs déjà posés :
```bash
SH=.; PASS=$(sed -n 's/^OPENSEARCH_ADMIN_PASSWORD=//p' $SH/.env)
docker compose -f $SH/docker-compose.yml exec -T opensearch \
  curl -sk -u "admin:$PASS" -X PUT "https://localhost:9200/*/_settings?expand_wildcards=all" \
  -H 'Content-Type: application/json' -d '{"index.blocks.read_only_allow_delete":null}'
docker compose -f $SH/docker-compose.yml restart opensearch api_server background
```
Prévoir ~20 Go de disque libre (les images, dont le model-server, sont volumineuses).

### Port 3000 déjà utilisé
Changer `ONYX_HOST_PORT` dans `.env` puis `make up`.

### Le 1er chat est lent
Le modèle se charge en RAM au 1er appel (CPU). Les suivants sont plus rapides
tant que le modèle reste chargé (`OLLAMA_KEEP_ALIVE`).

### `make verify` : api_server ne joint pas ollama
Souvent un démarrage encore en cours. Réessayer après 1–2 min ; vérifier
`docker compose logs ollama` et que le modèle est tiré (`make models`).

## 7. Montée en charge (optionnel)

- **Model server d'indexation dédié** : pour de gros volumes d'indexation,
  rétablir un second `inference_model_server` (`INDEXING_ONLY=True`) et pointer
  `INDEXING_MODEL_SERVER_HOST` dessus dans `.env`. Coût : +RAM.
- **OpenSearch** : augmenter `OPENSEARCH_HEAP` (≈ 50 % de `OPENSEARCH_MEM_LIMIT`).
- **GPU** : `make up GPU=1` + modèle plus capable (`llama3.1:8b`, `qwen2.5:14b`).

## 8. Variante : Ollama NATIF (au lieu du conteneur)

Pertinent surtout sur **macOS Apple Silicon** (le GPU Metal n'est pas accessible
depuis Docker) ou si vous tenez à votre install Ollama existante.

1. Retirer le service `ollama` du `docker-compose.yml` (ou ne pas le démarrer).
2. Sur l'hôte, exposer Ollama au réseau Docker :
   `OLLAMA_HOST=0.0.0.0:11434 ollama serve` (puis **pare-feu** pour ne l'ouvrir
   qu'à l'interface Docker — ne pas laisser 0.0.0.0 ouvert au LAN).
3. Dans Onyx, API Base URL = **`http://host.docker.internal:11434`**
   (les services `api_server`/`background` ont déjà `extra_hosts: host.docker.internal`).

## 9. (Avancé) Réintroduire le code-interpreter

Désactivé par défaut car il monte le socket Docker (**risque root hôte**). Ne le
réactiver que dans un environnement isolé et de confiance, jamais en exposition
réseau. Préférer le mode `docker-in-docker` (privileged) cloisonné si nécessaire.
