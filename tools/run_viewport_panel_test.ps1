param(
    [string]$OutDir = "logs/replay_viewport_panels",
    [int]$DurationS = 20,
    [int]$IntervalMs = 250,
    [double]$UpdateIntervalS = 0.2,
    [double]$ThresholdPx = 25,
    [switch]$Plot
)

$ErrorActionPreference = "Stop"

# Resolve workspace root (this script lives in tools/)
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot

try {
    $env:REPLAY_ENABLED = "1"
    $env:REPLAY_INTERVAL_MS = "$IntervalMs"
    $env:REPLAY_OUT_DIR = "$OutDir"

    $env:VIEWPORT_AUTO_ENABLED = "1"
    $env:VIEWPORT_UPDATE_INTERVAL_S = "$UpdateIntervalS"

    # Robust Sobel peak-pair defaults
    $env:VIEWPORT_PEAK_PERCENTILE = "97.5"
    $env:VIEWPORT_PEAK_MIN_SEP_PX = "25"
    $env:VIEWPORT_PEAK_MAX_CANDIDATES = "24"
    $env:VIEWPORT_PRIOR_WEIGHT = "0.8"

    # Discrete-state snapping (panel combos)
    $env:VIEWPORT_STATE_SNAP_PX = "18"
    $env:VIEWPORT_MAX_STATES = "8"

    $env:BOT_EXIT_AFTER_S = "$DurationS"

    Write-Host "Running bot for $DurationS s... (toggle your HUD panels now)"
    poetry run python -m src.main

    $plotArg = ""
    if ($Plot) { $plotArg = "--plot" }

    Write-Host "\nGenerating report..."
    poetry run python tools/viewport_replay_report.py "$OutDir" --threshold "$ThresholdPx" $plotArg

    Write-Host "\nDone. Output folder: $OutDir"
}
finally {
    Pop-Location
}
