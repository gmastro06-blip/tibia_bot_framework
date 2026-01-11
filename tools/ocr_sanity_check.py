from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping

import numpy as np


def _add_src_to_syspath() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def _resolve_rois_path(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        p = Path(s)
        if not p.is_absolute():
            repo_root = Path(__file__).resolve().parent.parent
            p = (repo_root / p).resolve()
        if p.exists() and p.is_file():
            return str(p)
    except Exception:
        return None
    return None


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
    return cfg.get("rois_guess_norm", cfg.get("rois", {})), cfg.get("source_resolution", [0, 0])


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="One-shot OCR sanity check against a live capture + current ROIs.")
    p.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or 2))
    p.add_argument("--out-dir", type=str, default=str(Path("logs") / "ocr_sanity"))
    p.add_argument(
        "--rois",
        type=str,
        default="",
        help="Path to ROI config JSON. If omitted, uses ROIS_CONFIG env var, else auto-picks by resolution.",
    )
    p.add_argument("--retries", type=int, default=60)
    p.add_argument("--save-overlay", action="store_true")
    p.add_argument("--save-crops", action="store_true")
    return p.parse_args()


def _crop(frame: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = rect
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(int(frame.shape[1]), x0 + max(1, int(w)))
    y1 = min(int(frame.shape[0]), y0 + max(1, int(h)))
    return frame[y0:y1, x0:x1].copy()


def main() -> int:
    _add_src_to_syspath()

    import cv2

    from capture.dxgi_capture import DXGICapture
    from vision.ocr import OCRProcessor
    from vision.presence import is_hungry_hsv, is_nonempty_icon

    args = _parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = DXGICapture(force_monitor=args.monitor)

    frame = None
    for _ in range(max(1, int(args.retries))):
        frame = cap.capture()
        if frame is not None:
            break
        time.sleep(0.05)

    if frame is None:
        print("FAIL: no frame captured")
        return 2

    resolution = (int(frame.shape[1]), int(frame.shape[0]))
    cfg_path = (
        _resolve_rois_path(args.rois)
        or _resolve_rois_path(os.getenv("ROIS_CONFIG", ""))
        or _pick_config_file(resolution)
    )
    rois, source_resolution = _load_config(cfg_path)
    rois = dict(rois)
    rois["_source_resolution"] = source_resolution

    ts = float(time.time())
    base_dir = out_dir / f"{ts:.6f}"
    base_dir.mkdir(parents=True, exist_ok=True)

    ocr = OCRProcessor()

    # OCR values
    hp_cur = hp_max = mp_cur = mp_max = None
    cap_val = None
    coords = None

    try:
        hp_cur, hp_max, mp_cur, mp_max = ocr.extract_hp_mp_full(frame, rois, resolution, rf_boxes=None)
    except Exception:
        pass

    try:
        cap_val = ocr.extract_capacity(frame, rois, resolution)
    except Exception:
        pass

    try:
        coords = ocr.extract_coords(frame, rois, resolution)
    except Exception:
        coords = None

    # Presence heuristics (optional)
    ring_equipped = None
    amulet_equipped = None
    hungry = None
    try:
        if rois.get("ring_slot") is not None:
            rx, ry, rw, rh = ocr._roi_to_px(frame, rois, resolution, rois["ring_slot"])  # type: ignore[index]
            ring_equipped = bool(is_nonempty_icon(frame[ry : ry + rh, rx : rx + rw]))
        if rois.get("amulet_slot") is not None:
            ax, ay, aw, ah = ocr._roi_to_px(frame, rois, resolution, rois["amulet_slot"])  # type: ignore[index]
            amulet_equipped = bool(is_nonempty_icon(frame[ay : ay + ah, ax : ax + aw]))

        if rois.get("hungry_icon") is not None:
            hx, hy, hw, hh = ocr._roi_to_px(frame, rois, resolution, rois["hungry_icon"])  # type: ignore[index]
            hungry = bool(is_nonempty_icon(frame[hy : hy + hh, hx : hx + hw]))
        elif rois.get("states_icons") is not None:
            sx, sy, sw, sh = ocr._roi_to_px(frame, rois, resolution, rois["states_icons"])  # type: ignore[index]
            crop = frame[sy : sy + sh, sx : sx + sw]
            min_pct = float(os.getenv("HUNGRY_MIN_PCT", "0.012"))
            low_h = int(float(os.getenv("HUNGRY_H_LOW", "8")))
            high_h = int(float(os.getenv("HUNGRY_H_HIGH", "35")))
            hungry = bool(is_hungry_hsv(crop, min_pct=min_pct, low_h=low_h, high_h=high_h))
    except Exception:
        pass

    # Save crops for the main ROIs if requested
    if args.save_crops:
        for name in [
            "hpmp_top_strip",
            "hp_top_ocr",
            "mp_top_ocr",
            "skills_panel",
            "cap_ocr",
            "soul_ocr",
            "equipment_slots",
            "states_icons",
            "ring_slot",
            "amulet_slot",
            "hungry_icon",
            "hpmp_low_panel",
            "hp_low_bar",
            "mp_low_bar",
            "coords_ocr",
        ]:
            try:
                if rois.get(name) is None:
                    continue
                rect = ocr._roi_to_px(frame, rois, resolution, rois[name])  # type: ignore[index]
                crop = _crop(frame, rect)
                if crop.size == 0:
                    continue
                cv2.imwrite(str(base_dir / f"{name}.png"), crop)
            except Exception:
                continue

    # Save overlay
    if args.save_overlay:
        overlay = frame.copy()
        try:
            for name, roi_def in rois.items():
                if str(name).startswith("_"):
                    continue
                if not isinstance(roi_def, Mapping):
                    continue
                try:
                    x, y, w, h = ocr._roi_to_px(frame, rois, resolution, roi_def)  # type: ignore[arg-type]
                    cv2.rectangle(overlay, (int(x), int(y)), (int(x + w), int(y + h)), (0, 255, 0), 2)
                    cv2.putText(
                        overlay,
                        str(name),
                        (int(x), max(12, int(y) - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (0, 255, 0),
                        1,
                    )
                except Exception:
                    continue
        except Exception:
            pass
        cv2.imwrite(str(base_dir / "overlay.png"), overlay)

    report = {
        "ts": ts,
        "config": cfg_path,
        "resolution": list(resolution),
        "source_resolution": source_resolution,
        "ocr": {
            "hp_current": hp_cur,
            "hp_max": hp_max,
            "mp_current": mp_cur,
            "mp_max": mp_max,
            "cap_current": cap_val,
            "coords": list(coords) if coords is not None else None,
        },
        "presence": {
            "ring_equipped": ring_equipped,
            "amulet_equipped": amulet_equipped,
            "hungry": hungry,
        },
    }
    (base_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # Console output for UI parsing
    print(f"OUT_DIR: {base_dir}")
    print(f"Config: {cfg_path} | frame={resolution[0]}x{resolution[1]} source={source_resolution}")
    print(
        f"HP: {hp_cur}/{hp_max} | MP: {mp_cur}/{mp_max} | Cap: {cap_val}"
        + (f" | Coords: {coords}" if coords is not None else "")
    )
    print(f"Ring: {ring_equipped} | Amulet: {amulet_equipped} | Hungry: {hungry}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
