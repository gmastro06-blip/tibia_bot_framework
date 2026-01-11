from __future__ import annotations

import argparse
import os
import subprocess
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


def _get_clipboard_text() -> str:
    """Read Windows clipboard text via PowerShell (no extra deps)."""

    try:
        # Out-String to force a plain string even if clipboard has multiple lines.
        res = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-Clipboard | Out-String",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if res.returncode != 0:
            return ""
        return (res.stdout or "").strip()
    except Exception:
        return ""


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Record coordinates by watching the clipboard for lines like 'X: 32561 Y: 32496 Z: 7'. "
            "Useful when building routes from TibiaMaps without in-game coordinate OCR."
        )
    )
    p.add_argument("--seconds", type=float, default=300.0)
    p.add_argument("--out", required=True, help="Output route JSON path")
    p.add_argument("--poll-s", type=float, default=0.25, help="Clipboard polling interval")

    p.add_argument("--min-manhattan", type=int, default=int(os.getenv("RECORD_ROUTE_MIN_MANHATTAN", "1") or 1))
    p.add_argument("--min-interval-s", type=float, default=float(os.getenv("RECORD_ROUTE_MIN_INTERVAL_S", "0") or 0))
    p.add_argument("--name-prefix", default="wp")
    p.add_argument("--print", action="store_true", help="Print when a point is recorded")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    rec = CoordsRecorder(min_manhattan=args.min_manhattan, min_interval_s=args.min_interval_s)

    last = ""
    end = time.time() + max(1.0, float(args.seconds))

    # Reuse the parser from coords_to_route.
    from tools.coords_to_route import _parse_coords_line

    while time.time() < end:
        cur = _get_clipboard_text()
        if cur and cur != last:
            last = cur
            parsed = _parse_coords_line(cur)
            if parsed is not None:
                x, y, z = parsed
                added = rec.maybe_add(x=int(x), y=int(y), z=(int(z) if z is not None else None))
                if added and args.print:
                    if z is None:
                        print(f"+ ({x},{y})")
                    else:
                        print(f"+ ({x},{y},{z})")

        time.sleep(max(0.05, float(args.poll_s)))

    rec.save_route(args.out, name_prefix=str(args.name_prefix))
    print(f"Saved {len(rec.samples)} points -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
