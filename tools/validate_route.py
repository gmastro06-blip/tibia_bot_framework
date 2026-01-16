from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# Ensure `src/` is on sys.path so imports like `from navigation...` work.
repo_root = Path(__file__).resolve().parents[1]
src_dir = repo_root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from navigation.route import Waypoint, load_route


@dataclass(frozen=True)
class RouteStats:
    n: int
    n_with_z: int
    distinct_floors: int
    consecutive_duplicates: int
    big_jumps: int
    floor_changes: int


def _manhattan(a: Waypoint, b: Waypoint) -> int:
    return abs(int(a.x) - int(b.x)) + abs(int(a.y) - int(b.y))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate/summarize a cavebot route JSON (navigation.route format).")
    p.add_argument("route", type=str, help="Path to route.json")
    p.add_argument("--jump-threshold", type=int, default=20, help="Flag jumps >= N tiles (default: 20)")
    p.add_argument("--drop-duplicates", action="store_true", help="Drop consecutive duplicate points")
    p.add_argument("--out", default="", help="Optional output path for a cleaned route JSON")
    p.add_argument("--print", action="store_true", help="Print flagged segments")
    return p.parse_args()


def _to_jsonable(route: list[Waypoint]) -> list[dict]:
    out: list[dict] = []
    for wp in route:
        d: dict = {"x": int(wp.x), "y": int(wp.y)}
        if wp.z is not None:
            d["z"] = int(wp.z)
        if wp.name is not None:
            d["name"] = str(wp.name)
        if wp.action is not None:
            d["action"] = str(wp.action)
        out.append(d)
    return out


def validate_route(route: list[Waypoint], *, jump_threshold: int) -> tuple[RouteStats, list[tuple[int, str]]]:
    flags: list[tuple[int, str]] = []
    n = len(route)
    n_with_z = sum(1 for w in route if w.z is not None)
    floors = sorted({int(w.z) for w in route if w.z is not None})

    dup = 0
    big = 0
    floor_changes = 0

    thr = max(1, int(jump_threshold))
    for i in range(1, n):
        a = route[i - 1]
        b = route[i]
        if int(a.x) == int(b.x) and int(a.y) == int(b.y) and (a.z if a.z is not None else None) == (b.z if b.z is not None else None):
            dup += 1
            flags.append((i, "duplicate"))

        d = _manhattan(a, b)
        if d >= thr:
            big += 1
            flags.append((i, f"big_jump:{d}"))

        if a.z is not None and b.z is not None and int(a.z) != int(b.z):
            floor_changes += 1
            flags.append((i, f"floor_change:{a.z}->{b.z}"))

    stats = RouteStats(
        n=n,
        n_with_z=n_with_z,
        distinct_floors=len(floors),
        consecutive_duplicates=dup,
        big_jumps=big,
        floor_changes=floor_changes,
    )
    return stats, flags


def _drop_consecutive_duplicates(route: list[Waypoint]) -> list[Waypoint]:
    if not route:
        return []
    out = [route[0]]
    for wp in route[1:]:
        last = out[-1]
        if int(wp.x) == int(last.x) and int(wp.y) == int(last.y) and (wp.z if wp.z is not None else None) == (last.z if last.z is not None else None):
            continue
        out.append(wp)
    return out


def main() -> int:
    args = _parse_args()
    p = Path(args.route)
    if not p.exists():
        raise SystemExit(f"Route not found: {p}")

    route = load_route(str(p))
    if not route:
        raise SystemExit("Route has no valid waypoints.")

    stats, flags = validate_route(route, jump_threshold=int(args.jump_threshold))

    print(f"n_waypoints={stats.n} with_z={stats.n_with_z} distinct_floors={stats.distinct_floors}")
    print(f"consecutive_duplicates={stats.consecutive_duplicates} floor_changes={stats.floor_changes} big_jumps={stats.big_jumps}")

    if args.print and flags:
        for idx, reason in flags[:200]:
            a = route[idx - 1]
            b = route[idx]
            print(
                f"#{idx:04d} {reason}  ({a.x},{a.y},{a.z}) -> ({b.x},{b.y},{b.z})"
                + (f" name={b.name}" if b.name else "")
                + (f" action={b.action}" if b.action else "")
            )
        if len(flags) > 200:
            print(f"... ({len(flags) - 200} more)")

    out_route = route
    if bool(args.drop_duplicates):
        out_route = _drop_consecutive_duplicates(out_route)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(_to_jsonable(out_route), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote cleaned route -> {out_path} ({len(out_route)} waypoints)")

    # Exit code: non-zero if suspicious.
    suspicious = stats.big_jumps > 0
    return 2 if suspicious else 0


if __name__ == "__main__":
    raise SystemExit(main())
