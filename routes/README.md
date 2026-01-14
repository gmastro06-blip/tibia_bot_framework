# Routes (UI / editor)

Esta carpeta existe para que el UI (`run_bot_ui.py`) tenga un lugar “por defecto” donde buscar rutas cuando uses la pestaña **Rutas / Cavebot**.

## Formato soportado

El editor de la UI trabaja con un formato tipo `waypoints.in` (estilo CloudBot):

- `label <name>`
- `action <token>`
- `node (x,y,z)`
- `stand (x,y,z)`
- `rope (x,y,z)`
- `ladder (x,y,z)`
- Comentarios: anteponer `#` deshabilita la línea

Además, dentro del editor hay un macro interno **MOVE** (no es parte del formato original):
- Se guarda como un step `move` dentro de la UI
- Al guardar, se expande a varios `node()` consecutivos (requiere una coordenada base previa)

## Setup (supplies)

Junto a `waypoints.in` suele vivir un `setup_*.json` con campos del estilo:

- `hunt_config`: `mana_name`, `take_mana`, `mana_leave`, `cap_leave`, etc.
- `items`: diccionario de consumibles, p.ej. `{ "mana potion": {"hotkey": "F1", "use": "self"} }`

La UI preserva campos desconocidos (roundtrip): si tu setup tiene más claves, no se pierden.

## Ejemplo listo

Hay un ejemplo mínimo en:

- `routes/example_minimal/waypoints.in`
- `routes/example_minimal/setup_ek.json`

Puedes abrir `waypoints.in` desde la UI y guardar cambios sin configurar nada extra.

> Nota: también existen rutas JSON en `configs/routes/` para el cavebot tradicional (`navigation.route.load_route`).
