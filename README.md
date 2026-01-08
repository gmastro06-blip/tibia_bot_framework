Hacer un “route viewer” (dibujar la ruta, detectar saltos raros, loops, distancias, acciones presentes).# Tibia Bot Framework

Framework de bot en tiempo real para Tibia-like MMORPGs usando un pipeline threaded: **captura → visión → decisión → acción**.

## Requisitos

- Windows
- Python (recomendado vía Poetry)

## Instalación

```bash
poetry install
```

## Ejecutar el bot

```bash
poetry run python -m src.main
```

## Tests (regresión)

Ejecuta la suite de tests para validar que no se pierde funcionalidad clave (config runtime, selección de ROIs, etc.):

```bash
poetry run pytest
```

## UI (iniciar / parar)

UI mínima con Tkinter para iniciar y detener el bot:

```bash
poetry run python run_bot_ui.py
```

La UI incluye pestañas:
- **Control**: iniciar/parar
- **Healing**: configuración básica (se aplica en tiempo real)
- **Cavebot**: configuración básica (se aplica en tiempo real)

## Captura de pantalla (multi-monitor)

La captura usa `MSS` como fallback robusto y soporta múltiples monitores.

### Monitor por defecto

- Si no configuras nada, el bot **prefiere el monitor 2** (proyector en este setup).
- Si el monitor forzado falla, automáticamente hace fallback a “buscar en todos los monitores”.

### Forzar monitor

Puedes forzar el monitor con la variable de entorno `FORCE_MONITOR`:

PowerShell:

```powershell
$env:FORCE_MONITOR='2'
poetry run python -m src.main
```

### Verbose / Debug logs

- `CAPTURE_VERBOSE=1`: logs detallados de captura
- `OCR_DEBUG=1` o `BOT_DEBUG=1`: logs detallados de OCR (por defecto el OCR es silencioso para no spamear consola)

### Diagnóstico: capturar todos los monitores

Para identificar qué índice corresponde a cada monitor (incluye el índice 0 combinado), ejecuta:

```bash
poetry run python scripts/capture_all_monitors.py
```

Esto guarda una imagen por monitor en `debug_images_real/`.

## Debug de ROIs (calibración)

### Generar overlay + crops (monitor 2)

Este script captura 1 frame, dibuja todas las ROIs encima y guarda crops clave (HP/MP):

```powershell
$env:FORCE_MONITOR='2'
poetry run python test_capture.py
```

Archivos generados (ejemplos):
- `debug_images_real/dxgi_full_...png`
- `debug_images_real/dxgi_overlay_...png`
- `debug_images_real/dxgi_crop_hpmp_top_strip_...png`

### Nota sobre ROIs de HP/MP

Para fullscreen real, las ROIs superiores se calibran desde `y=0.0` y el OCR usa como fallback robusto el ROI `hpmp_top_strip` (separa izquierda=HP, derecha=MP).

## Configuración

- ROIs por resolución:
  - `configs/rois_guess.json`
  - `configs/rois_guess_1920x1080.json`

