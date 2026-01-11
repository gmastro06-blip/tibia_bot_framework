from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Ensure `src/` is on sys.path so imports like `from navigation...` work.
repo_root = Path(__file__).resolve().parents[1]
src_dir = repo_root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from navigation.coords_recorder import CoordsRecorder


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Record coordinates by watching env vars PLAYER_X/PLAYER_Y/PLAYER_Z and exporting a cavebot route JSON. "
            "Useful for quick route recording if you can update env vars from an external tool." 
        )
    )
    p.add_argument("--seconds", type=float, default=60.0, help="How long to record")
    p.add_argument("--out", required=True, help="Output route JSON path")
    p.add_argument("--out-samples", default="", help="Optional JSONL path to save raw samples")
    p.add_argument("--poll-s", type=float, default=0.2, help="Env polling interval")

    p.add_argument("--min-manhattan", type=int, default=int(os.getenv("RECORD_ROUTE_MIN_MANHATTAN", "1") or 1))
    p.add_argument("--min-interval-s", type=float, default=float(os.getenv("RECORD_ROUTE_MIN_INTERVAL_S", "0") or 0))
    p.add_argument("--max-points", type=int, default=10_000, help="Hard cap on points (drops oldest)")
    p.add_argument("--name-prefix", default="wp")
    p.add_argument("--action", default="", help="Optional waypoint action string to attach")
    p.add_argument("--print", action="store_true", help="Print when a point is recorded")
    p.add_argument(
        "--fail-if-empty",
        action="store_true",
        help="Exit non-zero if no points were recorded (default: success with a warning)",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    rec = CoordsRecorder(
        min_manhattan=args.min_manhattan,
        min_interval_s=args.min_interval_s,
        max_points=args.max_points,
    )

    end = time.time() + max(1.0, float(args.seconds))
    last_warn = 0.0
    try:
        while time.time() < end:
            px = os.getenv("PLAYER_X", "").strip()
            py = os.getenv("PLAYER_Y", "").strip()
            pz = os.getenv("PLAYER_Z", "").strip()

            if px and py:
                try:
                    x = int(px)
                    y = int(py)
                    z = int(pz) if pz else None
                    added = rec.maybe_add(x=x, y=y, z=z)
                    if added and args.print:
                        if z is None:
                            print(f"+ ({x},{y})")
                        else:
                            print(f"+ ({x},{y},{z})")
                except Exception:
                    pass
            else:
                now = time.time()
                if now - last_warn >= 2.0:
                    last_warn = now
                    print("Waiting for PLAYER_X and PLAYER_Y env vars...")

            time.sleep(max(0.05, float(args.poll_s)))
    except KeyboardInterrupt:
        pass

    if not rec.samples:
        print("No points recorded (PLAYER_X/PLAYER_Y never set or filtered by thresholds).")
        return 2 if bool(args.fail_if_empty) else 0

    action = (str(args.action).strip() or None)
    rec.save_route(args.out, name_prefix=str(args.name_prefix), action=action)
    if args.out_samples:
        try:
            rec.save_samples_jsonl(args.out_samples)
        except Exception:
            pass

    print(f"Saved {len(rec.samples)} points -> {args.out}")
    if args.out_samples:
        print(f"Saved samples -> {args.out_samples}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
