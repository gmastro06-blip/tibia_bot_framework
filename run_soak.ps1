param(
  [int]$Seconds = 600,
  [int]$Monitor = 2,
  [double]$CaptureFps = 10,
  [string]$LogPath = "logs\\soak_run.log",
  [switch]$NoEmoji = $true,
  [switch]$DisableRoboflow = $true,
  [switch]$EnableOverlay = $true,
  [switch]$EnableReplay = $true,
  [switch]$EnableJsonl = $true
)

$ErrorActionPreference = 'Stop'

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null
Remove-Item -ErrorAction SilentlyContinue $LogPath

$env:BOT_PROFILE = "1"
$env:FORCE_MONITOR = "$Monitor"
$env:CAPTURE_FPS = "$CaptureFps"

if ($NoEmoji) {
  $env:NO_EMOJI = "1"
} else {
  Remove-Item Env:NO_EMOJI -ErrorAction SilentlyContinue
}

$argsList = @(
  "-u",
  "-m",
  "tools.smoke_run_bot_real",
  "--seconds", "$Seconds",
  "--monitor", "$Monitor",
  "--capture-fps", "$CaptureFps"
)

if ($DisableRoboflow) { $argsList += "--disable-roboflow" }
if ($EnableOverlay)   { $argsList += "--enable-overlay" }
if ($EnableReplay)    { $argsList += "--enable-replay" }
if ($EnableJsonl)     { $argsList += "--enable-jsonl" }

Write-Host "Running soak: seconds=$Seconds monitor=$Monitor fps=$CaptureFps log=$LogPath NO_EMOJI=$($NoEmoji.IsPresent)" 

# Stream output to console and log file.
poetry run python @argsList 2>&1 | Tee-Object -FilePath $LogPath -Append
