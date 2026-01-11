from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ViewportSample:
    ts: float
    x: float
    y: float
    w: float
    h: float
    method: str
    auto_ts: float | None
    confidence: float | None = None
    frozen: bool | None = None
    left_clip_px: float | None = None
    right_clip_px: float | None = None
    left_panels_est: int | None = None
    right_panels_est: int | None = None
    unit_left_px: float | None = None
    unit_right_px: float | None = None
    state: int | None = None


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Summarize viewport auto-adjust behavior from replay JSONs. "
            "Writes a CSV time-series and prints change-point events."
        )
    )
    p.add_argument(
        "--replay_dir",
        dest="replay_dir_opt",
        type=str,
        default="",
        help="Alias for positional replay_dir.",
    )
    p.add_argument(
        "replay_dir",
        type=str,
        nargs="?",
        default="logs/replay",
        help="Replay output directory containing *.json (default: logs/replay)",
    )
    p.add_argument(
        "--out",
        type=str,
        default="",
        help="Output CSV path (default: <replay_dir>/viewport_series.csv)",
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=20.0,
        help="Change-point threshold in px for |dx| or |dw| (default: 20)",
    )
    p.add_argument(
        "--plot",
        action="store_true",
        help="Also write a PNG plot if matplotlib is available.",
    )
    return p.parse_args()


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _parse_ts_from_filename(p: Path) -> float | None:
    try:
        return float(p.stem)
    except Exception:
        return None


def _iter_json_files(replay_dir: Path) -> list[Path]:
    files = [p for p in replay_dir.glob("*.json") if p.is_file()]

    def key(p: Path) -> float:
        ts = _parse_ts_from_filename(p)
        return ts if ts is not None else math.inf

    return sorted(files, key=key)


def _load_samples(files: Iterable[Path]) -> list[ViewportSample]:
    out: list[ViewportSample] = []
    for p in files:
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue

        ts = _safe_float(payload.get("ts"))
        if ts is None:
            ts = _parse_ts_from_filename(p) or 0.0

        rois_state = payload.get("rois_state") or {}
        vp = rois_state.get("game_viewport")
        auto = rois_state.get("viewport_auto")

        if not isinstance(vp, dict):
            continue

        x = _safe_float(vp.get("x"))
        y = _safe_float(vp.get("y"))
        w = _safe_float(vp.get("w"))
        h = _safe_float(vp.get("h"))
        if x is None or y is None or w is None or h is None:
            continue

        method = ""
        auto_ts = None
        confidence = None
        frozen = None
        left_clip_px = None
        right_clip_px = None
        left_panels_est = None
        right_panels_est = None
        unit_left_px = None
        unit_right_px = None
        if isinstance(auto, dict):
            method = str(auto.get("method") or "")
            auto_ts = _safe_float(auto.get("ts"))

            confidence = _safe_float(auto.get("confidence"))
            try:
                fr = auto.get("frozen")
                frozen = None if fr is None else bool(fr)
            except Exception:
                frozen = None

            panels = auto.get("panels")
            if isinstance(panels, dict):
                left_clip_px = _safe_float(panels.get("left_clip_px"))
                right_clip_px = _safe_float(panels.get("right_clip_px"))
                try:
                    lv = panels.get("left_panels_est")
                    left_panels_est = None if lv is None else int(lv)
                except Exception:
                    left_panels_est = None
                try:
                    rv = panels.get("right_panels_est")
                    right_panels_est = None if rv is None else int(rv)
                except Exception:
                    right_panels_est = None
                unit_left_px = _safe_float(panels.get("unit_left_px"))
                unit_right_px = _safe_float(panels.get("unit_right_px"))

        out.append(
            ViewportSample(
                ts=ts,
                x=x,
                y=y,
                w=w,
                h=h,
                method=method,
                auto_ts=auto_ts,
                confidence=confidence,
                frozen=frozen,
                left_clip_px=left_clip_px,
                right_clip_px=right_clip_px,
                left_panels_est=left_panels_est,
                right_panels_est=right_panels_est,
                unit_left_px=unit_left_px,
                unit_right_px=unit_right_px,
                state=None,
            )
        )

    out.sort(key=lambda s: s.ts)
    return out


def _assign_states(samples: list[ViewportSample], *, bin_px: float = 6.0) -> list[ViewportSample]:
    """Assign a discrete state id based on (x,w) clustering.

    This helps interpret panel combinations (left/right HUD panels) as a small set
    of stable viewport configurations.
    """
    if not samples:
        return samples

    b = max(1.0, float(bin_px))

    def k(s: ViewportSample) -> tuple[int, int]:
        return (int(round(s.x / b)), int(round(s.w / b)))

    counts: dict[tuple[int, int], int] = {}
    for s in samples:
        kk = k(s)
        counts[kk] = counts.get(kk, 0) + 1

    # Most common state gets id=0, next id=1, ...
    order = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    state_id: dict[tuple[int, int], int] = {kk: i for i, (kk, _) in enumerate(order)}

    return [
        ViewportSample(
            ts=s.ts,
            x=s.x,
            y=s.y,
            w=s.w,
            h=s.h,
            method=s.method,
            auto_ts=s.auto_ts,
            confidence=s.confidence,
            frozen=s.frozen,
            left_clip_px=s.left_clip_px,
            right_clip_px=s.right_clip_px,
            left_panels_est=s.left_panels_est,
            right_panels_est=s.right_panels_est,
            unit_left_px=s.unit_left_px,
            unit_right_px=s.unit_right_px,
            state=state_id.get(k(s)),
        )
        for s in samples
    ]


def _summarize(samples: list[ViewportSample]) -> None:
    if not samples:
        print("No viewport samples found.")
        return

    first = samples[0]
    last = samples[-1]

    def key(s: ViewportSample) -> tuple[float, float, float, float]:
        return (round(s.x, 1), round(s.y, 1), round(s.w, 1), round(s.h, 1))

    counts: dict[tuple[float, float, float, float], int] = {}
    for s in samples:
        k = key(s)
        counts[k] = counts.get(k, 0) + 1

    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]

    print(f"n_samples={len(samples)}")
    print(f"first ts={first.ts:.3f} vp=({first.x:.1f},{first.y:.1f},{first.w:.1f},{first.h:.1f}) method={first.method}")
    print(f"last  ts={last.ts:.3f} vp=({last.x:.1f},{last.y:.1f},{last.w:.1f},{last.h:.1f}) method={last.method}")
    print(f"distinct_viewports={len(counts)}")
    print("most_common_viewports:")
    for (x, y, w, h), n in top:
        print(f"  n={n:4d} vp=({x:.1f},{y:.1f},{w:.1f},{h:.1f})")


def _detect_changes(samples: list[ViewportSample], *, threshold_px: float) -> list[tuple[int, float, float, float]]:
    """Return list of (index, ts, dx, dw) where change exceeds threshold."""
    out: list[tuple[int, float, float, float]] = []
    if len(samples) < 2:
        return out

    thr = float(threshold_px)
    for i in range(1, len(samples)):
        prev = samples[i - 1]
        cur = samples[i]
        dx = float(cur.x - prev.x)
        dw = float(cur.w - prev.w)
        if abs(dx) >= thr or abs(dw) >= thr:
            out.append((i, cur.ts, dx, dw))
    return out


def _write_csv(samples: list[ViewportSample], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "ts",
                "x",
                "y",
                "w",
                "h",
                "method",
                "auto_ts",
                "confidence",
                "frozen",
                "left_clip_px",
                "right_clip_px",
                "left_panels_est",
                "right_panels_est",
                "unit_left_px",
                "unit_right_px",
                "state",
            ]
        )
        for s in samples:
            w.writerow([
                f"{s.ts:.6f}",
                f"{s.x:.3f}",
                f"{s.y:.3f}",
                f"{s.w:.3f}",
                f"{s.h:.3f}",
                s.method,
                "" if s.auto_ts is None else f"{s.auto_ts:.6f}",
                "" if s.confidence is None else f"{s.confidence:.6f}",
                "" if s.frozen is None else ("1" if bool(s.frozen) else "0"),
                "" if s.left_clip_px is None else f"{s.left_clip_px:.3f}",
                "" if s.right_clip_px is None else f"{s.right_clip_px:.3f}",
                "" if s.left_panels_est is None else str(int(s.left_panels_est)),
                "" if s.right_panels_est is None else str(int(s.right_panels_est)),
                "" if s.unit_left_px is None else f"{s.unit_left_px:.3f}",
                "" if s.unit_right_px is None else f"{s.unit_right_px:.3f}",
                "" if s.state is None else str(int(s.state)),
            ])


def _maybe_plot(samples: list[ViewportSample], out_png: Path) -> bool:
    try:
        import matplotlib.pyplot as plt  # type: ignore[import-not-found]
    except Exception:
        return False

    ts0 = samples[0].ts
    t = [s.ts - ts0 for s in samples]
    xs = [s.x for s in samples]
    ws = [s.w for s in samples]

    plt.figure(figsize=(10, 5))
    plt.plot(t, xs, label="x")
    plt.plot(t, ws, label="w")
    plt.xlabel("t (s)")
    plt.ylabel("px")
    plt.title("game_viewport over time")
    plt.grid(True, alpha=0.3)
    plt.legend(loc="best")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(out_png, dpi=160)
    plt.close()
    return True


def main() -> int:
    args = _parse_args()
    replay_dir_raw = (getattr(args, "replay_dir_opt", "") or "").strip() or str(args.replay_dir)
    replay_dir = Path(replay_dir_raw)
    if not replay_dir.exists():
        raise SystemExit(f"Replay dir does not exist: {replay_dir}")

    files = _iter_json_files(replay_dir)
    if not files:
        raise SystemExit(f"No JSON files found in: {replay_dir}")

    samples = _assign_states(_load_samples(files))
    _summarize(samples)

    changes = _detect_changes(samples, threshold_px=float(args.threshold))
    if changes:
        print(f"\nchange_points (threshold={float(args.threshold):.1f}px):")
        for idx, ts, dx, dw in changes:
            s = samples[idx]
            print(
                f"  ts={ts:.3f}  dx={dx:+.1f}  dw={dw:+.1f}  -> vp=({s.x:.1f},{s.y:.1f},{s.w:.1f},{s.h:.1f}) state={s.state}"
            )
    else:
        print(f"\nchange_points: none (threshold={float(args.threshold):.1f}px)")

    out_csv = Path(args.out) if args.out else (replay_dir / "viewport_series.csv")
    _write_csv(samples, out_csv)
    print(f"\nOK: wrote {out_csv}")

    if bool(args.plot):
        out_png = out_csv.with_suffix(".png")
        if _maybe_plot(samples, out_png):
            print(f"OK: wrote {out_png}")
        else:
            print("Note: matplotlib not available; skipped plot.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
