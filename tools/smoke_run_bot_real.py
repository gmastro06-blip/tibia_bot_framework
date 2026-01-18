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
    p.add_argument(
        "--out-base",
        type=str,
        default="",
        help=(
            "Optional base output directory. When set, writes: "
            "<out-base>/debug_overlay, <out-base>/replay, <out-base>/telemetry.jsonl, "
            "and defaults DECISION_TRACE_PATH to <out-base>/decision_trace.jsonl if unset."
        ),
    )
    p.add_argument(
        "--confirm-actions",
        action="store_true",
        default=True,
        help="Require confirm pulse to commit actions (safe default).",
    )
    p.add_argument(
        "--no-confirm-actions",
        action="store_true",
        help="Disable confirm gating so auto-commit policies can apply.",
    )

    # Cavebot (optional; kept OFF by default to preserve old smoke semantics).
    p.add_argument(
        "--enable-cavebot",
        action="store_true",
        help="Enable cavebot during the smoke run (will emit movement suggestions/actions).",
    )
    p.add_argument(
        "--route",
        type=str,
        default=os.getenv("CAVEBOT_ROUTE_PATH", "configs/route.json") or "configs/route.json",
        help="Route path for cavebot (JSON).",
    )
    p.add_argument(
        "--cavebot-mode",
        type=str,
        default=os.getenv("CAVEBOT_MODE", "pos") or "pos",
        help="Cavebot mode: pos|steps.",
    )
    return p.parse_args()


def main() -> int:
    _add_src_to_syspath()

    try:
        from console_sanitize import maybe_install_no_emoji_output

        maybe_install_no_emoji_output()
    except Exception:
        pass

    args = _parse_args()

    # Optional output base.
    out_base = (str(getattr(args, "out_base", "") or "").strip() or "")
    if out_base:
        try:
            Path(out_base).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        # Default DecisionTrace path if the caller didn't set it.
        if not (os.getenv("DECISION_TRACE_PATH", "") or "").strip():
            os.environ["DECISION_TRACE_PATH"] = str(Path(out_base) / "decision_trace.jsonl")

    # Make run deterministic-ish.
    os.environ["FORCE_MONITOR"] = str(int(args.monitor))
    os.environ["CAPTURE_FPS"] = str(float(args.capture_fps))

    # Safe OCR defaults: isolate EasyOCR into a subprocess with a hard timeout.
    # This prevents the vision thread from stalling on EasyOCR/Torch hangs.
    os.environ.setdefault("OCR_ENABLED", "1")
    os.environ.setdefault("OCR_ISOLATE_PROCESS", "1")
    os.environ.setdefault("OCR_READTEXT_TIMEOUT_MS", "250")
    os.environ.setdefault("HPMP_OCR_ENABLED", "1")
    # Keep CAP OCR off by default (more fragile + detail=1 heavy).
    os.environ.setdefault("CAP_OCR_ENABLED", "0")

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
        if out_base:
            os.environ["OVERLAY_OUT_DIR"] = str(Path(out_base) / "debug_overlay")
        else:
            os.environ.setdefault("OVERLAY_OUT_DIR", "logs/debug_overlay")
    else:
        os.environ["OVERLAY_ENABLED"] = "0"

    stop_event = threading.Event()

    # RuntimeConfig is only needed for UI toggles/replay/jsonl.
    from runtime_config import RuntimeConfig

    cfg = RuntimeConfig()

    # Assistant gating: safe default is confirm_actions=True.
    confirm_actions = bool(getattr(args, "confirm_actions", True)) and not bool(getattr(args, "no_confirm_actions", False))
    cfg.update_assistant(enabled=True, confirm_actions=confirm_actions, sound_alerts=False)

    # Cavebot (opt-in via CLI flag or env var).
    cavebot_enabled_env = (os.getenv("CAVEBOT_ENABLED", "0") or "0").strip().lower() in {"1", "true", "yes", "y", "on"}
    cavebot_enabled = bool(getattr(args, "enable_cavebot", False)) or bool(cavebot_enabled_env)
    if cavebot_enabled:
        try:
            route_path = str(getattr(args, "route", "") or os.getenv("CAVEBOT_ROUTE_PATH", "configs/route.json") or "configs/route.json")
            cavebot_mode = str(getattr(args, "cavebot_mode", "") or os.getenv("CAVEBOT_MODE", "pos") or "pos")
            cfg.update_cavebot(enabled=True, route_path=route_path, mode=cavebot_mode)
        except Exception:
            pass

    # Optional exporters
    if out_base:
        cfg.update_replay(enabled=bool(args.enable_replay), interval_ms=1500, out_dir=str(Path(out_base) / "replay"))
        cfg.update_logging(enabled=bool(args.enable_jsonl), interval_ms=250, out_file=str(Path(out_base) / "telemetry.jsonl"))
    else:
        cfg.update_replay(enabled=bool(args.enable_replay), interval_ms=1500, out_dir="logs/replay")
        cfg.update_logging(enabled=bool(args.enable_jsonl), interval_ms=250, out_file="logs/telemetry.jsonl")

    from main import run_bot

    print(
        "▶ Smoke real bot run"
        f" | seconds={args.seconds} monitor={args.monitor} capture_fps={args.capture_fps}"
        f" | disable_roboflow={bool(args.disable_roboflow)} overlay={bool(args.enable_overlay)}"
        f" replay={bool(args.enable_replay)} jsonl={bool(args.enable_jsonl)}"
        + (f" out_base={out_base}" if out_base else "")
        + (f" confirm_actions={int(confirm_actions)}" if out_base or bool(getattr(args, 'no_confirm_actions', False)) else "")
    )

    def _stop_later() -> None:
        time.sleep(max(0.5, float(args.seconds)))
        stop_event.set()

    timer = threading.Thread(target=_stop_later, daemon=True)
    timer.start()

    try:
        try:
            run_bot(stop_event=stop_event, runtime_config=cfg)
        except KeyboardInterrupt:
            # If the user manually interrupts during HUD-move tests, exit cleanly.
            stop_event.set()
        except Exception as e:
            stop_event.set()
            print(f"❌ Bot crashed: {e}")
            raise
    except KeyboardInterrupt:
        stop_event.set()
        # Ignore secondary Ctrl+C while shutting down.
    finally:
        stop_event.set()

    print("✅ Smoke finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
