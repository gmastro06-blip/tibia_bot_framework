# Perfil: overlay debug (sin inputs)
# - Exporta frames anotados a logs/debug_overlay/
# - Dibuja ROIs seleccionadas (OVERLAY_ROIS)
# - Incluye líneas de diagnóstico (roi_offset, viewport_auto, minimap, coords_provider)

param(
    [Parameter(Mandatory = $false)]
    [int]$Monitor = 2,

    [Parameter(Mandatory = $false)]
    [string]$Rois = 'coords_ocr,minimap_content,hp_top_ocr,mp_top_ocr,hp_low_bar,mp_low_bar,states_icons,equipment_slots,battlelist_rows',

    [Parameter(Mandatory = $false)]
    [double]$IntervalS = 1.0,

    [Parameter(Mandatory = $false)]
    [int]$TilePx = 32
)

# Monitor donde está el juego
$env:FORCE_MONITOR = "$Monitor"

# Overlay exporter
$env:OVERLAY_ENABLED = '1'
$env:OVERLAY_OUT_DIR = 'logs/debug_overlay'
$env:OVERLAY_INTERVAL_S = "$IntervalS"
$env:OVERLAY_TILE_GRID = '1'
$env:OVERLAY_TILE_PX = "$TilePx"
$env:OVERLAY_ROIS = "$Rois"

# Recomendación estable si NO hay coords visibles:
# (evita falsos positivos de OCR; no afecta el overlay)
if (-not $env:COORDS_PROVIDER) {
    $env:COORDS_PROVIDER = 'disabled'
}

poetry run python run_bot_ui.py
