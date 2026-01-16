from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except Exception:
            continue
    return rows


def _last_telemetry(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    tele = [r for r in rows if r.get("kind") == "telemetry"]
    return tele[-1] if tele else None


def _fmt(v: object) -> str:
    if v is None:
        return "-"
    try:
        if isinstance(v, float):
            # Keep it readable
            return f"{v:.3f}".rstrip("0").rstrip(".")
    except Exception:
        pass
    return str(v)


def _tele_key(t: dict[str, Any]) -> tuple[object, object, object, object]:
    return (
        t.get("cap_current"),
        t.get("ring_equipped"),
        t.get("amulet_equipped"),
        t.get("hungry"),
    )


def _print_timeline(rows: list[dict[str, Any]], *, show_all: bool, limit: int | None) -> None:
    tele = [r for r in rows if r.get("kind") == "telemetry"]
    if not tele:
        print("No telemetry rows found.")
        return

    if limit is not None and limit > 0:
        tele = tele[-int(limit) :]

    last_k = None
    for idx, t in enumerate(tele):
        k = _tele_key(t)
        if (not show_all) and (last_k is not None) and (k == last_k):
            continue
        last_k = k

        cap_cur = _fmt(t.get("cap_current"))
        ring = _fmt(t.get("ring_equipped"))
        amu = _fmt(t.get("amulet_equipped"))
        hungry = _fmt(t.get("hungry"))
        hp = _fmt(t.get("hp_current"))
        hp_m = _fmt(t.get("hp_max"))
        mp = _fmt(t.get("mp_current"))
        mp_m = _fmt(t.get("mp_max"))

        cap_method = _fmt(t.get("cap_method"))
        cap_reason = _fmt(t.get("cap_reason"))
        cap_roi = _fmt(t.get("cap_roi"))
        cap_panel = _fmt(t.get("cap_panel"))
        cap_panel_src = _fmt(t.get("cap_panel_source"))

        print(
            f"[{idx:03d}] cap={cap_cur} (roi={cap_roi} panel={cap_panel} src={cap_panel_src} method={cap_method} reason={cap_reason}) "
            f"ring={ring} amulet={amu} hungry={hungry} hp={hp}/{hp_m} mp={mp}/{mp_m}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Run a short live bot session and print key HUD flags from JSONL.")
    ap.add_argument("--seconds", type=float, default=12.0)
    ap.add_argument("--capture-fps", type=float, default=10.0)
    ap.add_argument("--interval-ms", type=int, default=2000)
    ap.add_argument("--out-dir", default="logs/live_validate")
    ap.add_argument("--replay", action="store_true", help="Enable replay crops for later inspection")
    ap.add_argument(
        "--show-all",
        action="store_true",
        help="Print every telemetry row (default prints only when CAP/ring/amulet/hungry changes)",
    )
    ap.add_argument(
        "--tail",
        type=int,
        default=0,
        help="Only print the last N telemetry rows (0 = no limit)",
    )
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = time.time()
    stamp = f"{ts:.6f}".replace(".", "_")
    jsonl_path = out_dir / f"telemetry_{stamp}.jsonl"

    env = os.environ.copy()
    env["BOT_EXIT_AFTER_S"] = str(float(args.seconds))
    env["CAPTURE_FPS"] = str(float(args.capture_fps))

    env["LOG_JSONL_ENABLED"] = "1"
    env["LOG_JSONL_INTERVAL_MS"] = str(int(args.interval_ms))
    env["LOG_JSONL_OUT_FILE"] = str(jsonl_path.as_posix())

    if args.replay:
        replay_dir = out_dir / f"replay_{stamp}"
        env["REPLAY_ENABLED"] = "1"
        env["REPLAY_INTERVAL_MS"] = str(int(args.interval_ms))
        env["REPLAY_OUT_DIR"] = str(replay_dir.as_posix())
    else:
        env["REPLAY_ENABLED"] = ""

    cmd = [sys.executable, "-m", "src.main"]
    print(f"Running: {' '.join(cmd)}")
    print(f"JSONL: {jsonl_path}")
    if args.replay:
        print(f"Replay: {env.get('REPLAY_OUT_DIR')}")

    proc = subprocess.run(cmd, env=env)
    if proc.returncode != 0:
        print(f"Bot exited with code {proc.returncode}")

    rows = _read_jsonl(jsonl_path)
    last = _last_telemetry(rows)
    print("---")
    if not last:
        print("No telemetry rows found.")
        return 2

    # Timeline (helpful for in-run equip/unequip validation)
    _print_timeline(rows, show_all=bool(args.show_all), limit=(int(args.tail) if int(args.tail or 0) > 0 else None))

    print("---")

    print("cap_current:", last.get("cap_current"))
    if "cap_method" in last or "cap_reason" in last:
        print("cap_method:", last.get("cap_method"))
        print("cap_reason:", last.get("cap_reason"))
    if any(k in last for k in ("cap_roi", "cap_panel", "cap_panel_source")):
        print("cap_roi:", last.get("cap_roi"))
        print("cap_panel:", last.get("cap_panel"))
        print("cap_panel_source:", last.get("cap_panel_source"))
    print("ring_equipped:", last.get("ring_equipped"))
    print("amulet_equipped:", last.get("amulet_equipped"))
    print("hungry:", last.get("hungry"))
    print("hp:", last.get("hp_current"), "/", last.get("hp_max"), "mp:", last.get("mp_current"), "/", last.get("mp_max"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
