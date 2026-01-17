param(
  [double]$SecondsDry = 25,
  [double]$SecondsLive = 15,
  [int]$Monitor = 2,
  [double]$CaptureFps = 10,
  [string]$Route = "configs/route.json",
  [string]$CavebotMode = "pos",
  [switch]$Live = $false
)

$ErrorActionPreference = 'Stop'

# Common observability
$env:OVERLAY_ENABLED = "1"
$env:OVERLAY_INTERVAL_S = "1.0"
$env:REPLAY_ENABLED = "1"
$env:REPLAY_INTERVAL_MS = "1500"
$env:LOG_JSONL_ENABLED = "1"
$env:LOG_JSONL_INTERVAL_MS = "250"

# Capture control
$env:FORCE_MONITOR = "$Monitor"
$env:CAPTURE_FPS = "$CaptureFps"

$argsList = @(
  "-u",
  "tools/run_real_trial_two_phase.py",
  "--seconds-dry", "$SecondsDry",
  "--seconds-live", "$SecondsLive",
  "--monitor", "$Monitor",
  "--capture-fps", "$CaptureFps",
  "--route", "$Route",
  "--cavebot-mode", "$CavebotMode"
)

if ($Live) {
  $argsList += "--live"
  $argsList += "--i-accept-live-input-risk"
}

Write-Host "Running two-phase trial: dry=${SecondsDry}s live=${SecondsLive}s monitor=$Monitor fps=$CaptureFps live=$($Live.IsPresent)"
poetry run python @argsList
