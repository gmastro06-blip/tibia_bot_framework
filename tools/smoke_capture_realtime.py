from __future__ import annotations

"""Smoke test: capture stays live in real-time.

Writes one JSONL line per tick to `logs/capture_health.jsonl`.

Usage (PowerShell):
    .venv/Scripts/python.exe tools/smoke_capture_realtime.py --seconds 5

Notes:
- This tool is best-effort and never crashes the bot framework.
- It does not start vision/decision threads; it only exercises capture.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def _add_src_to_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def _jsonl_append(path: Path, event: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    try:
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return

    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--out", type=str, default="logs/capture_health.jsonl")
    args = parser.parse_args()

    # Safe defaults: avoid OBS projector unless explicitly requested.
    os.environ.setdefault("CAPTURE_BACKEND", "dxgi")
    os.environ.setdefault("CAPTURE_TARGET", "client")
    os.environ.setdefault("CAPTURE_TITLE_INCLUDE", "Tibia")
    os.environ.setdefault("CAPTURE_TITLE_EXCLUDE", "Proyector|Projector|OBS")

    # Optional: pin capture to the monitor where OBS is.
    # Supported env vars:
    #   - FORCE_MONITOR
    #   - OBS_MONITOR_INDEX
    #   - CAPTURE_OBS_MONITOR
    force_monitor: int | None = None
    raw_force = (os.getenv("FORCE_MONITOR", "") or "").strip()
    if raw_force:
        try:
            force_monitor = int(float(raw_force))
        except Exception:
            force_monitor = None
    if force_monitor is None:
        raw_obs = (
            (os.getenv("CAPTURE_OBS_MONITOR", "") or "").strip()
            or (os.getenv("OBS_MONITOR_INDEX", "") or "").strip()
        )
        if raw_obs:
            try:
                force_monitor = int(float(raw_obs))
            except Exception:
                force_monitor = None
            if force_monitor is not None:
                os.environ.setdefault("CAPTURE_OBS_FALLBACK_MONITOR", str(force_monitor))

    _add_src_to_path()

    try:
        import main as bot_main
    except Exception as e:
        print(f"failed to import src.main: {e}")
        return 2

    out_path = Path(args.out)

    cap = bot_main.build_capture_backend(force_monitor=force_monitor)

    t_end = time.time() + max(0.2, float(args.seconds))
    period = 1.0 / max(1.0, float(args.fps))

    n_ok = 0
    n_none = 0
    n_ex = 0

    while time.time() < t_end:
        t0 = time.time()
        frame = None
        ex = None
        try:
            frame = cap.capture()
        except Exception as e:  # pragma: no cover
            ex = str(e)

        now = time.time()

        ok = frame is not None
        if ex is not None:
            n_ex += 1
        elif ok:
            n_ok += 1
        else:
            n_none += 1

        evt = {
            "ts": float(now),
            "ok": bool(ok),
            "exception": ex,
            "backend": str(getattr(cap, "capture_backend", "") or type(cap).__name__),
            "capture_target": str(getattr(cap, "capture_target", "") or os.getenv("CAPTURE_TARGET", "client")),
            "state": str(getattr(cap, "capture_state", "") or "unknown"),
            "target_hwnd": int(getattr(cap, "client_hwnd", 0) or getattr(cap, "hwnd", 0) or 0),
            "target_title": str(getattr(cap, "client_title", "") or ""),
            "target_found": bool(getattr(cap, "target_found", False)),
            "reason": str(getattr(cap, "target_reason", "") or ""),
            "fail_count": int(getattr(cap, "fail_count", 0) or 0),
            "reacquire_count": int(getattr(cap, "reacquire_count", 0) or 0),
            "last_error": str(getattr(cap, "last_error", "") or ""),
            "shape": (list(frame.shape) if ok and hasattr(frame, "shape") else None),
        }
        _jsonl_append(out_path, evt)

        # Light console line for humans.
        title = evt["target_title"]
        if len(title) > 60:
            title = title[:57] + "..."
        print(
            f"ok={int(evt['ok'])} backend={evt['backend']} state={evt['state']} hwnd={evt['target_hwnd']} title={title} reason={evt['reason']}"
        )

        dt = time.time() - t0
        to_sleep = max(0.0, period - dt)
        if to_sleep:
            time.sleep(to_sleep)

    print(f"done: ok={n_ok} none={n_none} ex={n_ex} out={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
