#!/usr/bin/env bash
# =============================================================================
# Diagnostic + AUTO-TUNING matériel (Linux / macOS) : CPU, RAM, GPU.
#
#   ./detect-hardware.sh            → RAPPORT (lecture seule) + valeurs conseillées
#   ./detect-hardware.sh --apply    → ÉCRIT les valeurs optimales dans .env  (= make tune)
#
# Objectif : exploiter au mieux la machine (gros modèle qui tient, limites
# proportionnelles à la RAM, réglages perf Ollama) TOUT en gardant une marge OS
# (jamais 100 % → sinon gel/OOM). Les secrets ne sont pas touchés.
#
# Pourquoi ? L'assistant ne peut pas inspecter votre poste depuis son sandbox
# cloud : exécutez ceci SUR la machine cible.
# =============================================================================
set -uo pipefail
cd "$(dirname "$0")/.."

APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
ENV_FILE=".env"; TEMPLATE="env.template"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
line() { printf -- '----------------------------------------------------------------\n'; }
clamp() { v=$1; [ "$v" -lt "$2" ] && v=$2; [ "$v" -gt "$3" ] && v=$3; echo "$v"; }

# ---- Détection --------------------------------------------------------------
OS="$(uname -s)"; ARCH="$(uname -m)"
CPU_MODEL="inconnu"; CORES=1; RAM_GB=0
GPU_KIND="none"; GPU_NAME=""; VRAM_GB=0; DOCKER_GPU="non"

case "$OS" in
  Linux)
    CORES="$(nproc 2>/dev/null || echo 1)"
    CPU_MODEL="$(sed -n 's/^model name[[:space:]]*: //p' /proc/cpuinfo 2>/dev/null | head -n1)"
    [ -z "$CPU_MODEL" ] && CPU_MODEL="$(uname -p)"
    kb="$(sed -n 's/^MemTotal:[[:space:]]*\([0-9]*\).*/\1/p' /proc/meminfo 2>/dev/null)"
    # MemTotal < RAM nominale (le noyau réserve une part) : on ARRONDIT au Go le
    # plus proche pour refléter la capacité physique réelle (16075 Mo → 16 Go).
    [ -n "$kb" ] && RAM_GB=$(( (kb + 512 * 1024) / 1024 / 1024 )) ;;
  Darwin)
    CORES="$(sysctl -n hw.ncpu 2>/dev/null || echo 1)"
    CPU_MODEL="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo Apple)"
    bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
    RAM_GB=$(( (bytes + 512 * 1024 * 1024) / 1024 / 1024 / 1024 )) ;;
  *)
    # OS non géré (ex. MINGW/Git Bash) : on N'AVORTE QUE si la RAM n'est PAS
    # forcée. Avec ONIX_FORCE_RAM_GB on calcule pour une machine CIBLE depuis
    # n'importe quel OS (dimensionnement à distance + tests autonomes du calcul).
    if ! { case "${ONIX_FORCE_RAM_GB:-}" in ''|*[!0-9]*) false ;; *) [ "${ONIX_FORCE_RAM_GB:-0}" -ge 1 ] ;; esac; }; then
      echo "OS non géré ($OS). Sous Windows : detect-hardware.ps1"; exit 1
    fi
    CPU_MODEL="cible forcée (ONIX_FORCE_*)" ;;
esac
[ "$RAM_GB" -lt 1 ] && RAM_GB=1; [ "$CORES" -lt 1 ] && CORES=1

# ---- Surcharges de TEST / dimensionnement à distance ------------------------
# Permettent de calculer le profil pour une machine CIBLE depuis une autre (ou
# de tester la logique sans toucher au vrai matériel). Fail-closed : une valeur
# non entière > 0 est IGNORÉE (on garde la détection réelle) plutôt qu'avalée.
# Non utilisés en exploitation normale (variables absentes = détection native).
is_pos_int() { case "$1" in ''|*[!0-9]*) return 1 ;; *) [ "$1" -ge 1 ] ;; esac; }
is_pos_int "${ONIX_FORCE_RAM_GB:-}"  && RAM_GB="$ONIX_FORCE_RAM_GB"
is_pos_int "${ONIX_FORCE_CORES:-}"   && CORES="$ONIX_FORCE_CORES"

if [ "${ONIX_FORCE_GPU:-}" = "none" ]; then
  GPU_KIND="none"; GPU_NAME=""; VRAM_GB=0
elif [ "${ONIX_FORCE_GPU:-}" = "nvidia" ]; then
  GPU_KIND="nvidia"; GPU_NAME="NVIDIA (forcé ONIX_FORCE_GPU)"
  is_pos_int "${ONIX_FORCE_VRAM_GB:-}" && VRAM_GB="$ONIX_FORCE_VRAM_GB"
elif command -v nvidia-smi >/dev/null 2>&1; then
  GPU_KIND="nvidia"
  GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n1)"
  vram_mb="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -n1)"
  [ -n "${vram_mb:-}" ] && VRAM_GB=$(( vram_mb / 1024 ))
elif [ "$OS" = "Darwin" ] && [ "$ARCH" = "arm64" ]; then
  GPU_KIND="apple"; GPU_NAME="Apple Silicon (GPU Metal — inaccessible depuis Docker)"
elif command -v lspci >/dev/null 2>&1 && lspci 2>/dev/null | grep -qiE 'amd/ati|radeon'; then
  GPU_KIND="amd"; GPU_NAME="$(lspci 2>/dev/null | grep -iE 'vga|3d|display' | grep -iE 'amd|radeon' | head -n1 | cut -d: -f3-)"
fi
# `docker info` purement INFORMATIF (champ DOCKER_GPU) : sans effet sur le profil
# calculé. On le SAUTE si le matériel est forcé (dimensionnement à distance/tests)
# ou via ONIX_SKIP_DOCKER=1 — évite un appel lent/bloquant au démon Docker.
if [ -z "${ONIX_FORCE_GPU:-}" ] && [ "${ONIX_SKIP_DOCKER:-0}" != "1" ] \
   && command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  if [ "$GPU_KIND" = nvidia ] && docker info 2>/dev/null | grep -qi 'nvidia'; then DOCKER_GPU="oui"
  elif [ "$GPU_KIND" = nvidia ] && command -v nvidia-ctk >/dev/null 2>&1; then DOCKER_GPU="probable (nvidia-ctk présent)"; fi
fi

# ---- Calcul du profil optimal -----------------------------------------------
# Principe anti-OOM (corrige la sur-allocation initiale) :
#   1. On RÉSERVE d'abord la marge OS + la BASELINE Onyx « régime établi »
#      (services toujours actifs hors Ollama : OpenSearch, model-server, api,
#      background, web, pg, minio, redis, nginx). Ollama ne reçoit QUE le reste.
#   2. La SOMME de TOUTES les limites mémoire (`*_MEM_LIMIT` + Ollama) est bornée
#      à < RAM physique — pas chaque limite isolément. Une boucle d'ajustement
#      rogne le profil (services d'abord) tant que la somme ne laisse pas ≥ 1 Go
#      de coussin, puis le modèle est aligné sur le plafond Ollama final.
# Calculs en Mo (limites en Mo → conversion finale en "g"/"m" pour Docker).
GB=1024
RES=$(clamp $(( RAM_GB / 8 )) 2 8)          # marge OS en Go (jamais < 2 Go)
RES_MB=$(( RES * GB ))
RAM_MB=$(( RAM_GB * GB ))
USE_GPU=0
[ "$GPU_KIND" = nvidia ] && [ "$VRAM_GB" -ge 6 ] && USE_GPU=1

# --- Limites mémoire des services Onyx (hors Ollama), en Mo ------------------
# Plafonds Docker lean, proportionnels à la RAM, alignés sur des multiples de
# 256 Mo (lisibilité). Planchers de fonctionnement bas (les petits services
# n'ont pas besoin du Go) ; plafonds hauts modérés. Sur machine contrainte
# (< 16 Go, sous le minimum recommandé), les planchers sont réduits pour que la
# somme tienne TOUJOURS sous la RAM (jamais de sur-allocation, même dégradée).
snap() { echo $(( ($1 / 256) * 256 )); }            # arrondi inférieur à 256 Mo
LOW=0; [ "$RAM_GB" -lt 16 ] && LOW=1
OS_FLOOR=$([ "$LOW" = 1 ] && echo $((GB + GB/2)) || echo $((2*GB)))   # 1.5g / 2g
BIG_FLOOR=$([ "$LOW" = 1 ] && echo $GB || echo $((2*GB)))            # 1g / 2g (infer, bg)
API_FLOOR=$([ "$LOW" = 1 ] && echo $((GB/2)) || echo $GB)            # 512m / 1g
HEAP=$(clamp $(( RAM_GB * 12 / 100 )) 1 8)          # heap JVM OpenSearch en Go (~12%)
OS_MEM=$(clamp $(( HEAP * GB * 3 / 2 )) "$OS_FLOOR" $((12*GB)))            # conteneur ≈ 1.5× heap
INFER_MEM=$(clamp "$(snap $(( RAM_MB * 15 / 100 )))" "$BIG_FLOOR" $((6*GB))) # model-server
API_MEM=$(clamp "$(snap $(( RAM_MB * 10 / 100 )))" "$API_FLOOR" $((6*GB)))   # api_server
BG_MEM=$(clamp "$(snap $(( RAM_MB * 15 / 100 )))" "$BIG_FLOOR" $((8*GB)))    # background (indexation)
WEB_MEM=$([ "$LOW" = 1 ] && echo 512 || echo 1024)  # frontend Next.js
PG_MEM=$([ "$LOW" = 1 ] && echo 512 || echo 1024)   # Postgres
MINIO_MEM=512                                        # MinIO (512 Mo)
NGINX_MEM=256                                        # nginx (256 Mo)
REDIS_MEM=256                                        # redis (256 Mo ; tmpfs/save off)

# BASELINE Onyx « régime établi » : empreinte mémoire RÉELLE (et non la somme des
# plafonds) des services non-Ollama, qui sert à dimensionner Ollama. En Go.
#   OpenSearch≈heap+1 · model-server≈2 · api≈1 · (background+web+pg+minio+nginx+redis)≈2
BASE_ONYX=$(( (HEAP + 1) + 2 + 1 + 2 ))             # ≈ 7 Go quand heap=1
[ "$BASE_ONYX" -lt 6 ] && BASE_ONYX=6

# RAM réellement libre pour Ollama (après marge OS + baseline Onyx), en Go.
AVAIL_OLLAMA=$(( RAM_GB - RES - BASE_ONYX )); [ "$AVAIL_OLLAMA" -lt 1 ] && AVAIL_OLLAMA=1
AVAIL=$(( RAM_GB - RES )); [ "$AVAIL" -lt 1 ] && AVAIL=1   # RAM utile globale (seuils PERF)

# Plus gros modèle qui TIENT dans AVAIL_OLLAMA (CPU) ou la VRAM (GPU).
# -----------------------------------------------------------------------------
# IMPORTANT (corrige le bug OOM #10, prouvé au runtime Azure) : le 2e nombre est
# l'EMPREINTE RAM RÉELLE EN GÉNÉRATION, pas le simple poids quantifié sur disque.
# Mesuré sur qwen2.5:14b (CPU, KV q8_0, gros num_ctx) : ~9 Go modèle + ~3 Go KV
# + ~8 Go prompt-cache/buffers ≈ 20 Go ; un plafond à 12 Go faisait OOM-killer
# (SIGKILL) llama-server sur un VRAI prompt RAG. On dimensionne donc au PIC réel,
# avec une petite marge ajoutée plus bas. Règle empirique CPU : ≈ 1,5–1,8× le
# poids Q4 (le KV + le prompt-cache dominent sur un contexte RAG long).
#   gemma3:1b≈3 · gemma3:4b≈7 · gemma3:12b≈18 · gemma3:27b≈30  (Go, CPU, RAG réaliste).
# En GPU les poids vont en VRAM → l'empreinte RAM HÔTE reste faible (≈ 4 Go).
pick_model() {
  # Famille GEMMA3 par défaut (chat) : gemma3 répond de façon SOURCEE a partir du
  # contexte recupere (prouve live, RUNTIME-EVIDENCE #12) la ou qwen2.5:14b ratait
  # le tool-calling agentique. Embedding par defaut = embeddinggemma (cf. emit).
  if [ "$USE_GPU" = 1 ]; then
    if   [ "$VRAM_GB" -ge 24 ]; then echo "gemma3:27b 30"
    elif [ "$VRAM_GB" -ge 12 ]; then echo "gemma3:12b 12"
    elif [ "$VRAM_GB" -ge 8 ];  then echo "gemma3:4b 8"
    else echo "gemma3:4b 4"; fi
  else
    # CPU : choix sur AVAIL_OLLAMA (RAM réellement libre), pas la RAM brute, et
    # sur l'empreinte PIC réelle (anti-OOM). gemma3:12b (8.1 Go Q4) exige ≥ ~18 Go
    # libres pour Ollama (poids + KV + prompt-cache sur contexte RAG) ; gemma3:4b
    # veut ≥ ~7 Go. Sur 16 Go (AVAIL_OLLAMA≈7) → gemma3:4b.
    if   [ "$AVAIL_OLLAMA" -ge 18 ]; then echo "gemma3:12b 18"
    elif [ "$AVAIL_OLLAMA" -ge 7 ];  then echo "gemma3:4b 7"
    else echo "gemma3:1b 3"; fi
  fi
}
read -r MODEL MODEL_NEED <<EOF
$(pick_model)
EOF

OLLAMA_FLOOR=$([ "$LOW" = 1 ] && echo $((2*GB)) || echo $((3*GB)))
if [ "$USE_GPU" = 1 ]; then
  OLLAMA_MEM=$((4*GB))                       # poids en VRAM → peu de RAM hôte
else
  # Plafond RAM Ollama (Mo) = empreinte PIC réelle du modèle (MODEL_NEED inclut
  # déjà KV + prompt-cache, cf. pick_model) + 2 Go de marge de sécurité, borné
  # par ce qui reste réellement libre. Sous-dimensionner ici = SIGKILL (#10).
  OLLAMA_MEM=$(( (MODEL_NEED + 2) * GB ))
  [ "$OLLAMA_MEM" -gt "$(( AVAIL_OLLAMA * GB ))" ] && OLLAMA_MEM=$(( AVAIL_OLLAMA * GB ))
  [ "$OLLAMA_MEM" -lt "$OLLAMA_FLOOR" ] && OLLAMA_MEM=$OLLAMA_FLOOR
fi

# --- GARANTIE anti-OOM : SOMME de TOUTES les limites <= RAM - 1 Go ------------
# Les limites Docker sont des PLAFONDS ; si leur somme dépasse la RAM, un pic
# simultané = OOM-kill de l'hôte. Stratégie de réduction :
#   1) rogner les GROS services jusqu'à leur plancher (régime établi << plafond),
#      en préservant le plafond Ollama (= le modèle) ;
#   2) si encore trop, rogner Ollama jusqu'à son plancher (le modèle suivra) ;
#   3) en dernier recours (machine vraiment minuscule), rogner les petits services.
# La boucle GARANTIT une somme < RAM tant qu'il reste de quoi réduire.
sum_limits() { echo $(( OS_MEM + INFER_MEM + API_MEM + BG_MEM + WEB_MEM + PG_MEM + MINIO_MEM + NGINX_MEM + REDIS_MEM + OLLAMA_MEM )); }
FIT_TARGET=$(( RAM_MB - GB )); [ "$FIT_TARGET" -lt "$GB" ] && FIT_TARGET=$GB
guard=0
while [ "$(sum_limits)" -gt "$FIT_TARGET" ] && [ "$guard" -lt 512 ]; do
  if   [ "$BG_MEM"    -gt "$BIG_FLOOR" ]; then BG_MEM=$(( BG_MEM - 256 ))
  elif [ "$INFER_MEM" -gt "$BIG_FLOOR" ]; then INFER_MEM=$(( INFER_MEM - 256 ))
  elif [ "$OS_MEM"    -gt "$OS_FLOOR" ];  then OS_MEM=$(( OS_MEM - 256 ))
  elif [ "$API_MEM"   -gt "$API_FLOOR" ]; then API_MEM=$(( API_MEM - 256 ))
  elif [ "$USE_GPU" != 1 ] && [ "$OLLAMA_MEM" -gt "$OLLAMA_FLOOR" ]; then OLLAMA_MEM=$(( OLLAMA_MEM - 256 ))
  elif [ "$WEB_MEM"   -gt 256 ]; then WEB_MEM=$(( WEB_MEM - 256 ))
  elif [ "$PG_MEM"    -gt 256 ]; then PG_MEM=$(( PG_MEM - 256 ))
  elif [ "$MINIO_MEM" -gt 256 ]; then MINIO_MEM=$(( MINIO_MEM - 256 ))
  else break; fi
  guard=$(( guard + 1 ))
done
SUM_LIMITS="$(sum_limits)"
HEADROOM=$(( RAM_MB - SUM_LIMITS ))

# --- COHÉRENCE modèle ↔ plafond Ollama ---------------------------------------
# Si la garantie anti-OOM a rogné le plafond Ollama sous le besoin du modèle
# choisi, on RÉTROGRADE le modèle pour qu'il tienne (sinon OOM-kill au chargement).
# Seuils ALIGNÉS sur l'empreinte PIC réelle de pick_model (anti-OOM #10) :
# gemma3:12b≈18 Go · gemma3:4b≈7 Go · gemma3:1b≈3 Go. On ne « promeut » un modèle
# que si le plafond Ollama FINAL couvre réellement son pic de génération.
if [ "$USE_GPU" != 1 ]; then
  om_gb=$(( OLLAMA_MEM / GB ))
  if   [ "$om_gb" -ge 18 ]; then MODEL="gemma3:12b"
  elif [ "$om_gb" -ge 7 ];  then MODEL="gemma3:4b"
  else MODEL="gemma3:1b"; fi
fi

# --- AVERTISSEMENT fail-closed informatif (petite RAM) -----------------------
# Si même le plus petit modèle CPU n'a pas son pic garanti (RAM vraiment juste),
# on le DIT BRUYAMMENT (jamais d'avalement silencieux) : l'exploitant sait que la
# génération peut OOM et doit réduire le contexte / ajouter de la RAM / un GPU.
RAM_WARN=""
if [ "$USE_GPU" != 1 ] && [ "$(( OLLAMA_MEM / GB ))" -lt 3 ]; then
  RAM_WARN="RAM trop juste pour Ollama (plafond $(( OLLAMA_MEM / GB )) Go < 3 Go) : risque d'OOM en génération même sur llama3.2:1b. Ajoutez de la RAM, baissez OLLAMA_CONTEXT_LENGTH, ou utilisez un GPU."
fi

# --- Réglages Ollama liés à la RAM utile -------------------------------------
# KEEP_ALIVE=-1 (toujours chargé) UNIQUEMENT si RAM physique >= ~24 Go ; sinon 5m
# (sur 16 Go, épingler un modèle en permanence rapproche dangereusement de l'OOM).
KEEP_ALIVE=$([ "$RAM_GB" -ge 24 ] && echo "-1" || echo "5m")
MAXLOAD=$([ "$AVAIL_OLLAMA" -ge 12 ] && echo 2 || echo 1)
if [ "$USE_GPU" = 1 ]; then NPAR=$([ "$VRAM_GB" -ge 12 ] && echo 4 || echo 2)
else NPAR=$([ "$AVAIL_OLLAMA" -ge 12 ] && echo 2 || echo 1); fi
PERF_OK=$([ "$RAM_GB" -ge 32 ] || { [ "$USE_GPU" = 1 ] && [ "$RAM_GB" -ge 24 ]; } && echo 1 || echo 0)

# Fenêtre de contexte (num_ctx) au plus juste du plafond Ollama FINAL (après la
# garantie anti-OOM et l'éventuelle rétrogradation du modèle). Le défaut Ollama
# (4096) tronque silencieusement le contexte RAG ; on l'élargit sans risque grâce
# au cache KV q8_0 (~/2). Mémoire KV ~ OLLAMA_CONTEXT_LENGTH × OLLAMA_NUM_PARALLEL.
if [ "$USE_GPU" = 1 ]; then
  OLLAMA_CTX=16384
else
  om_ctx_gb=$(( OLLAMA_MEM / GB ))
  if   [ "$om_ctx_gb" -ge 7 ]; then OLLAMA_CTX=12288   # 7-14B (la RAM suit par construction)
  elif [ "$om_ctx_gb" -ge 3 ]; then OLLAMA_CTX=8192    # 3B (≈ 16 Go)
  else OLLAMA_CTX=4096; fi                              # postes minuscules : prudence
fi

# Formate des Mo en unité Docker : multiple de 1024 → "Ng", sinon "Nm".
fmt_mem() { if [ $(( $1 % GB )) -eq 0 ]; then printf '%sg' $(( $1 / GB )); else printf '%sm' "$1"; fi; }
# Valeur en Go (1 décimale) pour l'affichage du détail de la somme.
gb1() { awk -v m="$1" -v g="$GB" 'BEGIN{printf "%.1f", m/g}'; }
SUM_GB="$(gb1 "$SUM_LIMITS")"; HEAD_GB="$(gb1 "$HEADROOM")"

# ---- Rapport ----------------------------------------------------------------
line; bold "  DIAGNOSTIC & TUNING — onix (stack IA locale)"; line
printf "  OS / Arch     : %s / %s\n" "$OS" "$ARCH"
printf "  CPU           : %s (%s threads)\n" "${CPU_MODEL:-inconnu}" "$CORES"
printf "  RAM totale    : %s Go  (réserve OS %s Go · baseline Onyx %s Go → dispo Ollama ~%s Go)\n" "$RAM_GB" "$RES" "$BASE_ONYX" "$AVAIL_OLLAMA"
printf "  GPU           : %s\n" "$([ "$GPU_KIND" = none ] && echo 'aucun GPU dédié' || echo "$GPU_NAME")"
[ "$VRAM_GB" -gt 0 ] && printf "  VRAM          : %s Go\n" "$VRAM_GB"
printf "  Docker + GPU  : %s\n" "$DOCKER_GPU"
if [ "$OS" = Linux ]; then
  mmc="$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)"
  [ "${mmc:-0}" -ge 262144 ] && printf "  OpenSearch    : vm.max_map_count=%s ✓\n" "$mmc" \
    || printf "  OpenSearch    : ⚠ vm.max_map_count=%s (<262144) → sudo sysctl -w vm.max_map_count=262144\n" "$mmc"
fi
line
[ "$GPU_KIND" = apple ] && { bold "  macOS : Docker n'accède pas au GPU → CPU en conteneur."; echo "  Pour le GPU Metal, lancez Ollama en NATIF (cf. docs/RUNBOOK.md)."; line; }
[ "$USE_GPU" = 1 ] && bold "  PROFIL : GPU NVIDIA — lancez : make up GPU=1" || bold "  PROFIL : CPU"
[ "$PERF_OK" = 1 ] && echo "  Ressources confortables → indexation dédiée possible : make up PERF=1"
[ -n "$RAM_WARN" ] && { printf '\033[1;33m  ⚠ %s\033[0m\n' "$RAM_WARN"; }
line

emit() { printf '    %s=%s\n' "$1" "$2"; }
echo "  Réglages optimaux pour CETTE machine :"; echo
emit OLLAMA_MODELS_TO_PULL "$MODEL embeddinggemma"
emit OLLAMA_FLASH_ATTENTION 1
emit OLLAMA_KV_CACHE_TYPE q8_0
emit OLLAMA_KEEP_ALIVE "$KEEP_ALIVE"
emit OLLAMA_NUM_PARALLEL "$NPAR"
emit OLLAMA_MAX_LOADED_MODELS "$MAXLOAD"
emit OLLAMA_CONTEXT_LENGTH "$OLLAMA_CTX"
emit OLLAMA_CPU_LIMIT "$CORES"
emit OLLAMA_MEM_LIMIT "$(fmt_mem "$OLLAMA_MEM")"
emit OPENSEARCH_HEAP "${HEAP}g"
emit OPENSEARCH_MEM_LIMIT "$(fmt_mem "$OS_MEM")"
emit INFERENCE_MEM_LIMIT "$(fmt_mem "$INFER_MEM")"
emit BACKGROUND_MEM_LIMIT "$(fmt_mem "$BG_MEM")"
emit BACKGROUND_CPU_LIMIT "$CORES"
emit API_SERVER_MEM_LIMIT "$(fmt_mem "$API_MEM")"
emit WEB_MEM_LIMIT "$(fmt_mem "$WEB_MEM")"
emit POSTGRES_MEM_LIMIT "$(fmt_mem "$PG_MEM")"
emit MINIO_MEM_LIMIT "$(fmt_mem "$MINIO_MEM")"
emit NGINX_MEM_LIMIT "$(fmt_mem "$NGINX_MEM")"
line
# Contrôle anti-OOM : la SOMME des limites doit rester < RAM physique.
printf "  Somme des limites mémoire : %s Go\n" "$SUM_GB"
printf "    = OpenSearch %s + infer %s + api %s + bg %s + web %s + pg %s + minio %s + nginx %s + redis %s + Ollama %s\n" \
  "$(fmt_mem "$OS_MEM")" "$(fmt_mem "$INFER_MEM")" "$(fmt_mem "$API_MEM")" "$(fmt_mem "$BG_MEM")" \
  "$(fmt_mem "$WEB_MEM")" "$(fmt_mem "$PG_MEM")" "$(fmt_mem "$MINIO_MEM")" "$(fmt_mem "$NGINX_MEM")" \
  "$(fmt_mem "$REDIS_MEM")" "$(fmt_mem "$OLLAMA_MEM")"
if [ "$SUM_LIMITS" -lt "$RAM_MB" ]; then
  printf "  → %s Go < %s Go RAM physique ✓  (coussin libre ~%s Go)\n" "$SUM_GB" "$RAM_GB" "$HEAD_GB"
else
  printf "  → ⚠ %s Go >= %s Go RAM physique : profil trop juste, réduisez un *_MEM_LIMIT.\n" "$SUM_GB" "$RAM_GB"
fi
line

# ---- Application -------------------------------------------------------------
if [ "$APPLY" != 1 ]; then
  echo "  Pour écrire ces valeurs dans .env :  make tune   (ou ./scripts/detect-hardware.sh --apply)"
  exit 0
fi

[ -f "$ENV_FILE" ] || cp "$TEMPLATE" "$ENV_FILE"
set_force() { # set_force KEY VALUE (remplace ou ajoute ; secrets non touchés)
  if grep -q "^$1=" "$ENV_FILE"; then
    awk -v k="$1" -v v="$2" -F= '$1==k{print k"="v; next}{print}' "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
  else printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; fi
}
set_force OLLAMA_MODELS_TO_PULL "$MODEL embeddinggemma"
set_force OLLAMA_FLASH_ATTENTION 1
set_force OLLAMA_KV_CACHE_TYPE q8_0
set_force OLLAMA_KEEP_ALIVE "$KEEP_ALIVE"
set_force OLLAMA_NUM_PARALLEL "$NPAR"
set_force OLLAMA_MAX_LOADED_MODELS "$MAXLOAD"
set_force OLLAMA_CONTEXT_LENGTH "$OLLAMA_CTX"
set_force OLLAMA_CPU_LIMIT "$CORES"
set_force OLLAMA_MEM_LIMIT "$(fmt_mem "$OLLAMA_MEM")"
set_force OPENSEARCH_HEAP "${HEAP}g"
set_force OPENSEARCH_MEM_LIMIT "$(fmt_mem "$OS_MEM")"
set_force INFERENCE_MEM_LIMIT "$(fmt_mem "$INFER_MEM")"
set_force BACKGROUND_MEM_LIMIT "$(fmt_mem "$BG_MEM")"
set_force BACKGROUND_CPU_LIMIT "$CORES"
set_force API_SERVER_MEM_LIMIT "$(fmt_mem "$API_MEM")"
set_force WEB_MEM_LIMIT "$(fmt_mem "$WEB_MEM")"
set_force POSTGRES_MEM_LIMIT "$(fmt_mem "$PG_MEM")"
set_force MINIO_MEM_LIMIT "$(fmt_mem "$MINIO_MEM")"
set_force NGINX_MEM_LIMIT "$(fmt_mem "$NGINX_MEM")"
[ -f "$ENV_FILE" ] && chmod 600 "$ENV_FILE"
bold "  ✓ .env mis à jour avec le profil optimal."
echo "  Étapes : make secrets (si pas fait)  →  make up$([ "$PERF_OK" = 1 ] && echo ' PERF=1')$([ "$USE_GPU" = 1 ] && echo ' GPU=1')  →  make verify"
