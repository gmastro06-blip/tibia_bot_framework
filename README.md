# Tibia Bot Framework

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

## Replay (ROI + JSON) y export JSONL

### Replay (snapshots)
- Actívalo en la UI: **Configuración → Guardar replays (ROI+JSON)**.
- Ajusta:
  - `Replay interval (ms)`
  - `Replay out_dir` (por defecto: `logs/replay`)
- Botón **Snapshot ahora**: fuerza un snapshot en el próximo frame (aunque no toque por intervalo).
- Botón **Abrir carpeta**: abre el directorio de replays en Windows.

Archivos generados:
- `logs/replay/<ts>.json`
- `logs/replay/rois/<ts>_<roi_name>.png`

### Pruning (opcional)
Para evitar crecimiento infinito de `logs/replay/`, usa:

```powershell
$env:REPLAY_MAX_JSON='200'
poetry run python -m src.main
```

Mantiene los N JSON más recientes y borra los PNG asociados al mismo timestamp.

### Export de telemetría y eventos (JSONL)
- Actívalo en la UI: **Configuración → Exportar telemetría JSONL**.
- Ajusta:
  - `Log interval (ms)`
  - `Log out_file` (por defecto: `logs/telemetry.jsonl`)
- Botones **Abrir archivo** / **Abrir carpeta** para acceder rápido.

El archivo incluye:
- `kind="telemetry"` (muestreo periódico)
- `kind` tipo `event.*` cuando cambian target/recommendation/cavebot o cambian flags.

### Inspeccionar un replay (montage)
Genera una imagen con los crops en grilla y un header con telemetría:

```bash
poetry run python tools/replay_inspect.py logs/replay/<ts>.json
```

Salida por defecto: `logs/replay/<ts>.montage.png`

## Smoke test: HP/MP real + estados simulados

Si quieres validar rápido que el OCR/barras leen HP/MP en vivo, pero mantener el resto simulado:

```powershell
$env:FORCE_MONITOR='2'
poetry run python tools/smoke_live_hpmp.py --seconds 20 --fps 10 --paralyzed --utamo
```

Esto imprime HP/MP reales y flags simulados (paralyzed/haste/utamo/hungry) sin ejecutar inputs.

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

## Performance tuning

Variables de entorno útiles para balancear (A) FPS, (B) latencia/jitter, (C) CPU/GPU:

- `CAPTURE_FPS` (default 10): FPS objetivo de captura.
- `OCR_MIN_INTERVAL_S` (default 0.0): throttling del OCR (0 = OCR en cada ciclo de visión).
- `ROBOFLOW_MIN_INTERVAL_S` (default 0.5): throttling de detección de criaturas.
- `ROBOFLOW_HPMP_MIN_INTERVAL_S` (default 0.0): throttling de detección de barras HP/MP por Roboflow.

Ejemplo (reduce CPU/GPU manteniendo el bot usable):

```powershell
$env:CAPTURE_FPS='20'
$env:OCR_MIN_INTERVAL_S='0.25'
$env:ROBOFLOW_MIN_INTERVAL_S='0.5'
$env:ROBOFLOW_HPMP_MIN_INTERVAL_S='0.2'
poetry run python -m src.main
```

Preset recomendado (i9 + RTX 4070, 2×1080p, Tibia en monitor 2, OBS en monitor 1):

```powershell
$env:FORCE_MONITOR='2'
$env:CAPTURE_FPS='60'
$env:OCR_MIN_INTERVAL_S='0.10'
$env:ROBOFLOW_MIN_INTERVAL_S='0.20'
$env:ROBOFLOW_HPMP_MIN_INTERVAL_S='0.10'
$env:BOT_PROFILE='1'
poetry run python -m src.main
```

Cuando confirmes que va bien, apaga el profiling:

```powershell
Remove-Item Env:BOT_PROFILE -ErrorAction SilentlyContinue
```

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

