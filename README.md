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

## Quick start (sin coords visibles) ✅

Si tu cliente **no muestra coordenadas en pantalla**, el OCR de `coords_ocr` puede dar falsos positivos. El modo más estable recomendado es:

- `COORDS_PROVIDER=disabled` (no inventa coords)
- `CAVEBOT_MODE=steps` (cavebot por pasos, no requiere coords absolutas)

En PowerShell:

```powershell
./scripts/profile_no_coords_steps.ps1
```

## Quick start (overlay debug) 🧪

Para generar frames anotados en `logs/debug_overlay/` con ROIs y diagnósticos:

```powershell
./scripts/profile_overlay_debug.ps1 -Monitor 2
```

Opcional (cambiar lista de ROIs y frecuencia):

```powershell
./scripts/profile_overlay_debug.ps1 -Monitor 2 -Rois 'coords_ocr,minimap_content,hp_low_bar,mp_low_bar' -IntervalS 0.5 -TilePx 32
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
- **Rutas / Cavebot**: editor de rutas (waypoints.in) + setup (setup_*.json)

Notas:
- El botón **Minimizar** usa system tray si están disponibles `pystray` + `Pillow`; si no, solo minimiza la ventana.
- El editor de rutas es *asistente-only*: modifica archivos, no inyecta inputs.

### UI settings (persistencia)

La UI guarda/recupera tu configuración en `configs/ui_settings.json`.

- Se guarda automáticamente al pulsar **Iniciar** (y también con **Guardar UI**).
- Puedes cambiar la ubicación con `UI_SETTINGS_FILE` (ruta relativa al repo o absoluta).

Ejemplos:

```powershell
$env:UI_SETTINGS_FILE = 'configs/ui_settings.my_profile.json'
poetry run python run_bot_ui.py
```

La pestaña **Configuración** incluye presets rápidos:
- **Preset (replay/log)**: `Off`, `Debug`, `Soak`, `Soak Full` (este último también habilita Overlay con `Full HUD`).
- **Overlay Preset**: `Minimal`, `Debug HUD`, `Full HUD`.

Nota: el preset **Soak Full** escribe por defecto en:
- `logs/replay_soak/`
- `logs/debug_overlay_soak/`

Además, al pulsar **Iniciar** con preset `Soak`/`Soak Full`, la UI crea automáticamente un subdirectorio por sesión:
- `logs/replay_soak/YYYYMMDD_HHMMSS/`
- `logs/debug_overlay_soak/YYYYMMDD_HHMMSS/`
- `logs/telemetry_soak_YYYYMMDD_HHMMSS.jsonl`

La UI también incluye botones para abrir rápidamente el último soak:
- **Abrir último soak** (carpetas replay + overlay)
- **Abrir JSONL soak**
- **Abrir TODO soak**
- **Soak timeline (HTML)** (genera y abre el reporte)
- **Abrir timeline**
Y muestra el `Soak run_id` actual cuando aplica.

### Soak timeline report (HTML)

Si tienes outputs de soak (o `logs/telemetry.jsonl` + replay/overlay), puedes generar un HTML con una tabla timeline que enlaza cada fila del JSONL con el replay JSON y el overlay PNG más cercano en tiempo:

```powershell
poetry run python tools/soak_timeline_report.py
```

Por defecto auto-elige el último `logs/replay_soak/*`, `logs/debug_overlay_soak/*` y `logs/telemetry_soak_*.jsonl` (y si no, cae a `logs/replay/`, `logs/debug_overlay/`, `logs/telemetry.jsonl`). Output: `logs/soak_timeline.html`.

Filtros útiles:

```powershell
# Solo commits (acciones confirmadas)
poetry run python tools/soak_timeline_report.py --only-committed

# Solo eventos relacionados con acciones
poetry run python tools/soak_timeline_report.py --only-action-events

# Buscar una acción por substring (case-insensitive)
poetry run python tools/soak_timeline_report.py --grep-action "move:west"
```

Atajo PowerShell (genera + abre):

```powershell
scripts/open_soak_timeline.ps1 -OnlyActionEvents
```

## Cavebot routes (record + validate)

El cavebot consume rutas en formato JSON compatible con `navigation.route.load_route()`:

```json
[{"x": 32561, "y": 32496, "z": 7, "name": "wp0000", "action": "rope"}]
```

### Grabar una ruta (hotkeys + clipboard)

Recorder interactivo (seguro): lee coordenadas del portapapeles y escribe `route.json`.

```powershell
poetry run python tools/route_recorder.py --out configs/route.json
```

- Hotkeys por defecto: `F12` agrega waypoint, `F11` agrega waypoint con `action=stand`.
- Comandos en consola: `label X`, `action Y`, `once rope|shovel|loot`, `add`, `undo`, `list`, `stop`.

Si no puedes usar hotkeys (permisos/antivirus), usa modo consola:

```powershell
poetry run python tools/route_recorder.py --no-hotkeys --out configs/route.json
```

### Validar una ruta

Resumen rápido de calidad (duplicados, saltos grandes, cambios de piso):

```powershell
poetry run python tools/validate_route.py configs/route.json --print
```

Opcional: limpiar duplicados consecutivos y escribir otra ruta:

```powershell
poetry run python tools/validate_route.py configs/route.json --drop-duplicates --out configs/route.cleaned.json
```

## Coordenadas (pos_x/pos_y): `COORDS_PROVIDER`

El bot puede obtener coordenadas de distintas fuentes. Esto es importante porque **si tu cliente no muestra coords en pantalla**, el OCR puede producir falsos positivos.

### Recomendado si NO hay coords visibles (estable)

En este repo, el camino más estable cuando tu cliente **no** muestra coordenadas es:

- Deshabilitar coords (`COORDS_PROVIDER=disabled`) para evitar falsos positivos.
- Usar cavebot por pasos (`CAVEBOT_MODE=steps`) con `StepNavigator` (no requiere coords absolutas).

Perfil listo (PowerShell):

```powershell
./scripts/profile_no_coords_steps.ps1
```

Variables de entorno:

- `COORDS_PROVIDER=ocr|env|file|minimap|disabled` (default: `ocr`)
  - `ocr`: OCR de la ROI `coords_ocr`.
  - `env`: lee `PLAYER_X`, `PLAYER_Y` y opcional `PLAYER_Z`.
  - `file`: lee `COORDS_FILE` (JSON con `{x,y,z}` o lista `[x,y,z]`).
  - `minimap`: **experimental**. No hace OCR de coords: estima movimiento midiendo el desplazamiento del `minimap_content` y lo acumula sobre una **seed**.
  - `disabled`: fuerza coords `None` (y el cavebot en modo `pos` se degrada a “no coords”).

Ejemplos (PowerShell):

```powershell
# Deshabilitar coords OCR (recomendado si NO hay coords visibles)
$env:COORDS_PROVIDER='disabled'

# Y usar cavebot por pasos (no requiere coords)
$env:CAVEBOT_MODE='steps'

# Proveer coords por env vars
$env:COORDS_PROVIDER='env'
$env:PLAYER_X='32561'
$env:PLAYER_Y='32496'
$env:PLAYER_Z='7'

# Proveer coords por archivo JSON
$env:COORDS_PROVIDER='file'
$env:COORDS_FILE='logs/coords.json'

# Minimap (experimental): necesitas una seed absoluta + minimap_content bien calibrado
$env:COORDS_PROVIDER='minimap'
$env:COORDS_SEED_X='32561'
$env:COORDS_SEED_Y='32496'
$env:COORDS_SEED_Z='7'

# Alternativa: seed por archivo (mismo formato que COORDS_FILE)
$env:COORDS_SEED_FILE='logs/coords_seed.json'
```

Notas para `minimap` (experimental):

- Esto **nunca inventa coords**: si no hay seed o la correlación es mala, devuelve `None`/mantiene la última.
- Si ves drift, ajusta estos knobs:
  - `MINIMAP_TILE_PX` (default `4`)
  - `MINIMAP_PHASECORR_MIN_RESPONSE` (default `0.15`)
  - `MINIMAP_MAX_SHIFT_PX` (default `32`)
  - `MINIMAP_MAX_STEP_PER_FRAME` (default `3`)
  - `MINIMAP_INVERT_X`, `MINIMAP_INVERT_Y` (si el signo te queda al revés)

Herramienta de debug (recomendado para calibrar):

```powershell
poetry run python tools/watch_minimap_motion.py --monitor 2 --seconds 30 --interval-ms 120 --print --save-crops

Chequeo rápido de ROI (si `accepted==0` sospecha que `minimap_content` no apunta al minimapa real):

```powershell
poetry run python tools/minimap_roi_check.py --monitor 2 --seconds 20 --print --save-overlay --save-diff
```
```

Modo integrado (seed + coords acumuladas):

```powershell
# Seed por env (o usa --seed-x/--seed-y/--seed-z)
$env:COORDS_SEED_X='32561'
$env:COORDS_SEED_Y='32496'
$env:COORDS_SEED_Z='7'

# Imprime dx/dy y también x/y acumuladas; además escribe un JSON compatible con COORDS_FILE
poetry run python tools/watch_minimap_motion.py --monitor 2 --seconds 60 --print --out-coords logs/coords_minimap.json
```

Tip: si prefieres un flujo más controlado, puedes hacer que el bot consuma un archivo (en vez de `COORDS_PROVIDER=minimap`):

```powershell
$env:COORDS_PROVIDER='file'
$env:COORDS_FILE='logs/coords_minimap.json'
```

Si quieres cavebot sin coords, usa `CAVEBOT_MODE=steps`.

## Behavior Tree (assistant-only)

La capa de decisión puede planificar acciones usando un Behavior Tree (lib `py-trees`).

- No inyecta inputs: solo genera `ActionRequest` (preview/committed) para logging/UI.
- Se puede desactivar si quieres volver al planner inline.

Env vars:

```powershell
# Habilitar (default)
$env:BT_ENABLED='1'

# Deshabilitar
$env:BT_ENABLED='0'
```

### Fuente externa simple (clipboard -> `COORDS_FILE`)

Si tu cliente no muestra coords en pantalla, una opción práctica es alimentar coords desde afuera y que el bot las lea con `COORDS_PROVIDER=file`.

1) Arranca el writer (lee el portapapeles y escribe JSON):

```powershell
poetry run python tools/coords_file_writer.py --out logs/coords.json --print
```

2) En otra consola (o antes), configura el bot:

```powershell
$env:COORDS_PROVIDER='file'
$env:COORDS_FILE='logs/coords.json'
```

### Perfil recomendado (minimap, experimental)

Si quieres probar coords por minimapa (requiere seed):

```powershell
./scripts/profile_minimap.ps1 -Monitor 2 -SeedX 32561 -SeedY 32496 -SeedZ 7
```

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

## Soak test (estabilidad runtime, sin inputs)

Para validar estabilidad “real” del pipeline (captura + visión + decisión + writers) durante varios minutos, usa el runner:

```bash
poetry run python -m tools.smoke_run_bot_real --help
```

### Run rápido (foreground)

Útil para ver errores inmediatos en consola:

```powershell
$env:BOT_PROFILE='1'
$env:FORCE_MONITOR='2'
$env:CAPTURE_FPS='10'
poetry run python -u -m tools.smoke_run_bot_real --seconds 60 --enable-overlay --enable-replay --enable-jsonl --disable-roboflow
```

### Run recomendado (detached + logs, 10 min)

Alternativa rápida (script):

```powershell
./scripts/profile_soak_debug.ps1 -Monitor 2 -Seconds 600 -CaptureFps 10

# Para habilitar Roboflow durante el soak:
./scripts/profile_soak_debug.ps1 -Monitor 2 -Seconds 600 -CaptureFps 10 -Roboflow enabled
```

En PowerShell es más robusto lanzarlo como proceso separado (evita Ctrl+C accidental y problemas de encoding al pipear a archivo):

```powershell
New-Item -ItemType Directory -Force -Path logs | Out-Null

$outLog = 'logs\soak_run.log'
$errLog = 'logs\soak_run.err.log'
Remove-Item -ErrorAction SilentlyContinue $outLog, $errLog

$env:BOT_PROFILE = '1'
$env:FORCE_MONITOR = '2'
$env:CAPTURE_FPS = '10'
$env:PYTHONIOENCODING = 'utf-8'

$p = Start-Process -FilePath 'poetry' -ArgumentList @(
  'run','python','-u','-m','tools.smoke_run_bot_real',
  '--seconds','600',
  '--enable-overlay','--enable-replay','--enable-jsonl',
  '--disable-roboflow'
) -NoNewWindow -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru

"Started PID=$($p.Id)"
```

Monitorear el log en vivo:

```powershell
Get-Content -Encoding utf8 logs\soak_run.log -Tail 30 -Wait
```

Parar el proceso:

```powershell
Stop-Process -Id <PID> -Force
```

Outputs esperados durante el soak:
- `logs/soak_run.log` (stdout del runner)
- `logs/soak_run.err.log` (stderr)
- `logs/debug_overlay/` (frames anotados si `--enable-overlay`)
- `logs/replay/` (snapshots ROI+JSON si `--enable-replay`)
- `logs/telemetry.jsonl` (telemetría + eventos si `--enable-jsonl`)

Overlay (frames anotados) incluye:
- Caja del `game_viewport` + grilla de tiles (si `OVERLAY_TILE_GRID=1`)
- Boxes de Roboflow (si existen)
- ROIs dibujadas (lista configurable con `OVERLAY_ROIS`)
- Líneas de diagnóstico (ROI offset, viewport auto, minimap debug, coords_provider)

Knobs útiles:
- `OVERLAY_ROIS='coords_ocr,minimap_content,hp_low_bar,mp_low_bar'` (CSV)
- `OVERLAY_TILE_PX='32'` (o `TIBIA_TILE_PX`)
- `OVERLAY_INTERVAL_S='1.0'`

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

### Nota sobre ROIs de HP/MP

Para fullscreen real, las ROIs superiores se calibran desde `y=0.0` y el OCR usa como fallback robusto el ROI `hpmp_top_strip` (separa izquierda=HP, derecha=MP).


## ROIs / Calibración

### Si movés el HUD (barras / paneles)

Las ROIs están definidas para un layout específico. Si movés las barras/paneles dentro del juego, esa calibración deja de coincidir.

- Recomendado: mantené el HUD fijo (un layout por perfil).
- Si usás varios layouts, guardá varios JSON y elegí cuál cargar con `ROIS_CONFIG`.

Ejemplos (PowerShell):

- Usar un perfil de ROIs específico:
  - ` $env:ROIS_CONFIG = 'configs/rois_guess_1920x1080_mi_layout.json' `
  - `poetry run python -m src.main`

- Usar la calibración resuelta guardada en `data/ROIs_resueltos.json`:
  - ` $env:ROIS_CONFIG = 'data/ROIs_resueltos.json' `

Tip: podés duplicar el archivo base (`configs/rois_guess_1920x1080.json`) con otro nombre y recalibrar sobre ese.

### Auto-ajuste (ancla) cuando movés el HUD (experimental)

Si querés que el bot se recupere automáticamente cuando movés el HUD, podés configurar un **anchor**:
se guarda una plantilla (imagen chica) y en runtime se hace template-matching para calcular un offset global
que se aplica a todas las ROIs.

1) Crear el anchor (seleccioná algo bien estable: esquina de un panel, un ícono fijo, etc.)

```powershell
$env:FORCE_MONITOR='2'
poetry run python -m tools.create_anchor_template --base right_hud_panel --write
```

Esto guarda la plantilla en `data/anchors/hud_anchor.png` y escribe `_anchor` dentro de tu config de ROIs.

2) Correr normalmente: el bot auto-alinea ROIs si `_anchor` existe.

Opcional:
- Desactivar ancla: ` $env:ANCHOR_ENABLED='0' `
## Configuración

- ROIs por resolución:
  - `configs/rois_guess.json`
  - `configs/rois_guess_1920x1080.json`

