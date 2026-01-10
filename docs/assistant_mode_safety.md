# Tibia Bot Framework — Modo asistente (sin inputs) y checklist de seguridad

## Qué significa “cero riesgo” aquí
En este repo, **“cero riesgo”** significa:

- **El proceso NO debe enviar inputs al sistema operativo** (ni teclado ni ratón), es decir: nada de `keyboard`, `pyautogui`, `SendInput`, etc.
- El bot se limita a **capturar pantalla + analizar + recomendar**, y a lo sumo **registrar** qué acciones *haría*.

En la práctica, eso se consigue manteniéndose en el circuito:

- `ActionRequest`: representa una acción como **datos** (ej. `kind="move"`, `value="north"`).
- `MockInputDriver`: **solo guarda/loguea** esas acciones; **no ejecuta nada**.

Y evitando “cablear” (conectar) cualquier ejecutor de inputs reales.

## Componentes que SOLO simulan / no inyectan inputs
Estas piezas son seguras en el sentido de que **no generan input OS**:

- `ActionRequest`, `InputDriver`, `MockInputDriver` — acciones como datos + driver mock
- `SimulationConfig`, `AssistantConfig`, `TelemetrySnapshot` — simulación de flags + configuración + telemetría
- `StepNavigator.preview()` — calcula el siguiente paso **sin mutar** (modo confirmación)
- `build_requests_from_waypoint_action()` — convierte acciones de waypoint a `ActionRequest` (preview/committed)
- `ReplayRecorder` — guarda JSON/crops en disco (telemetría)
- `RoboflowInference` — visión/inferencia

## El “punto rojo”: dónde sí hay input real
Hay un módulo que **sí** inyecta teclas a nivel OS:

- `src/action/movement.py` (`MoveExecutor`) usa `keyboard.press_and_release(...)`

Ese archivo por sí solo no hace nada; el riesgo aparece si lo conectas a la lógica de decisión (por ejemplo, llamándolo desde `decision_thread`).

## Regla práctica
Si quieres mantenerlo **100% asistente**:

- Mantén el flujo en: **recomendación → `ActionRequest` → `MockInputDriver` → logs/UI**
- No uses/instancies ejecutores que llamen a librerías de input OS.

## “Grep de seguridad” / guardrail automático
Para evitar regresiones, se puede añadir un test que falle si:

- aparece `import keyboard`, `keyboard.` o `pyautogui` dentro de `src/`.

Puedes permitir excepciones explícitas (whitelist), por ejemplo solo `src/action/movement.py`.

### Cómo exportar este documento a PDF
Opción A (VS Code):
- Abre este archivo y usa el preview de Markdown.
- Ejecuta `Ctrl+Shift+P` → **Markdown: Open Preview**.
- En el preview, usa **Print** (o el menú del browser/preview) y selecciona **Save as PDF**.

Opción B (extensión):
- Instala la extensión **Markdown PDF**.
- `Ctrl+Shift+P` → **Markdown PDF: Export (pdf)**.
