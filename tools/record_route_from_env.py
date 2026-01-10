from __future__ import annotations

import argparse
import os
import time

from navigation.coords_recorder import CoordsRecorder


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Record (PLAYER_X,PLAYER_Y,PLAYER_Z) changes into a route.json")
    p.add_argument("--seconds", type=float, default=60.0)
    p.add_argument("--out", required=True)
    p.add_argument("--min-manhattan", type=int, default=int(os.getenv("RECORD_ROUTE_MIN_MANHATTAN", "1") or 1))
    p.add_argument("--min-interval-s", type=float, default=float(os.getenv("RECORD_ROUTE_MIN_INTERVAL_S", "0") or 0))
    p.add_argument("--poll-s", type=float, default=0.2)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    rec = CoordsRecorder(min_manhattan=args.min_manhattan, min_interval_s=args.min_interval_s)

    end = time.time() + max(1.0, float(args.seconds))
    while time.time() < end:
        px = os.getenv("PLAYER_X", "").strip()
        py = os.getenv("PLAYER_Y", "").strip()
        pz = os.getenv("PLAYER_Z", "").strip()

        if px and py:
            try:
                x = int(px)
                y = int(py)
                z = int(pz) if pz else None
                rec.maybe_add(x=x, y=y, z=z)
            except Exception:
                pass

        time.sleep(max(0.05, float(args.poll_s)))

    rec.save_route(args.out, name_prefix="wp")
    print(f"Saved {len(rec.samples)} points -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
