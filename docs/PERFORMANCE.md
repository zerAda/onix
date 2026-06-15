# Performance — exploiter au mieux la machine (sans la faire tomber)

Objectif : **maximum de débit/qualité** pour le matériel réellement présent, en
gardant une **marge OS** (utiliser 100 % de la RAM → gel / OOM, inacceptable).
Tout est calibré automatiquement par `make tune` ; ce document explique *quoi* et
*pourquoi*, avec les compromis (pas de réglage « magique »).

## 1. Auto-tuning : la voie recommandée

```bash
make tune     # détecte CPU/RAM/GPU et ÉCRIT les valeurs optimales dans .env
make secrets  # (si pas déjà fait)
make up       # + GPU=1 si GPU NVIDIA, + PERF=1 si machine confortable
make verify
```

`make tune` choisit notamment :
- la **réserve OS** (≈ RAM/8, plancher 2 Go) **et** la **baseline Onyx** (services
  toujours actifs hors Ollama, ≈ 7 Go sur 16 Go), réservées **avant** de dimensionner Ollama ;
- le **plus gros modèle qui tient** dans la RAM **restante** (= RAM − OS − baseline), ou la VRAM ;
- les **limites mémoire** proportionnelles à la RAM, dont la **somme reste < RAM physique** (anti-OOM) ;
- les **réglages perf Ollama** ci-dessous.

> `make tune` ne touche **pas** vos secrets ; il ne modifie que les clés de réglage.

## 2. Les réglages Ollama qui comptent vraiment

| Variable | Effet | Compromis |
|---|---|---|
| `OLLAMA_FLASH_ATTENTION=1` | Noyau d'attention optimisé, **−30 à −50 % de mémoire de contexte (KV)**. | **Bénéfice surtout GPU** (noyaux CUDA dédiés) ; en **CPU**, l'effet vitesse est ~**neutre** (gain marginal/nul) mais l'économie de mémoire KV reste utile. Sans risque → laissé à `1`. |
| `OLLAMA_KV_CACHE_TYPE=q8_0` | Quantifie le cache KV → **moitié de RAM/VRAM de contexte** (permet + de contexte ou + gros modèle). | `q8_0` quasi sans perte ; `q4_0` plus agressif. **Actif seulement si Flash Attention = 1.** Le gain de débit est **net sur GPU** ; en **CPU** il est surtout un gain de **mémoire** (vitesse ≈ neutre). |
| `OLLAMA_KEEP_ALIVE=-1` | Modèle **toujours chargé** → zéro latence de rechargement. | Occupe la RAM en permanence. `make tune` ne met `-1` que si **RAM ≥ ~24 Go** ; sinon `5m` (sur 16 Go, épingler un modèle rapproche de l'OOM). |
| `OLLAMA_NUM_PARALLEL` | Requêtes concurrentes par modèle (débit multi-utilisateur). | Chaque requête = un slot KV en plus → coût RAM/VRAM. `2` si RAM **libre** (hors baseline) ≥ ~12 Go. |
| `OLLAMA_MAX_LOADED_MODELS` | Garde chat **et** embeddings chargés ensemble. | +RAM. `2` si RAM **libre** (hors baseline Onyx) ≥ ~12 Go. |

Sources : [Ollama FAQ](https://docs.ollama.com/faq) · [Ollama — Troubleshooting & Performance](https://deepwiki.com/ollama/ollama/6.4-troubleshooting-and-performance) · [KV cache quantization](https://smcleod.net/2024/12/bringing-k/v-context-quantisation-to-ollama/).

> **CPU vs GPU (Flash Attention / KV q8_0)** : ces deux réglages donnent leur
> plein effet de **vitesse sur GPU**. En **CPU pur**, ils sont essentiellement
> **neutres côté débit** ; on les garde activés pour l'**économie de mémoire de
> contexte** (KV), qui permet un contexte plus large à RAM égale, sans perte de
> qualité notable (`q8_0`). Ils ne dégradent pas le CPU → aucun inconvénient.

> Sur **CPU**, le nombre de threads est auto-détecté par Ollama (= cœurs physiques),
> ce qui est optimal : on **ne** force **pas** `num_thread` (le forcer dégrade souvent).

## 3. Choix du modèle (qualité vs vitesse)

`make tune` retient le plus gros modèle qui **tient dans la RAM réellement libre**
(RAM − réserve OS − baseline Onyx ≈ 7 Go), ou dans la **VRAM** en GPU. Le choix
se fait sur cette RAM **libre**, pas sur la RAM brute (sinon risque d'OOM). Seuils
(source de vérité unique = `scripts/detect-hardware.sh`, repris à l'identique par
`detect-hardware.ps1` et `env.template`) :

| Critère (RAM **libre** Ollama, ou VRAM) | Modèle retenu | Note |
|---|---|---|
| GPU VRAM ≥ 24 Go | `qwen2.5:32b-instruct` | Excellente qualité FR. |
| GPU VRAM ≥ 12 Go **ou** CPU RAM libre ≥ 18 Go | `qwen2.5:14b-instruct` | Très bon compromis (CPU : ≈ 32 Go RAM physique). |
| GPU VRAM ≥ 8 Go | `llama3.1:8b` | GPU uniquement (jamais épinglé en CPU). |
| CPU RAM libre 7-17 Go | `qwen2.5:7b-instruct` | ≈ 24 Go RAM physique. |
| CPU RAM libre 4-6 Go | `llama3.2:3b` | **Défaut prudent ≈ 16 Go RAM physique.** |
| CPU RAM libre < 4 Go | `llama3.2:1b` | Postes très contraints. |

> En **CPU**, un modèle plus gros = plus « intelligent » mais **plus lent**
> (tokens/s). Au-delà de 14B en CPU pur, la latence devient inconfortable :
> `make tune` plafonne donc à 14B en CPU (32B reste possible manuellement, GPU).
>
> **Garantie anti-OOM** : `make tune` borne la **somme de toutes les limites
> mémoire** (`*_MEM_LIMIT` + Ollama) à **< RAM physique** (et non chaque limite
> isolément), avec un coussin ≥ 1 Go. Le modèle est aligné sur le plafond Ollama
> final (jamais un modèle dont le poids dépasse `OLLAMA_MEM_LIMIT`).

## 4. Onyx — débit d'indexation

- `make up PERF=1` rétablit un **model-server d'indexation dédié** : l'indexation
  ne se dispute plus les ressources avec l'inférence (≈ +3-5 Go RAM). Recommandé
  par `make tune` si RAM ≥ 32 Go (ou GPU + 24 Go).
- `OPENSEARCH_HEAP` ≈ 20 % de la RAM (plafond 8 Go) ; conteneur = 2× le heap.

## 5. GPU NVIDIA

`make up GPU=1` (nécessite `nvidia-container-toolkit`). Le GPU décuple le débit et
permet de plus gros modèles. Sur **macOS**, Docker n'accède pas au GPU → pour
exploiter Metal, lancer **Ollama en natif** (cf. `RUNBOOK.md` §8). Sur **Windows**,
le GPU passe par le backend **WSL2** de Docker Desktop.

## 6. Hygiène hôte

- Linux : `vm.max_map_count >= 262144` (OpenSearch) — vérifié par `make verify`.
- Disque **SSD/NVMe** fortement recommandé (index OpenSearch + I/O modèles).
- Surveiller en direct : `make stats`. Si un service est bridé, relancer `make tune`
  (RAM ajoutée ?) ou ajuster les `*_MEM_LIMIT` dans `.env`.

## 7. Ce qu'on ne fait PAS (anti-cargo-cult)

- Pas de suppression des **limites mémoire** : elles évitent l'OOM-kill de l'hôte
  (on les dimensionne large, on ne les retire pas).
- Pas de `num_thread` forcé en CPU (l'auto-détection d'Ollama est meilleure).
- Pas de `q4_0` par défaut (perte de qualité) : `q8_0` est le bon compromis.
