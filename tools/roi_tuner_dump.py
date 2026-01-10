from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

import cv2


def _load_rois(path: str, *, fallback_resolution: tuple[int, int]) -> tuple[dict[str, Any], list[int]]:
    """Load ROI config in any supported format.

    Returns: (rois_dict, source_resolution)
    """

    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"ROI config not found: {path}")

    raw = json.loads(p.read_text(encoding="utf-8"))

    # Supported formats:
    # - {"source_resolution": [...], "rois_guess_norm": {...}}
    # - {"rois": {...}, "source_resolution": [...]}
    # - direct dict of rois
    if isinstance(raw, dict):
        rois = raw.get("rois_guess_norm")
        if rois is None:
            rois = raw.get("rois")
        if rois is None:
            rois = raw

        src = raw.get("source_resolution")
        if isinstance(src, (list, tuple)) and len(src) == 2:
            source_resolution = [int(src[0]), int(src[1])]
        else:
            source_resolution = [int(fallback_resolution[0]), int(fallback_resolution[1])]

        if not isinstance(rois, dict):
            raise ValueError(f"Invalid ROI format in {path}")

        # Keep a copy and embed the source res for downstream conversion.
        out = dict(rois)
        out["_source_resolution"] = source_resolution
        return out, source_resolution

    raise ValueError(f"Invalid JSON ROI root type in {path}: {type(raw)}")


def roi_to_px(
    *,
    frame_w: int,
    frame_h: int,
    rois: Mapping[str, Any],
    resolution: tuple[int, int],
    roi_def: Mapping[str, Any],
) -> tuple[int, int, int, int]:
    """Convert normalized (or px) ROI to frame pixels. Mirrors OCRProcessor._roi_to_px."""

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

    is_norm = (
        unit != "px"
        and _is_normalized(x_val)
        and _is_normalized(y_val)
        and _is_normalized(w_val)
        and _is_normalized(h_val)
    )

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

    try:
        off = rois.get("_roi_offset_px") if hasattr(rois, "get") else None
        if isinstance(off, (list, tuple)) and len(off) == 2:
            x_src += float(off[0] or 0.0)
            y_src += float(off[1] or 0.0)
    except Exception:
        pass

    x = int(round(offset_x + x_src * scale))
    y = int(round(offset_y + y_src * scale))
    w = int(round(w_src * scale))
    h = int(round(h_src * scale))

    x = max(0, min(x, frame_w - 1))
    y = max(0, min(y, frame_h - 1))
    w = max(1, min(w, frame_w - x))
    h = max(1, min(h, frame_h - y))
    return x, y, w, h


def main() -> int:
    # Match repo import style (modules live under src/)
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    sys.path.insert(0, str(src_dir))
    sys.path.insert(0, str(repo_root))

    from capture.dxgi_capture import DXGICapture

    ap = argparse.ArgumentParser(description="Dump a captured frame + ROI crops + overlay to help tune coords_ocr.")
    ap.add_argument("--rois", default=os.getenv("ROIS_CONFIG", "configs/rois_guess_1920x1080.json"))
    ap.add_argument("--out", default="logs/roi_tuner")
    ap.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or "2"))
    ap.add_argument(
        "--names",
        default=(
            "coords_ocr,"
            "minimap_content,"
            "right_hud_panel,"
            "hpmp_top_strip,"
            "hpmp_low_panel,"
            "skills_panel,"
            "battlelist_rows,"
            "chat_panel,"
            "game_viewport"
        ),
        help="Comma-separated ROI names to dump",
    )
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = DXGICapture(force_monitor=int(args.monitor))
    frame = cap.capture()
    if frame is None:
        print("Failed to capture frame (frame=None)")
        return 2

    frame_h, frame_w = int(frame.shape[0]), int(frame.shape[1])
    resolution = (frame_w, frame_h)

    rois, _src = _load_rois(args.rois, fallback_resolution=resolution)

    # Save raw frame
    frame_path = out_dir / "frame.png"
    cv2.imwrite(str(frame_path), frame)

    overlay = frame.copy()
    names = [n.strip() for n in str(args.names).split(",") if n.strip()]

    for name in names:
        roi_def = rois.get(name)
        if not isinstance(roi_def, dict):
            continue
        x, y, w, h = roi_to_px(frame_w=frame_w, frame_h=frame_h, rois=rois, resolution=resolution, roi_def=roi_def)
        crop = frame[y : y + h, x : x + w].copy()
        cv2.imwrite(str(out_dir / f"{name}.png"), crop)
        cv2.rectangle(overlay, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(
            overlay,
            name,
            (x + 2, max(12, y - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )

    overlay_path = out_dir / "frame_rois.png"
    cv2.imwrite(str(overlay_path), overlay)

    print(f"Wrote: {frame_path}")
    print(f"Wrote: {overlay_path}")
    print(f"Wrote ROI crops: {', '.join([str(out_dir / (n + '.png')) for n in names if (out_dir / (n + '.png')).exists()])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
