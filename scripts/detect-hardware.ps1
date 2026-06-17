# =============================================================================
# Diagnostic matériel (Windows / PowerShell) : CPU, RAM, GPU + aptitude Docker.
# Imprime un VERDICT et les valeurs .env recommandées. Lecture seule.
#   Exécution :  powershell -ExecutionPolicy Bypass -File .\scripts\detect-hardware.ps1
# =============================================================================

function Line { Write-Host ("-" * 64) }

$cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1)
$cpuName  = $cpu.Name
$cpuCores = $cpu.NumberOfLogicalProcessors
$ramGB    = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)

# GPU
$gpuKind = "none"; $gpuName = ""; $vramGB = 0
$nvidia = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($nvidia) {
    $gpuKind = "nvidia"
    $gpuName = (& nvidia-smi --query-gpu=name --format=csv,noheader | Select-Object -First 1)
    $vramMB  = (& nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | Select-Object -First 1)
    if ($vramMB) { $vramGB = [math]::Round([int]$vramMB / 1024) }
} else {
    $vc = Get-CimInstance Win32_VideoController | Select-Object -First 1
    if ($vc) {
        $gpuName = $vc.Name
        if ($vc.Name -match "NVIDIA") { $gpuKind = "nvidia" }
        elseif ($vc.Name -match "AMD|Radeon") { $gpuKind = "amd" }
        else { $gpuKind = "intel" }
    }
}

# Docker
$dockerOK = "non"
if (Get-Command docker -ErrorAction SilentlyContinue) {
    docker info *> $null; if ($LASTEXITCODE -eq 0) { $dockerOK = "oui" }
}

# ---- Recommandation : MÊME logique que scripts/detect-hardware.sh -----------
# (source de vérité = .sh ; ce script l'applique à l'identique sous Windows)
#   1) réserve OS + baseline Onyx (~7 Go) AVANT de dimensionner Ollama ;
#   2) modèle choisi sur la RAM RÉELLEMENT LIBRE (ou la VRAM en GPU) ;
#   3) somme de TOUTES les limites bornée à < RAM physique (anti-OOM) ;
#   4) KEEP_ALIVE=-1 seulement si RAM >= ~24 Go ; sinon 5m.
$GB = 1024
# IDiv : division ENTIÈRE par troncature (comme bash `/`) — PAS l'arrondi par
# défaut de [int] (banquier) → garantit des seuils IDENTIQUES à detect-hardware.sh.
function IDiv([int]$a,[int]$b){ [int][math]::Floor([double]$a / $b) }
function Clamp([int]$v,[int]$lo,[int]$hi){ if($v -lt $lo){$lo}elseif($v -gt $hi){$hi}else{$v} }
function Snap([int]$m){ (IDiv $m 256) * 256 }
function FmtMem([int]$m){ if($m % $GB -eq 0){ "$(IDiv $m $GB)g" } else { "${m}m" } }

$useGpu = ($gpuKind -eq "nvidia" -and $vramGB -ge 6)
$ramMB  = $ramGB * $GB
$res    = Clamp (IDiv $ramGB 8) 2 8                # marge OS (Go)
$low    = ($ramGB -lt 16)

# Limites des services Onyx (hors Ollama), en Mo — planchers réduits si < 16 Go.
$osFloor  = if($low){ IDiv ($GB*3) 2 } else { 2*$GB }
$bigFloor = if($low){ $GB } else { 2*$GB }
$apiFloor = if($low){ IDiv $GB 2 } else { $GB }
$heap     = Clamp (IDiv ($ramGB * 12) 100) 1 8     # heap OpenSearch (Go)
$osMem    = Clamp (IDiv ($heap*$GB*3) 2) $osFloor (12*$GB)
$inferMem = Clamp (Snap (IDiv ($ramMB*15) 100)) $bigFloor (6*$GB)
$apiMem   = Clamp (Snap (IDiv ($ramMB*10) 100)) $apiFloor (6*$GB)
$bgMem    = Clamp (Snap (IDiv ($ramMB*15) 100)) $bigFloor (8*$GB)
$webMem   = if($low){512}else{1024}; $pgMem = if($low){512}else{1024}
$minioMem = 512; $nginxMem = 256; $redisMem = 256

# Baseline Onyx « régime établi » (Go) → RAM libre pour Ollama.
$baseOnyx = ($heap + 1) + 2 + 1 + 2; if($baseOnyx -lt 6){ $baseOnyx = 6 }
$availOllama = $ramGB - $res - $baseOnyx; if($availOllama -lt 1){ $availOllama = 1 }

# Choix du modèle (mêmes seuils que le .sh) + besoin mémoire (Go).
$modelNeed = 2; $model = "llama3.2:1b"
if ($useGpu) {
    if     ($vramGB -ge 24){ $model="qwen2.5:32b-instruct"; $modelNeed=22 }
    elseif ($vramGB -ge 12){ $model="qwen2.5:14b-instruct"; $modelNeed=12 }
    elseif ($vramGB -ge 8) { $model="llama3.1:8b";          $modelNeed=8  }
    else                   { $model="llama3.2:3b";          $modelNeed=4  }
} else {
    if     ($availOllama -ge 18){ $model="qwen2.5:14b-instruct"; $modelNeed=11 }
    elseif ($availOllama -ge 7) { $model="qwen2.5:7b-instruct";  $modelNeed=6  }
    elseif ($availOllama -ge 4) { $model="llama3.2:3b";          $modelNeed=4  }
    else                        { $model="llama3.2:1b";          $modelNeed=2  }
}

$ollamaFloor = if($low){ 2*$GB } else { 3*$GB }
if ($useGpu) {
    $ollamaMem = 4*$GB
} else {
    $ollamaMem = ($modelNeed + 1) * $GB
    if ($ollamaMem -gt (($availOllama + 1) * $GB)) { $ollamaMem = ($availOllama + 1) * $GB }
    if ($ollamaMem -lt $ollamaFloor) { $ollamaMem = $ollamaFloor }
}

# GARANTIE anti-OOM : somme <= RAM - 1 Go (rogne services d'abord, Ollama ensuite).
function SumLimits { $osMem+$inferMem+$apiMem+$bgMem+$webMem+$pgMem+$minioMem+$nginxMem+$redisMem+$ollamaMem }
$fitTarget = $ramMB - $GB; if($fitTarget -lt $GB){ $fitTarget = $GB }
$guard = 0
while ((SumLimits) -gt $fitTarget -and $guard -lt 512) {
    if     ($bgMem    -gt $bigFloor){ $bgMem -= 256 }
    elseif ($inferMem -gt $bigFloor){ $inferMem -= 256 }
    elseif ($osMem    -gt $osFloor) { $osMem -= 256 }
    elseif ($apiMem   -gt $apiFloor){ $apiMem -= 256 }
    elseif (-not $useGpu -and $ollamaMem -gt $ollamaFloor){ $ollamaMem -= 256 }
    elseif ($webMem   -gt 256){ $webMem -= 256 }
    elseif ($pgMem    -gt 256){ $pgMem -= 256 }
    elseif ($minioMem -gt 256){ $minioMem -= 256 }
    else { break }
    $guard++
}
$sumLimits = SumLimits
# Cohérence modèle <-> plafond Ollama (CPU) : rétrograde si rogné sous le besoin.
if (-not $useGpu) {
    $omGb = IDiv $ollamaMem $GB
    if     ($omGb -ge 12){ $model = "qwen2.5:14b-instruct" }
    elseif ($omGb -ge 7) { $model = "qwen2.5:7b-instruct" }
    elseif ($omGb -ge 4) { $model = "llama3.2:3b" }
    else                 { $model = "llama3.2:1b" }
}

$profile   = if ($useGpu) { "GPU NVIDIA (via WSL2)" } else { "CPU" }
$gpuHint   = if ($useGpu) { "Lancer avec le profil GPU : make up GPU=1 (Docker Desktop + backend WSL2 + pilotes NVIDIA)." } else { "" }
$keepAlive = if ($ramGB -ge 24) { "-1" } else { "5m" }
$nPar      = if ($availOllama -ge 12) { 2 } else { 1 }
$maxLoad   = if ($availOllama -ge 12) { 2 } else { 1 }
# Fenêtre de contexte (num_ctx) au plus juste du plafond Ollama. Le défaut (4096)
# tronque le contexte RAG ; KV ~ contextLength x NUM_PARALLEL (q8_0 ~/2).
$ollamaCtx = if ($useGpu) { 16384 } elseif ((IDiv $ollamaMem $GB) -ge 7) { 12288 } elseif ((IDiv $ollamaMem $GB) -ge 3) { 8192 } else { 4096 }
$sumGb     = [math]::Round($sumLimits / $GB, 1)
$headGb    = [math]::Round(($ramMB - $sumLimits) / $GB, 1)

Line; Write-Host "  DIAGNOSTIC & TUNING — onix (stack IA locale)" -ForegroundColor Cyan; Line
Write-Host "  OS            : Windows"
Write-Host "  CPU           : $cpuName ($cpuCores threads)"
Write-Host "  RAM totale    : $ramGB Go  (réserve OS $res Go · baseline Onyx $baseOnyx Go → dispo Ollama ~$availOllama Go)"
if ($gpuName) { Write-Host "  GPU           : $gpuName" } else { Write-Host "  GPU           : aucun" }
if ($vramGB -gt 0) { Write-Host "  VRAM          : $vramGB Go" }
Write-Host "  Docker        : $dockerOK"
Line
Write-Host "  NOTE Windows : le GPU NVIDIA n'est exploitable par Docker que via le"
Write-Host "  backend WSL2 (Docker Desktop) avec les pilotes NVIDIA pour WSL installés."
Line
Write-Host "  VERDICT : profil recommandé = $profile" -ForegroundColor Green
Write-Host "  Valeurs à reporter dans .env :"
Write-Host ""
Write-Host "    OLLAMA_MODELS_TO_PULL=$model nomic-embed-text"
Write-Host "    OLLAMA_FLASH_ATTENTION=1"
Write-Host "    OLLAMA_KV_CACHE_TYPE=q8_0"
Write-Host "    OLLAMA_KEEP_ALIVE=$keepAlive"
Write-Host "    OLLAMA_NUM_PARALLEL=$nPar"
Write-Host "    OLLAMA_MAX_LOADED_MODELS=$maxLoad"
Write-Host "    OLLAMA_CONTEXT_LENGTH=$ollamaCtx"
Write-Host "    OLLAMA_CPU_LIMIT=$cpuCores"
Write-Host "    OLLAMA_MEM_LIMIT=$(FmtMem $ollamaMem)"
Write-Host "    OPENSEARCH_HEAP=${heap}g"
Write-Host "    OPENSEARCH_MEM_LIMIT=$(FmtMem $osMem)"
Write-Host "    INFERENCE_MEM_LIMIT=$(FmtMem $inferMem)"
Write-Host "    BACKGROUND_MEM_LIMIT=$(FmtMem $bgMem)"
Write-Host "    BACKGROUND_CPU_LIMIT=$cpuCores"
Write-Host "    API_SERVER_MEM_LIMIT=$(FmtMem $apiMem)"
Write-Host "    WEB_MEM_LIMIT=$(FmtMem $webMem)"
Write-Host "    POSTGRES_MEM_LIMIT=$(FmtMem $pgMem)"
Write-Host "    MINIO_MEM_LIMIT=$(FmtMem $minioMem)"
Write-Host "    NGINX_MEM_LIMIT=$(FmtMem $nginxMem)"
Line
Write-Host ("  Somme des limites mémoire : {0} Go" -f $sumGb)
if ($sumLimits -lt $ramMB) {
    Write-Host ("  -> {0} Go < {1} Go RAM physique  (coussin libre ~{2} Go)" -f $sumGb,$ramGB,$headGb) -ForegroundColor Green
} else {
    Write-Host ("  -> {0} Go >= {1} Go RAM physique : profil trop juste, reduisez un *_MEM_LIMIT." -f $sumGb,$ramGB) -ForegroundColor Yellow
}
if ($gpuHint) { Write-Host ""; Write-Host "  $gpuHint" }
Line
Write-Host "  NB : sous WSL2 vous pouvez utiliser directement 'make tune' (écrit .env)."
Write-Host "  Étapes (depuis la racine du repo) : make secrets ; make up ; make verify"
