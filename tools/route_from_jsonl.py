from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

# Ensure `src/` is on sys.path so imports like `from navigation...` work.
repo_root = Path(__file__).resolve().parents[1]
src_dir = repo_root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from navigation.coords_recorder import CoordsRecorder


def _iter_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                yield obj


def _as_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int,)):
        return int(v)
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return int(float(s))
        except Exception:
            return None
    return None


def _extract_pos(ev: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    # Prefer top-level telemetry payload.
    x = _as_int(ev.get("pos_x"))
    y = _as_int(ev.get("pos_y"))
    z = _as_int(ev.get("pos_z"))

    # Support raw sample JSONL (CoordsRecorder.save_samples_jsonl) and other tools.
    if x is None:
        x = _as_int(ev.get("x"))
    if y is None:
        y = _as_int(ev.get("y"))
    if z is None:
        z = _as_int(ev.get("z"))

    # Some event kinds may nest telemetry in `telemetry`.
    if (x is None or y is None) and isinstance(ev.get("telemetry"), dict):
        tel = ev["telemetry"]
        x = _as_int(tel.get("pos_x"))
        y = _as_int(tel.get("pos_y"))
        z = _as_int(tel.get("pos_z"))

        if x is None:
            x = _as_int(tel.get("x"))
        if y is None:
            y = _as_int(tel.get("y"))
        if z is None:
            z = _as_int(tel.get("z"))

    return x, y, z


def _extract_ts(ev: Dict[str, Any]) -> Optional[float]:
    v = ev.get("ts")
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Build a cavebot route JSON from a telemetry JSONL file (logs/telemetry.jsonl). "
            "It reads pos_x/pos_y/pos_z from kind='telemetry' lines (or nested telemetry payloads)."
        )
    )
    p.add_argument(
        "--file",
        default=os.getenv("TELEMETRY_JSONL", "logs/telemetry.jsonl"),
        help="Input JSONL path (default: logs/telemetry.jsonl or TELEMETRY_JSONL env var)",
    )
    p.add_argument("--out", required=True, help="Output route JSON path")
    p.add_argument("--out-samples", default="", help="Optional JSONL path to save raw samples")

    p.add_argument(
        "--kind",
        default="telemetry",
        help="Only read entries with this kind (default: telemetry). Use 'any' to accept all.",
    )

    # Time filtering
    p.add_argument(
        "--since-s",
        type=float,
        default=0.0,
        help="Only include samples with ts >= (now - since_s). 0 means no filter.",
    )
    p.add_argument("--start-ts", type=float, default=0.0, help="Only include samples with ts >= start_ts")
    p.add_argument("--end-ts", type=float, default=0.0, help="Only include samples with ts <= end_ts")

    # Filters / output
    p.add_argument("--min-manhattan", type=int, default=int(os.getenv("RECORD_ROUTE_MIN_MANHATTAN", "1") or 1))
    p.add_argument("--min-interval-s", type=float, default=float(os.getenv("RECORD_ROUTE_MIN_INTERVAL_S", "0") or 0))
    p.add_argument("--max-points", type=int, default=10_000)
    p.add_argument("--name-prefix", default="wp")
    p.add_argument("--action", default="", help="Optional waypoint action string to attach")
    p.add_argument("--require-z", action="store_true", help="Skip samples without z")
    p.add_argument("--z", type=int, default=10_000_000, help="If set, keep only samples where pos_z == z")
    p.add_argument("--print", action="store_true", help="Print when a point is recorded")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    in_path = str(args.file)
    if not os.path.exists(in_path):
        print(f"File not found: {in_path}", file=sys.stderr)
        return 2

    kind_wanted = str(args.kind).strip()

    start_ts = None
    end_ts = None

    if float(args.since_s) > 0.0:
        start_ts = time.time() - max(0.0, float(args.since_s))

    if float(args.start_ts) > 0.0:
        start_ts = float(args.start_ts) if start_ts is None else max(start_ts, float(args.start_ts))

    if float(args.end_ts) > 0.0:
        end_ts = float(args.end_ts)

    z_filter = None
    try:
        if int(args.z) != 10_000_000:
            z_filter = int(args.z)
    except Exception:
        z_filter = None

    rec = CoordsRecorder(
        min_manhattan=int(args.min_manhattan),
        min_interval_s=float(args.min_interval_s),
        max_points=int(args.max_points),
    )

    n_lines = 0
    n_candidates = 0
    n_with_pos = 0
    n_has_pos_keys = 0
    n_has_xy_keys = 0

    for ev in _iter_jsonl(in_path):
        n_lines += 1

        if ("pos_x" in ev) or ("pos_y" in ev) or ("pos_z" in ev):
            n_has_pos_keys += 1
        if ("x" in ev) or ("y" in ev) or ("z" in ev):
            n_has_xy_keys += 1

        kind = ev.get("kind")
        if kind_wanted.lower() != "any" and kind != kind_wanted:
            continue

        ts = _extract_ts(ev)
        if ts is not None:
            if start_ts is not None and ts < start_ts:
                continue
            if end_ts is not None and ts > end_ts:
                continue

        n_candidates += 1

        x, y, z = _extract_pos(ev)
        if x is None or y is None:
            continue
        if args.require_z and z is None:
            continue
        if z_filter is not None and (z is None or int(z) != z_filter):
            continue

        n_with_pos += 1
        added = rec.maybe_add(x=int(x), y=int(y), z=(int(z) if z is not None else None), ts=ts)
        if added and args.print:
            if z is None:
                print(f"+ ({x},{y})")
            else:
                print(f"+ ({x},{y},{z})")

    if not rec.samples:
        print(f"No route points recorded.", file=sys.stderr)
        print(f"Stats: lines={n_lines} matched_kind={n_candidates} with_valid_pos={n_with_pos}", file=sys.stderr)
        print(
            f"Detected keys: entries_with_pos_x/pos_y/pos_z={n_has_pos_keys}, entries_with_x/y/z={n_has_xy_keys}",
            file=sys.stderr,
        )
        print(
            "Hint: your JSONL must contain either pos_x/pos_y(/pos_z) (telemetry logs) or x/y(/z) (raw samples).",
            file=sys.stderr,
        )
        return 1

    action = (str(args.action).strip() or None)
    rec.save_route(str(args.out), name_prefix=str(args.name_prefix), action=action)
    if args.out_samples:
        try:
            rec.save_samples_jsonl(str(args.out_samples))
        except Exception:
            pass

    print(f"Saved {len(rec.samples)} points -> {args.out}")
    if args.out_samples:
        print(f"Saved samples -> {args.out_samples}")
    print(f"Stats: lines={n_lines} matched_kind={n_candidates} with_pos={n_with_pos}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
