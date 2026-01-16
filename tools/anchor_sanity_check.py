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


def _load_anchor_cfg(path: str) -> Mapping[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        rois = cfg.get("rois_guess_norm", cfg.get("rois", {}))
        if isinstance(rois, Mapping):
            raw = rois.get("_anchor")
            if isinstance(raw, Mapping):
                return raw
    except Exception:
        return None
    return None


def _pick_anchor_roi(rois: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]] | tuple[None, None]:
    for key in ("equipment_slots", "hpmp_low_panel", "skills_panel", "right_hud_panel"):
        try:
            v = rois.get(key)
            if isinstance(v, Mapping):
                return str(key), v
        except Exception:
            continue
    return None, None


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compute AnchorTracker dx/dy/score on a live capture (helps debug moved HUD/ROIs)."
    )
    p.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or 2))
    p.add_argument("--out-dir", type=str, default=str(Path("logs") / "anchor_sanity"))
    p.add_argument(
        "--rois",
        type=str,
        default="",
        help="Path to ROI config JSON. If omitted, uses ROIS_CONFIG env var, else auto-picks by resolution.",
    )
    p.add_argument("--retries", type=int, default=60)
    p.add_argument("--search-radius", type=int, default=720)
    p.add_argument("--save-overlay", action="store_true")
    p.add_argument(
        "--template",
        type=str,
        default="",
        help="Optional template PNG path. If omitted, uses _anchor.template_path from the ROIs config when available.",
    )
    p.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Minimum score to accept (tool default 0.0 so you always get dx/dy + score).",
    )
    return p.parse_args()


def main() -> int:
    _add_src_to_syspath()

    import cv2

    from capture.dxgi_capture import DXGICapture
    from vision.anchor_tracker import AnchorTracker
    from vision.ocr import OCRProcessor

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
    cfg_path = _resolve_rois_path(args.rois) or _resolve_rois_path(os.getenv("ROIS_CONFIG", "")) or _pick_config_file(resolution)
    rois, source_resolution = _load_config(cfg_path)
    rois = dict(rois)
    rois["_source_resolution"] = source_resolution

    anchor_cfg_from_file = _load_anchor_cfg(cfg_path)

    anchor_name_display = ""

    # Prefer explicit _anchor config if present.
    if anchor_cfg_from_file is not None:
        try:
            rois["_anchor"] = dict(anchor_cfg_from_file)
        except Exception:
            rois["_anchor"] = anchor_cfg_from_file

        try:
            if isinstance(anchor_cfg_from_file, Mapping):
                # Optional field; may not exist.
                anchor_name_display = str(anchor_cfg_from_file.get("roi_name") or "")
        except Exception:
            anchor_name_display = ""
    else:
        # Fallback to an auto anchor roi_norm, but require a real template path.
        anchor_name, anchor_roi = _pick_anchor_roi(rois)
        if anchor_name is None or anchor_roi is None:
            print(
                "FAIL: no suitable anchor ROI found in config (expected equipment_slots/hpmp_low_panel/skills_panel/right_hud_panel)"
            )
            return 3

        tmpl = _resolve_rois_path(args.template) or str(args.template or "").strip()
        if not tmpl:
            # Default: if you previously ran create_anchor_template, use it.
            try:
                repo_root = Path(__file__).resolve().parent.parent
                cand = repo_root / "data" / "anchors" / "hud_anchor.png"
                if cand.exists() and cand.is_file():
                    tmpl = str(cand)
            except Exception:
                tmpl = ""
        if not tmpl:
            print("FAIL: no _anchor config found and no --template provided")
            print("Hint: ejecuta 'Configurar ancla' para crear data/anchors/hud_anchor.png")
            return 4

        rois["_anchor"] = {
            "roi_norm": dict(anchor_roi),
            "template_mode": "file",
            "template_path": tmpl,
            "roi_name": str(anchor_name),
            "search_radius_px": int(max(80, int(args.search_radius))),
            "min_score": float(args.min_score),
            "update_interval_s": 0.0,
            "smoothing": 1.0,
        }
        anchor_name_display = str(anchor_name)

    ocr = OCRProcessor()
    tracker = AnchorTracker()

    # Compute one update. The tracker writes into `rois` dict.
    try:
        tracker.maybe_update(frame=frame, rois=rois, resolution=resolution, roi_to_px=ocr._roi_to_px)
    except Exception:
        pass

    dx = None
    dy = None
    score = None
    try:
        off = rois.get("_roi_offset_px")
        if isinstance(off, (list, tuple)) and len(off) >= 2:
            dx = float(off[0])
            dy = float(off[1])
        sc = rois.get("_roi_offset_score")
        if sc is not None:
            score = float(sc)
    except Exception:
        dx = dy = score = None

    ts = float(time.time())
    base_dir = out_dir / f"{ts:.6f}"
    base_dir.mkdir(parents=True, exist_ok=True)

    # Optional overlay for quick visual confirmation.
    overlay_path = None
    if args.save_overlay:
        try:
            overlay = frame.copy()
            # Draw all ROIs (green) + anchor ROI (thicker).
            for name, roi_def in list(rois.items()):
                if str(name).startswith("_"):
                    continue
                if not isinstance(roi_def, Mapping):
                    continue
                try:
                    x, y, w, h = ocr._roi_to_px(frame, rois, resolution, roi_def)  # type: ignore[arg-type]
                    cv2.rectangle(overlay, (int(x), int(y)), (int(x + w), int(y + h)), (0, 255, 0), 1)
                except Exception:
                    continue

            try:
                anchor_roi_norm = None
                try:
                    raw = rois.get("_anchor")
                    if isinstance(raw, Mapping):
                        anchor_roi_norm = raw.get("roi_norm")
                except Exception:
                    anchor_roi_norm = None

                if anchor_roi_norm is None and "anchor_roi" in locals():
                    anchor_roi_norm = anchor_roi

                if not isinstance(anchor_roi_norm, Mapping):
                    raise RuntimeError("no anchor_roi_norm")

                ax, ay, aw, ah = ocr._roi_to_px(frame, rois, resolution, anchor_roi_norm)  # type: ignore[arg-type]
                cv2.rectangle(overlay, (int(ax), int(ay)), (int(ax + aw), int(ay + ah)), (255, 255, 0), 2)
                cv2.putText(
                    overlay,
                    f"anchor={anchor_name_display}",
                    (int(ax), max(14, int(ay) - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (255, 255, 0),
                    1,
                )
            except Exception:
                pass

            msg = f"dx={dx:.0f} dy={dy:.0f} score={score:.2f}" if (dx is not None and dy is not None and score is not None) else "dx/dy/score unavailable"
            cv2.putText(overlay, msg, (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)

            overlay_path = str(base_dir / "overlay.png")
            cv2.imwrite(overlay_path, overlay)
        except Exception:
            overlay_path = None

    # Read back what anchor ROI name we used (best-effort).
    anchor_name = None
    try:
        raw = rois.get("_anchor")
        if isinstance(raw, Mapping):
            anchor_name = str(raw.get("roi_name") or "") or None
    except Exception:
        anchor_name = None

    report = {
        "ts": ts,
        "config": cfg_path,
        "resolution": list(resolution),
        "source_resolution": source_resolution,
        "anchor": {
            "roi_name": anchor_name,
            "search_radius_px": int(args.search_radius),
            "min_score": float(args.min_score),
            "dx_src_px": dx,
            "dy_src_px": dy,
            "score": score,
        },
        "artifacts": {
            "overlay": "overlay.png" if overlay_path else None,
        },
    }
    (base_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OUT_DIR: {base_dir}")
    print(f"Config: {cfg_path} | anchor={anchor_name} | frame={resolution[0]}x{resolution[1]} source={source_resolution}")
    print(f"Anchor: dx={dx} dy={dy} score={score}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
