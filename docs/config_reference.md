# Config reference (env vars)

This project is **assistant-first** by default (plans actions; no OS input injection). Most behavior can be tuned via environment variables.

## Input safety

- `ACTION_DRIVER`
  - `mock` (default): no OS input.
  - `keyboard` / `wininput`: enables OS key injection via `WindowsKeyboardDriver`.
- `TARGET_HOTKEY`: hotkey for targeting (e.g. `F1`).
- `MINIMAP_CLICK_HOTKEY`: hotkey or combo for minimap click sim (e.g. `CTRL+L`).

**Important**: When OS injection is enabled, only actions marked as `committed` are allowed. Preview actions are blocked.

## Watchdog / fail-closed

- `WATCHDOG_INTERVAL_S` (default `1`): watchdog polling interval.
- `WATCHDOG_STALE_GS_S` (default `5`): seconds without a GameState before considering the pipeline stale.
- `WATCHDOG_STOP_ON_THREAD_DEAD` (default `1`): stop the bot if any critical thread dies.
- `WATCHDOG_STOP_ON_STALE`
  - Default is **fail-closed** when OS injection is enabled.
  - Set to `0` to keep the bot running even if stale (OS injection will still be disabled).

## Capture / ROI configs

- `FORCE_MONITOR` (default `2`): preferred monitor index for capture.
- `CAPTURE_FPS` (default `10`): capture loop target FPS.
- `ROIS_CONFIG`: override ROI JSON file (relative to repo root or absolute path).

## Telemetry / debugging

- `REPLAY_ENABLED`: enable replay crops + JSON snapshots.
- `REPLAY_OUT_DIR` (default `logs/replay`)
- `REPLAY_INTERVAL_MS` (default `2000`)
- `OVERLAY_ENABLED`: enable debug overlay exports.
- `LOG_JSONL_ENABLED` or `LOG_ENABLED`: enable JSONL telemetry logging.
- `LOG_JSONL_OUT_FILE` (default `logs/telemetry.jsonl`)
- `LOG_JSONL_INTERVAL_MS` (default `250`)

## KPI report

Run:

- `python -m tools.report_kpis logs/telemetry.jsonl`
- `python -m tools.report_kpis logs/telemetry.jsonl --last-seconds 60 --format json`
