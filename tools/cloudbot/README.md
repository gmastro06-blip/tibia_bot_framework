# CloudBot script analysis tooling

This folder contains *offline* analysis tools for the `scripts-master/` dataset.

## What it does

- Parses `scripts-master/**/waypoints.in`
- Generates a simple route viewer PNG for each script
- Extracts inventories of actions used in:
  - `waypoints.in` (`action <name>`)
  - `setup*.json` (`label_actions` / `persistent_actions`)
- Compares waypoint actions used vs actions supported in `scripts-master/global_actions.py`

## Run

From repo root:

```bash
python tools/cloudbot/analyze_scripts.py --scripts-root scripts-master --out-dir reports/cloudbot
```

## UI Viewer

Interactive waypoint viewer:

```bash
python tools/cloudbot/waypoint_viewer_ui.py
```

- Select a script folder (with `waypoints.in`).
- Navigate waypoints with buttons or arrow keys.
- Shows map with points/lines, current waypoint highlighted in red.
- Load and display JSON files (setup_actions.json, actions_in_waypoints.json, route_stats.json) from `reports/cloudbot/<script_name>/` (run the analyzer first).

## Outputs

- `reports/cloudbot/SUMMARY.json`
- Per script folder under `reports/cloudbot/<script_name>/`:
  - `route.png`
  - `route_stats.json`
  - `actions_in_waypoints.json`
  - `setup_actions.json`

## Test Script Loading (sin UI)

Test loading without GUI:

```bash
python tools/cloudbot/test_script_loading.py wasp_ab
# o para todos:
python tools/cloudbot/test_script_loading.py all
```

- Loads waypoints and JSONs for a script.
- Prints summaries to console for verification.
- **Resultado exhaustivo:** 84/84 scripts (100% funcionalidad) cargados correctamente.

## Generate Route Timeline

Generate detailed timeline of route execution:

```bash
python tools/cloudbot/generate_timeline.py wasp_ab --output reports/cloudbot/wasp_ab/timeline.json
```

- Creates JSON with timestamped sequence of waypoints/actions.
- Includes setup context for actions.
