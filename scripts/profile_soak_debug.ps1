# Perfil: soak debug (sin UI, sin inputs)
# - Lanza tools.smoke_run_bot_real como proceso separado
# - Activa overlay + replay + jsonl
# - Redirige stdout/stderr a logs/ para facilitar monitoreo

param(
    [Parameter(Mandatory = $false)]
    [int]$Monitor = 2,

    [Parameter(Mandatory = $false)]
    [int]$Seconds = 600,

    [Parameter(Mandatory = $false)]
    [double]$CaptureFps = 10,

    [Parameter(Mandatory = $false)]
    [ValidateSet('enabled','disabled')]
    [string]$Roboflow = 'disabled'
)

New-Item -ItemType Directory -Force -Path logs | Out-Null

$outLog = 'logs\soak_run.log'
$errLog = 'logs\soak_run.err.log'
Remove-Item -ErrorAction SilentlyContinue $outLog, $errLog

# Estabilidad / reproducibilidad
$env:FORCE_MONITOR = "$Monitor"
$env:CAPTURE_FPS = "$CaptureFps"
$env:PYTHONIOENCODING = 'utf-8'

# Overlay (exporta PNGs a logs/debug_overlay)
$env:OVERLAY_ENABLED = '1'
$env:OVERLAY_OUT_DIR = 'logs/debug_overlay'
$env:OVERLAY_INTERVAL_S = '1.0'
$env:OVERLAY_TILE_GRID = '1'
$env:OVERLAY_TILE_PX = '32'

# Si NO hay coords visibles, evita falsos positivos por OCR.
# (No afecta al overlay/replay/jsonl; solo a navegación pos-mode)
if (-not $env:COORDS_PROVIDER) {
    $env:COORDS_PROVIDER = 'disabled'
}

$argsList = @(
  'run','python','-u','-m','tools.smoke_run_bot_real',
  '--seconds',"$Seconds",
  '--monitor',"$Monitor",
  '--capture-fps',"$CaptureFps",
  '--enable-overlay','--enable-replay','--enable-jsonl'
)

if ($Roboflow -ne 'enabled') {
  $argsList += '--disable-roboflow'
}

$p = Start-Process -FilePath 'poetry' -ArgumentList $argsList -NoNewWindow -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru

"Started PID=$($p.Id)"
"stdout: $outLog"
"stderr: $errLog"
"tail:   Get-Content -Encoding utf8 $outLog -Tail 30 -Wait"
