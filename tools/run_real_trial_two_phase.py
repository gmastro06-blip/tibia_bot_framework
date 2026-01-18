from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _add_src_to_syspath() -> None:
    # Keep imports working when this script is invoked directly.
    root = _repo_root()
    src_dir = root / "src"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _set_common_env(env: dict[str, str], *, monitor: int, capture_fps: float) -> None:
    env["FORCE_MONITOR"] = str(int(monitor))
    env["CAPTURE_FPS"] = str(float(capture_fps))

    # Diagnostics: try to dump Python stacks on fatal native crashes.
    env.setdefault("PYTHONFAULTHANDLER", "1")

    # Strongly recommended observability.
    env.setdefault("OVERLAY_ENABLED", "1")
    env.setdefault("OVERLAY_INTERVAL_S", "1.0")
    env.setdefault("REPLAY_ENABLED", "1")
    env.setdefault("REPLAY_INTERVAL_MS", "1500")
    env.setdefault("LOG_JSONL_ENABLED", "1")
    env.setdefault("LOG_JSONL_INTERVAL_MS", "250")

    # OCR defaults (safe): enable OCR in an isolated subprocess with a hard timeout.
    # This keeps the vision thread responsive even if EasyOCR/Torch hangs.
    env.setdefault("OCR_ENABLED", "1")
    env.setdefault("OCR_ISOLATE_PROCESS", "1")
    env.setdefault("OCR_READTEXT_TIMEOUT_MS", "250")

    # Default OCR surface area: HP/MP only (most useful + least risky).
    env.setdefault("HPMP_OCR_ENABLED", "1")
    env.setdefault("CAP_OCR_ENABLED", "0")

    # Emit bar diagnostics (top strip vs legacy low bars) in console and JSONL.
    env.setdefault("HPMP_LOG_BARS", "1")
    env.setdefault("HPMP_LOG_BARS_EVERY_S", "1.5")


def _write_summary(*, decision_trace_path: Path, out_txt: Path) -> None:
    try:
        out_txt.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    cmd = [sys.executable, str(_repo_root() / "tools" / "decision_trace_summary.py"), str(decision_trace_path)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_repo_root()))
        out_txt.write_text((p.stdout or "") + ("\n" + p.stderr if p.stderr else ""), encoding="utf-8")
    except Exception as e:
        try:
            out_txt.write_text(f"summary error: {e}\n", encoding="utf-8")
        except Exception:
            pass


def _run_smoke_subprocess(*, seconds: float, monitor: int, capture_fps: float, out_base: Path, disable_roboflow: bool, confirm_actions: bool, extra_env: dict[str, str]) -> int:
    env = dict(os.environ)
    env.update({k: str(v) for k, v in (extra_env or {}).items()})
    _set_common_env(env, monitor=monitor, capture_fps=capture_fps)

    # Ensure DecisionTrace uses per-run output.
    env["DECISION_TRACE_PATH"] = str(out_base / "decision_trace.jsonl")

    cmd = [
        sys.executable,
        "-u",
        "-m",
        "tools.smoke_run_bot_real",
        "--seconds",
        str(float(seconds)),
        "--monitor",
        str(int(monitor)),
        "--capture-fps",
        str(float(capture_fps)),
        "--enable-cavebot",
        "--route",
        str(extra_env.get("CAVEBOT_ROUTE_PATH", "configs/route.json")),
        "--cavebot-mode",
        str(extra_env.get("CAVEBOT_MODE", "pos")),
        "--out-base",
        str(out_base),
        "--enable-overlay",
        "--enable-replay",
        "--enable-jsonl",
    ]
    if disable_roboflow:
        cmd.append("--disable-roboflow")
    if not confirm_actions:
        cmd.append("--no-confirm-actions")

    p = subprocess.run(cmd, cwd=str(_repo_root()), env=env)
    return int(p.returncode)


def main() -> int:
    _add_src_to_syspath()

    ap = argparse.ArgumentParser(
        description=(
            "Two-phase real trial runner: (1) dry-run (no OS input), (2) live injection (optional). "
            "Collects replay/overlay/decision_trace and writes a decision_trace summary."
        )
    )
    ap.add_argument("--seconds-dry", type=float, default=25.0)
    ap.add_argument("--seconds-live", type=float, default=15.0)
    ap.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or 2))
    ap.add_argument("--capture-fps", type=float, default=float(os.getenv("CAPTURE_FPS", "10") or 10))
    ap.add_argument("--route", type=str, default=os.getenv("CAVEBOT_ROUTE_PATH", "configs/route.json") or "configs/route.json")
    ap.add_argument("--cavebot-mode", type=str, default=os.getenv("CAVEBOT_MODE", "pos") or "pos")
    ap.add_argument(
        "--roboflow",
        choices=["off", "on"],
        default=(os.getenv("TRIAL_ROBOFLOW", "off") or "off").strip().lower(),
        help=(
            "Roboflow mode: off (default, avoids hosted inference stalls), on (enable Roboflow if configured). "
            "If you see STALE_GS, try --roboflow off."
        ),
    )
    ap.add_argument(
        "--ocr",
        choices=["off", "hpmp", "all"],
        default=(os.getenv("TRIAL_OCR", "hpmp") or "hpmp").strip().lower(),
        help=(
            "OCR mode: off (most stable), hpmp (enable HP/MP OCR only), "
            "all (enable HP/MP + CAP OCR)."
        ),
    )
    ap.add_argument(
        "--ocr-gpu",
        choices=["auto", "0", "1"],
        default=(os.getenv("OCR_GPU", "0") or "0").strip().lower(),
        help="OCR GPU mode: auto (don't set OCR_GPU), 0 (force CPU), 1 (force GPU)",
    )
    ap.add_argument(
        "--ocr-init-log",
        action="store_true",
        help="Print OCR init diagnostics (sets OCR_INIT_LOG=1).",
    )
    ap.add_argument("--out-dir", type=str, default="logs/trials")
    ap.add_argument("--live", action="store_true", help="Run the live injection phase (sends OS input).")
    ap.add_argument(
        "--i-accept-live-input-risk",
        action="store_true",
        help="Required with --live. Acknowledge this can send real keyboard inputs to the foreground window.",
    )
    args = ap.parse_args()

    out_base = Path(args.out_dir) / _timestamp()
    try:
        out_base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # ---------- Phase 1: dry-run (safe) ----------
    phase1 = out_base / "dry_run"
    phase1.mkdir(parents=True, exist_ok=True)

    # Optional OCR override (default remains stable/off).
    ocr_env: dict[str, str] = {}
    try:
        mode = str(getattr(args, "ocr", "off") or "off").strip().lower()
    except Exception:
        mode = "off"

    if mode in {"hpmp", "all"}:
        ocr_env["OCR_ENABLED"] = "1"
        ocr_env["OCR_ISOLATE_PROCESS"] = "1"
        ocr_env["OCR_READTEXT_TIMEOUT_MS"] = os.getenv("OCR_READTEXT_TIMEOUT_MS", "250") or "250"
        ocr_env["HPMP_OCR_ENABLED"] = "1"
        ocr_env["CAP_OCR_ENABLED"] = "1" if mode == "all" else "0"
    elif mode == "off":
        # Keep defaults from _set_common_env (OCR disabled).
        pass

    try:
        ocr_gpu = str(getattr(args, "ocr_gpu", "auto") or "auto").strip().lower()
    except Exception:
        ocr_gpu = "auto"
    if ocr_gpu in {"0", "1"}:
        ocr_env["OCR_GPU"] = ocr_gpu

    if bool(getattr(args, "ocr_init_log", False)):
        ocr_env["OCR_INIT_LOG"] = "1"

    print(f"▶ Phase 1/2: dry-run (no OS input) | seconds={args.seconds_dry} | out={phase1}")
    use_roboflow = str(getattr(args, "roboflow", "off") or "off").strip().lower() in {"on", "1", "true", "yes"}
    rc1 = _run_smoke_subprocess(
        seconds=float(args.seconds_dry),
        monitor=int(args.monitor),
        capture_fps=float(args.capture_fps),
        out_base=phase1,
        disable_roboflow=(not bool(use_roboflow)),
        confirm_actions=False,
        extra_env={
            "ASSIST_DRY_RUN": "1",
            "ASSIST_AUTO_COMMIT_WHEN_ARMED": "0",
            "CAVEBOT_ROUTE_PATH": str(args.route),
            "CAVEBOT_MODE": str(args.cavebot_mode),
            **ocr_env,
        },
    )
    if rc1 != 0:
        print(f"❌ Dry-run failed (exit={rc1})")
        return int(rc1)

    _write_summary(decision_trace_path=phase1 / "decision_trace.jsonl", out_txt=phase1 / "decision_trace_summary.txt")
    print(f"✅ Phase 1 done. Summary: {phase1 / 'decision_trace_summary.txt'}")

    # ---------- Phase 2: live injection (optional, dangerous) ----------
    if not bool(args.live):
        print("ℹ️ Live phase skipped. Re-run with --live --i-accept-live-input-risk to test OS injection.")
        return 0

    if not bool(args.i_accept_live_input_risk):
        print("⛔ Refusing live phase without --i-accept-live-input-risk")
        return 2

    phase2 = out_base / "live"
    phase2.mkdir(parents=True, exist_ok=True)

    print("\n⚠️ Phase 2/2: LIVE INPUT WILL BE SENT")
    print("- Bring the Tibia client window to the foreground now.")
    print("- Press Ctrl+C to abort.")
    time.sleep(3.0)


    rc2 = _run_smoke_subprocess(
        seconds=float(args.seconds_live),
        monitor=int(args.monitor),
        capture_fps=float(args.capture_fps),
        out_base=phase2,
        disable_roboflow=(not bool(use_roboflow)),
        confirm_actions=False,
        extra_env={
            "ASSIST_DRY_RUN": "0",
            "ASSIST_AUTO_COMMIT_WHEN_ARMED": "1",
            "ALLOW_BACKGROUND_INPUT": os.getenv("ALLOW_BACKGROUND_INPUT", "0") or "0",
            "ALLOWED_WINDOW_TITLES": os.getenv("ALLOWED_WINDOW_TITLES", "Tibia") or "Tibia",
            "CAVEBOT_ROUTE_PATH": str(args.route),
            "CAVEBOT_MODE": str(args.cavebot_mode),
            **ocr_env,
        },
    )
    if rc2 != 0:
        print(f"❌ Live phase failed (exit={rc2})")
        return int(rc2)

    _write_summary(decision_trace_path=phase2 / "decision_trace.jsonl", out_txt=phase2 / "decision_trace_summary.txt")
    print(f"✅ Phase 2 done. Summary: {phase2 / 'decision_trace_summary.txt'}")

    print(f"\nDone. Outputs: {out_base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
