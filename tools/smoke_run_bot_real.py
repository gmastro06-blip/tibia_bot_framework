from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path


def _add_src_to_syspath() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Real execution smoke test: run the full bot for N seconds, then stop.")
    p.add_argument("--seconds", type=float, default=15.0)
    p.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or 2))
    p.add_argument("--capture-fps", type=float, default=float(os.getenv("CAPTURE_FPS", "8") or 8))
    p.add_argument("--disable-roboflow", action="store_true", help="Disable Roboflow (creatures + HP/MP) for faster smoke.")
    p.add_argument("--enable-overlay", action="store_true", help="Enable OVERLAY exporter during the smoke.")
    p.add_argument("--enable-replay", action="store_true", help="Enable Replay snapshots during the smoke.")
    p.add_argument("--enable-jsonl", action="store_true", help="Enable JSONL telemetry export during the smoke.")
    return p.parse_args()


def main() -> int:
    _add_src_to_syspath()

    args = _parse_args()

    # Make run deterministic-ish.
    os.environ["FORCE_MONITOR"] = str(int(args.monitor))
    os.environ["CAPTURE_FPS"] = str(float(args.capture_fps))

    if args.disable_roboflow:
        # Disable hosted models.
        os.environ["ROBOFLOW_API_KEY"] = ""
        os.environ["ROBOFLOW_WORKSPACE"] = ""
        os.environ["ROBOFLOW_PROJECT"] = ""
        os.environ["ROBOFLOW_VERSION"] = ""
        os.environ["ROBOFLOW_HPMP_LOCAL_MODEL"] = ""
        os.environ["ROBOFLOW_HPMP_WORKSPACE"] = ""
        os.environ["ROBOFLOW_HPMP_PROJECT"] = ""
        os.environ["ROBOFLOW_HPMP_VERSION"] = ""

    # Overlay exporter is opt-in by env.
    if args.enable_overlay:
        os.environ["OVERLAY_ENABLED"] = "1"
        os.environ.setdefault("OVERLAY_INTERVAL_S", "1.0")
        os.environ.setdefault("OVERLAY_OUT_DIR", "logs/debug_overlay")
    else:
        os.environ["OVERLAY_ENABLED"] = "0"

    stop_event = threading.Event()

    # RuntimeConfig is only needed for UI toggles/replay/jsonl.
    from runtime_config import RuntimeConfig

    cfg = RuntimeConfig()

    # Keep assistant mode on; this project doesn't inject real inputs.
    cfg.update_assistant(enabled=True, confirm_actions=True, sound_alerts=False)

    # Optional exporters
    cfg.update_replay(enabled=bool(args.enable_replay), interval_ms=1500, out_dir="logs/replay")
    cfg.update_logging(enabled=bool(args.enable_jsonl), interval_ms=250, out_file="logs/telemetry.jsonl")

    from main import run_bot

    print(
        "▶ Smoke real bot run"
        f" | seconds={args.seconds} monitor={args.monitor} capture_fps={args.capture_fps}"
        f" | disable_roboflow={bool(args.disable_roboflow)} overlay={bool(args.enable_overlay)}"
        f" replay={bool(args.enable_replay)} jsonl={bool(args.enable_jsonl)}"
    )

    def _stop_later() -> None:
        time.sleep(max(0.5, float(args.seconds)))
        stop_event.set()

    timer = threading.Thread(target=_stop_later, daemon=True)
    timer.start()

    try:
        run_bot(stop_event=stop_event, runtime_config=cfg)
    except KeyboardInterrupt:
        stop_event.set()
    except Exception as e:
        stop_event.set()
        print(f"❌ Bot crashed: {e}")
        raise

    print("✅ Smoke finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
