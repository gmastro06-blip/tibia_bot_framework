# Perfil: coords por minimapa (experimental, sin inputs)
# - Usa COORDS_PROVIDER=minimap (no OCR de coords)
# - Requiere una seed absoluta (x,y[,z]) para acumular movimiento
# - Recomendado cuando no hay coords visibles en el cliente

param(
    [Parameter(Mandatory = $false)]
    [int]$Monitor = 2,

    [Parameter(Mandatory = $false)]
    [int]$SeedX,

    [Parameter(Mandatory = $false)]
    [int]$SeedY,

    [Parameter(Mandatory = $false)]
    [int]$SeedZ
)

# Monitor donde está el juego
$env:FORCE_MONITOR = "$Monitor"

# Proveedor de coords
$env:COORDS_PROVIDER = 'minimap'

# Seed: toma parámetros si se pasaron; si no, usa variables de entorno existentes
if ($PSBoundParameters.ContainsKey('SeedX')) { $env:COORDS_SEED_X = "$SeedX" }
if ($PSBoundParameters.ContainsKey('SeedY')) { $env:COORDS_SEED_Y = "$SeedY" }
if ($PSBoundParameters.ContainsKey('SeedZ')) { $env:COORDS_SEED_Z = "$SeedZ" }

if (-not $env:COORDS_SEED_X -or -not $env:COORDS_SEED_Y) {
    Write-Host "Falta seed. Ejemplo:" -ForegroundColor Yellow
    Write-Host "  ./scripts/profile_minimap.ps1 -Monitor 2 -SeedX 32561 -SeedY 32496 -SeedZ 7" -ForegroundColor Yellow
    Write-Host "o setea env vars: COORDS_SEED_X / COORDS_SEED_Y / COORDS_SEED_Z" -ForegroundColor Yellow
    exit 2
}

# Knobs opcionales (descomenta si necesitas ajustar drift / sensibilidad)
# $env:MINIMAP_TILE_PX = '4'
# $env:MINIMAP_PHASECORR_MIN_RESPONSE = '0.15'
# $env:MINIMAP_MAX_SHIFT_PX = '32'
# $env:MINIMAP_MAX_STEP_PER_FRAME = '3'
# $env:MINIMAP_INVERT_X = '0'
# $env:MINIMAP_INVERT_Y = '0'

poetry run python run_bot_ui.py
