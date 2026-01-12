param(
  [string]$Out = "logs\\soak_timeline.html",
  [switch]$OnlyCommitted = $false,
  [switch]$OnlyActionEvents = $false,
  [string]$GrepAction = "",
  [double]$MatchWindowS = 1.5,
  [int]$Limit = 5000
)

$ErrorActionPreference = 'Stop'

# Run from repo root regardless of where invoked.
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $repoRoot
Push-Location $repoRoot

try {
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Out) | Out-Null

  $argsList = @(
    'run','python','tools/soak_timeline_report.py',
    '--out', $Out,
    '--limit', "$Limit",
    '--match-window-s', "$MatchWindowS"
  )

  if ($OnlyCommitted) { $argsList += '--only-committed' }
  if ($OnlyActionEvents) { $argsList += '--only-action-events' }
  if ($GrepAction) { $argsList += @('--grep-action', $GrepAction) }

  poetry @argsList

  $abs = Resolve-Path $Out
  Start-Process $abs
}
finally {
  Pop-Location
}
