from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable


_COORD_RE = re.compile(
    r"(?i)(?:x\s*[:=\s,]+(?P<x>-?\d+))\D+"  # X: 123
    r"(?:y\s*[:=\s,]+(?P<y>-?\d+))"  # Y: 456
    r"(?:\D+(?:z\s*[:=\s,]+(?P<z>-?\d+)))?"  # optional Z: 7
)


def _iter_lines_from_input(inp: str | None) -> Iterable[str]:
    if inp is None or inp.strip() == "-":
        for line in sys.stdin:
            yield line
        return

    p = Path(inp)
    yield from p.read_text(encoding="utf-8", errors="replace").splitlines()


def _parse_coords_line(line: str) -> tuple[int, int, int | None] | None:
    s = line.strip()
    if not s:
        return None

    # Accept common formats:
    # - X: 32561 Y: 32496 Z: 7
    # - 32561,32496,7
    # - 32561 32496 7
    # - 32561 32496

    m = _COORD_RE.search(s)
    if m:
        x = int(m.group("x"))
        y = int(m.group("y"))
        z_raw = m.group("z")
        z = int(z_raw) if z_raw is not None else None
        return x, y, z

    # Try raw numbers.
    nums = re.findall(r"-?\d+", s)
    if len(nums) >= 2:
        x = int(nums[0])
        y = int(nums[1])
        z = int(nums[2]) if len(nums) >= 3 else None
        return x, y, z

    return None


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Convert pasted Tibia coordinate lines into a cavebot route JSON. "
            "Reads from --input file or stdin."
        )
    )
    p.add_argument("--input", default="-", help="Input file path, or '-' for stdin (default).")
    p.add_argument("--output", required=True, help="Output route JSON path.")
    p.add_argument("--name", default="wp", help="Waypoint name prefix (default: wp).")
    p.add_argument(
        "--action",
        default="",
        help="Optional action string to attach to every waypoint (default: none).",
    )
    p.add_argument(
        "--drop-duplicates",
        action="store_true",
        help="Drop consecutive duplicate (x,y,z) points.",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    waypoints: list[dict] = []
    last_key: tuple[int, int, int | None] | None = None

    for raw in _iter_lines_from_input(args.input):
        parsed = _parse_coords_line(raw)
        if parsed is None:
            continue
        x, y, z = parsed
        key = (x, y, z)
        if args.drop_duplicates and last_key == key:
            continue
        last_key = key

        wp: dict = {"x": x, "y": y, "name": f"{args.name}{len(waypoints):03d}"}
        if z is not None:
            wp["z"] = z
        if args.action:
            wp["action"] = str(args.action)
        waypoints.append(wp)

    if not waypoints:
        print("No coordinates parsed. Paste lines like 'X: 32561 Y: 32496 Z: 7' or '32561 32496 7'.", file=sys.stderr)
        return 2

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(waypoints, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {len(waypoints)} waypoints -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
