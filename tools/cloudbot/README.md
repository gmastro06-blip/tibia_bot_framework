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

## Outputs

- `reports/cloudbot/SUMMARY.json`
- Per script folder under `reports/cloudbot/<script_name>/`:
  - `route.png`
  - `route_stats.json`
  - `actions_in_waypoints.json`
  - `setup_actions.json`

## Notes

- Jump flagging threshold is controlled by `CLOUDBOT_JUMP_THRESHOLD` (default `50`, Manhattan distance between consecutive points).
- The plot is 2D (x,y) with color indicating z.
