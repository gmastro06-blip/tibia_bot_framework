from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

import cv2
import numpy as np

# Allow running as a standalone script from repo root.
project_root = Path(__file__).resolve().parents[1]
src_dir = project_root / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from vision.presence import (
    _saturation_pct,
    _strip_border,
    _to_gray,
    is_hungry_hsv,
    is_nonempty_equipment_slot,
)


def _pick_latest(rois_dir: Path, pattern: str) -> tuple[float, Path] | None:
    items: list[tuple[float, Path]] = []
    for p in glob.glob(str(rois_dir / pattern)):
        path = Path(p)
        try:
            ts = float(path.name.split("_", 1)[0])
        except Exception:
            ts = 0.0
        items.append((ts, path))
    items.sort(key=lambda t: t[0], reverse=True)
    return items[0] if items else None


def _pick_latest_n(rois_dir: Path, pattern: str, *, n: int) -> list[tuple[float, Path]]:
    items: list[tuple[float, Path]] = []
    for p in glob.glob(str(rois_dir / pattern)):
        path = Path(p)
        try:
            ts = float(path.name.split("_", 1)[0])
        except Exception:
            ts = 0.0
        items.append((ts, path))
    items.sort(key=lambda t: t[0], reverse=True)
    return items[: max(0, int(n))]


def _analyze_slot(path: Path) -> None:
    img = cv2.imread(str(path))
    if img is None:
        print(f"could not read {path}")
        return

    gray = _strip_border(_to_gray(img))
    mean = float(gray.mean())
    std = float(gray.std())
    sat_30_40 = _saturation_pct(img, s_thr=30, v_thr=40, strip_border=True)
    sat_50_60 = _saturation_pct(img, s_thr=50, v_thr=60, strip_border=True)

    pred_default = is_nonempty_equipment_slot(img)
    pred_strict = is_nonempty_equipment_slot(
        img,
        min_sat_pct=0.012,
        sat_thr=40,
        v_thr=60,
    )

    print(
        f"{path.name} shape={img.shape} mean={mean:.2f} std={std:.2f} "
        f"sat30/40={sat_30_40:.4f} sat50/60={sat_50_60:.4f} "
        f"pred_default={int(pred_default)} pred_strict={int(pred_strict)}"
    )


def _analyze_hungry(path: Path) -> None:
    img = cv2.imread(str(path))
    if img is None:
        print(f"could not read {path}")
        return
    pred = bool(is_hungry_hsv(img))

    # Recompute pct for visibility
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    lower = np.array([8, 80, 80], dtype=np.uint8)
    upper = np.array([35, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)
    pct = float(np.count_nonzero(mask)) / float(mask.size)

    print(f"{path.name} shape={img.shape} hungry={int(pred)} pct={pct:.4f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Debug ring/amulet/hungry detection from replay ROI crops")
    ap.add_argument(
        "--rois-dir",
        default="",
        help="Path to replay rois/ folder (e.g., logs/replay_presence_test/rois). If omitted, tries common defaults.",
    )
    ap.add_argument(
        "--scan-n",
        type=int,
        default=200,
        help="How many most-recent ring_slot crops to scan for distribution stats.",
    )
    args = ap.parse_args()

    rois_dir: Path | None = None
    if str(args.rois_dir or "").strip():
        rois_dir = Path(str(args.rois_dir).strip())
    else:
        # Common locations
        candidates = [
            Path("logs/replay/rois"),
            Path("logs/replay_env/rois"),
            Path("logs/replay_smoke/rois"),
            Path("logs/replay_presence_test/rois"),
        ]
        for c in candidates:
            if c.is_dir():
                rois_dir = c
                break

    if rois_dir is None or not rois_dir.is_dir():
        print(f"rois_dir={rois_dir}")
        print("No replay rois dir found. Use --rois-dir.")
        return

    print(f"rois_dir={rois_dir}")

    ring = _pick_latest(rois_dir, "*_ring_slot.png")
    amulet = _pick_latest(rois_dir, "*_amulet_slot.png")
    hungry = _pick_latest(rois_dir, "*_hungry_icon.png")
    states = _pick_latest(rois_dir, "*_states_icons.png")

    print("latest ring_slot", ring)
    print("latest amulet_slot", amulet)
    print("latest hungry_icon", hungry)
    print("latest states_icons", states)

    if ring:
        _analyze_slot(ring[1])
    if amulet:
        _analyze_slot(amulet[1])

    # Quick distribution scan (helps tune thresholds).
    try:
        N = max(0, int(args.scan_n))
        ring_items = _pick_latest_n(rois_dir, "*_ring_slot.png", n=N)
        if ring_items:
            n_true = 0
            sat_vals = []
            std_vals = []
            for _ts, p in ring_items:
                img = cv2.imread(str(p))
                if img is None:
                    continue
                if is_nonempty_equipment_slot(img):
                    n_true += 1
                gray = _strip_border(_to_gray(img))
                try:
                    std_vals.append(float(gray.std()))
                except Exception:
                    pass
                try:
                    sat_vals.append(float(_saturation_pct(img, s_thr=30, v_thr=40, strip_border=True)))
                except Exception:
                    pass
            if std_vals and sat_vals:
                std_vals.sort()
                sat_vals.sort()
                print(
                    f"ring_slot scan last {len(ring_items)}: true={n_true} "
                    f"std[p50]={std_vals[len(std_vals)//2]:.1f} std[p90]={std_vals[int(len(std_vals)*0.9)]:.1f} "
                    f"sat[p50]={sat_vals[len(sat_vals)//2]:.4f} sat[p90]={sat_vals[int(len(sat_vals)*0.9)]:.4f}"
                )
    except Exception:
        pass

    if hungry:
        _analyze_hungry(hungry[1])
    if states:
        _analyze_hungry(states[1])


if __name__ == "__main__":
    main()
