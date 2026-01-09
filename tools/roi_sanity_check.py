from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np


def _add_src_to_syspath() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def _pick_config_file(resolution: tuple[int, int]) -> str:
    width, height = resolution
    config_files = {
        (2048, 1076): "configs/rois_guess.json",
        (1920, 1080): "configs/rois_guess_1920x1080.json",
        (1920, 1009): "configs/rois_guess_1920x1080.json",
    }
    return config_files.get((width, height), "configs/rois_guess_1920x1080.json")


def _load_config(path: str) -> tuple[dict, list[int]]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg.get("rois_guess_norm", {}), cfg.get("source_resolution", [0, 0])


def _roi_to_px(
    *,
    frame: np.ndarray,
    rois: Mapping[str, Any],
    resolution: tuple[int, int],
    roi_def: Mapping[str, Any],
) -> tuple[int, int, int, int]:
    """Convert ROI (normalized or px-in-source) into frame pixels (matches OCRProcessor._roi_to_px)."""
    frame_w, frame_h = int(frame.shape[1]), int(frame.shape[0])
    source_resolution = rois.get("_source_resolution") if hasattr(rois, "get") else None
    if (
        isinstance(source_resolution, (list, tuple))
        and len(source_resolution) == 2
        and source_resolution[0]
        and source_resolution[1]
    ):
        source_w, source_h = int(source_resolution[0]), int(source_resolution[1])
    else:
        source_w, source_h = int(resolution[0]), int(resolution[1])

    scale = min(frame_w / source_w, frame_h / source_h) if source_w and source_h else 1.0
    content_w = source_w * scale
    content_h = source_h * scale
    offset_x = (frame_w - content_w) / 2.0
    offset_y = (frame_h - content_h) / 2.0

    unit = str(roi_def.get("unit", "") if hasattr(roi_def, "get") else "").lower()
    x_val = roi_def.get("x") if hasattr(roi_def, "get") else None
    y_val = roi_def.get("y") if hasattr(roi_def, "get") else None
    w_val = roi_def.get("w") if hasattr(roi_def, "get") else None
    h_val = roi_def.get("h") if hasattr(roi_def, "get") else None

    def _f(v: Any, default: float) -> float:
        try:
            if v is None:
                return default
            return float(v)
        except Exception:
            return default

    def _is_normalized(v: Any) -> bool:
        try:
            vf = float(v)
        except Exception:
            return False
        return 0.0 <= vf <= 1.0

    is_norm = unit != "px" and _is_normalized(x_val) and _is_normalized(y_val) and _is_normalized(w_val) and _is_normalized(h_val)
    if is_norm:
        x_src = _f(x_val, 0.0) * source_w
        y_src = _f(y_val, 0.0) * source_h
        w_src = _f(w_val, 0.0) * source_w
        h_src = _f(h_val, 0.0) * source_h
    else:
        x_src = _f(x_val, 0.0)
        y_src = _f(y_val, 0.0)
        w_src = _f(w_val, 0.0)
        h_src = _f(h_val, 0.0)

    x = int(round(offset_x + x_src * scale))
    y = int(round(offset_y + y_src * scale))
    w = int(round(w_src * scale))
    h = int(round(h_src * scale))
    x = max(0, min(x, frame_w - 1))
    y = max(0, min(y, frame_h - 1))
    w = max(1, min(w, frame_w - x))
    h = max(1, min(h, frame_h - y))
    return x, y, w, h


def _contains(outer: tuple[int, int, int, int], inner: tuple[int, int, int, int], *, pad: int = 0) -> bool:
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return (
        ix >= (ox - pad)
        and iy >= (oy - pad)
        and (ix + iw) <= (ox + ow + pad)
        and (iy + ih) <= (oy + oh + pad)
    )


@dataclass
class RoiCheck:
    name: str
    status: str  # OK/WARN/FAIL/SKIP
    reason: str
    rect: tuple[int, int, int, int] | None = None


def _crop(frame: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = rect
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(int(frame.shape[1]), x0 + max(1, int(w)))
    y1 = min(int(frame.shape[0]), y0 + max(1, int(h)))
    return frame[y0:y1, x0:x1].copy()


def _is_uniform(crop: np.ndarray) -> bool:
    try:
        return bool(np.all(crop == crop.reshape(-1, crop.shape[-1])[0])) if crop.ndim == 3 else bool(np.all(crop == crop.flat[0]))
    except Exception:
        return False


def _stats(crop: np.ndarray) -> dict[str, float]:
    if crop.size == 0:
        return {"mean": 0.0, "std": 0.0, "black_pct": 1.0}

    if crop.ndim == 3:
        gray = (0.114 * crop[:, :, 0] + 0.587 * crop[:, :, 1] + 0.299 * crop[:, :, 2]).astype(np.float32)
    else:
        gray = crop.astype(np.float32)

    mean = float(np.mean(gray))
    std = float(np.std(gray))
    black_pct = float(np.mean(gray <= 5.0))
    return {"mean": mean, "std": std, "black_pct": black_pct}


def _default_min_size(name: str) -> tuple[int, int]:
    # Conservative defaults; tweakable via CLI.
    if name in {"hp_low_bar", "mp_low_bar"}:
        return 30, 4
    if name in {"hp_top_ocr", "mp_top_ocr", "cap_ocr", "soul_ocr"}:
        return 10, 6
    if name in {"ring_slot", "amulet_slot", "hungry_icon"}:
        return 8, 8
    return 16, 16


def _check_roi(
    *,
    name: str,
    frame: np.ndarray,
    rect: tuple[int, int, int, int],
    min_w: int,
    min_h: int,
    std_fail: float,
    std_warn: float,
    black_fail_pct: float,
    black_warn_pct: float,
) -> RoiCheck:
    x, y, w, h = rect
    if w <= 1 or h <= 1:
        return RoiCheck(name=name, status="FAIL", reason="empty rect", rect=rect)

    if x < 0 or y < 0 or (x + w) > frame.shape[1] or (y + h) > frame.shape[0]:
        # We still crop/clamp later; but this signals misalignment.
        return RoiCheck(name=name, status="FAIL", reason="rect out of bounds", rect=rect)

    if w < min_w or h < min_h:
        return RoiCheck(name=name, status="FAIL", reason=f"too small ({w}x{h} < {min_w}x{min_h})", rect=rect)

    crop = _crop(frame, rect)
    if crop.size == 0:
        return RoiCheck(name=name, status="FAIL", reason="empty crop", rect=rect)

    if np.all(crop == 0):
        return RoiCheck(name=name, status="FAIL", reason="all black", rect=rect)

    if _is_uniform(crop):
        return RoiCheck(name=name, status="FAIL", reason="uniform (no content variation)", rect=rect)

    s = _stats(crop)
    if s["black_pct"] >= black_fail_pct:
        return RoiCheck(name=name, status="FAIL", reason=f"mostly black ({s['black_pct']*100:.1f}%)", rect=rect)

    if s["std"] < std_fail:
        return RoiCheck(name=name, status="FAIL", reason=f"very low contrast (std={s['std']:.2f})", rect=rect)

    # WARN thresholds
    warns: list[str] = []
    if s["black_pct"] >= black_warn_pct:
        warns.append(f"high black pct ({s['black_pct']*100:.1f}%)")
    if s["std"] < std_warn:
        warns.append(f"low contrast (std={s['std']:.2f})")

    if warns:
        return RoiCheck(name=name, status="WARN", reason="; ".join(warns), rect=rect)

    return RoiCheck(name=name, status="OK", reason="ok", rect=rect)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sanity-check configured ROIs on a live capture and save debug artifacts.")
    p.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or 2))
    p.add_argument("--out-dir", type=str, default=str(Path("logs") / "roi_sanity"))
    p.add_argument("--names", type=str, default="", help="Comma-separated ROI names to check (default: all in config)")
    p.add_argument("--strict", action="store_true", help="Exit non-zero if any ROI FAILs")

    # Tunables
    p.add_argument("--std-fail", type=float, default=1.0)
    p.add_argument("--std-warn", type=float, default=3.0)
    p.add_argument("--black-fail-pct", type=float, default=0.98)
    p.add_argument("--black-warn-pct", type=float, default=0.90)
    p.add_argument("--min-w", type=int, default=0, help="Override minimum width for all ROIs (0=auto)")
    p.add_argument("--min-h", type=int, default=0, help="Override minimum height for all ROIs (0=auto)")

    p.add_argument("--save-all-crops", action="store_true", help="Save crops for OK ROIs too")
    p.add_argument("--save-overlay", action="store_true", help="Save an overlay image with ROI boxes")
    return p.parse_args()


def main() -> int:
    _add_src_to_syspath()

    import cv2

    from capture.dxgi_capture import DXGICapture

    args = _parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = DXGICapture(force_monitor=args.monitor)

    frame = None
    for _ in range(60):
        frame = cap.capture()
        if frame is not None:
            break
    if frame is None:
        print("FAIL: no frame captured")
        return 2

    resolution = (int(frame.shape[1]), int(frame.shape[0]))
    cfg_path = _pick_config_file(resolution)
    rois, source_resolution = _load_config(cfg_path)
    rois = dict(rois)
    rois["_source_resolution"] = source_resolution

    names: list[str]
    if args.names.strip():
        names = [n.strip() for n in args.names.split(",") if n.strip()]
    else:
        names = [k for k in rois.keys() if not str(k).startswith("_")]

    checks: list[RoiCheck] = []
    overlay = frame.copy()

    ts = time.time()
    base_dir = out_dir / f"{ts:.6f}"
    base_dir.mkdir(parents=True, exist_ok=True)

    print(f"Config: {cfg_path} | frame={resolution[0]}x{resolution[1]} source={source_resolution}")

    # Precompute rects to also validate nesting.
    rects: dict[str, tuple[int, int, int, int]] = {}
    for name in names:
        roi_def = rois.get(name)
        if not isinstance(roi_def, Mapping):
            checks.append(RoiCheck(name=name, status="SKIP", reason="missing or invalid ROI def"))
            continue

        try:
            rect = _roi_to_px(frame=frame, rois=rois, resolution=resolution, roi_def=roi_def)
            rects[name] = rect
        except Exception as e:
            checks.append(RoiCheck(name=name, status="FAIL", reason=f"roi_to_px failed: {e}"))
            continue

        if args.min_w > 0 and args.min_h > 0:
            min_w, min_h = int(args.min_w), int(args.min_h)
        else:
            min_w, min_h = _default_min_size(name)
            if args.min_w > 0:
                min_w = int(args.min_w)
            if args.min_h > 0:
                min_h = int(args.min_h)

        chk = _check_roi(
            name=name,
            frame=frame,
            rect=rect,
            min_w=min_w,
            min_h=min_h,
            std_fail=float(args.std_fail),
            std_warn=float(args.std_warn),
            black_fail_pct=float(args.black_fail_pct),
            black_warn_pct=float(args.black_warn_pct),
        )
        checks.append(chk)

        # Save artifacts for WARN/FAIL (and optionally OK)
        if chk.rect is not None and (args.save_all_crops or chk.status in {"WARN", "FAIL"}):
            crop = _crop(frame, chk.rect)
            cv2.imwrite(str(base_dir / f"{name}_{chk.status}.png"), crop)

        if chk.rect is not None:
            color = (0, 255, 0)
            if chk.status == "WARN":
                color = (0, 215, 255)
            elif chk.status == "FAIL":
                color = (0, 0, 255)
            x, y, w, h = chk.rect
            cv2.rectangle(overlay, (x, y), (x + w, y + h), color, 2)
            cv2.putText(
                overlay,
                f"{name}:{chk.status}",
                (x, max(12, y - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
            )

    # Nesting checks (structural sanity)
    nesting = {
        "hp_top_ocr": "hpmp_top_strip",
        "mp_top_ocr": "hpmp_top_strip",
        "cap_ocr": "skills_panel",
        "soul_ocr": "skills_panel",
        "battlelist_rows": "battlelist_panel",
        "minimap_content": "right_hud_panel",
        "equipment_slots": "right_hud_panel",
        "states_icons": "right_hud_panel",
        "hpmp_low_panel": "right_hud_panel",
        "hp_low_bar": "hpmp_low_panel",
        "mp_low_bar": "hpmp_low_panel",
        "ring_slot": "equipment_slots",
        "amulet_slot": "equipment_slots",
        "hungry_icon": "states_icons",
    }
    for child, parent in nesting.items():
        if child in rects and parent in rects:
            if not _contains(rects[parent], rects[child], pad=2):
                checks.append(
                    RoiCheck(
                        name=f"nest:{child}",
                        status="FAIL",
                        reason=f"{child} not inside {parent}",
                        rect=rects[child],
                    )
                )

    # Summary
    n_ok = sum(1 for c in checks if c.status == "OK")
    n_warn = sum(1 for c in checks if c.status == "WARN")
    n_fail = sum(1 for c in checks if c.status == "FAIL")
    n_skip = sum(1 for c in checks if c.status == "SKIP")

    for c in checks:
        if c.status in {"WARN", "FAIL"}:
            print(f"{c.status}: {c.name}: {c.reason}")

    print(f"Summary: OK={n_ok} WARN={n_warn} FAIL={n_fail} SKIP={n_skip}")

    if args.save_overlay:
        cv2.imwrite(str(base_dir / "overlay.png"), overlay)
        print(f"Saved overlay: {base_dir / 'overlay.png'}")

    # Save machine-readable report
    report = {
        "ts": ts,
        "config": cfg_path,
        "resolution": list(resolution),
        "source_resolution": source_resolution,
        "summary": {"ok": n_ok, "warn": n_warn, "fail": n_fail, "skip": n_skip},
        "checks": [
            {
                "name": c.name,
                "status": c.status,
                "reason": c.reason,
                "rect": list(c.rect) if c.rect is not None else None,
            }
            for c in checks
        ],
    }
    (base_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.strict and n_fail:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
