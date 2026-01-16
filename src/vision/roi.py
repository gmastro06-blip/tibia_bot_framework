from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class RoiPxResult:
    ok: bool
    roi: tuple[int, int, int, int] | None
    reason: str
    in_bounds: bool
    unclamped_roi: tuple[float, float, float, float] | None


def _f(v: Any, default: float) -> float:
    try:
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _is_norm(v: Any) -> bool:
    try:
        fv = float(v)
        return 0.0 <= fv <= 1.0
    except Exception:
        return False


def roi_to_px_result(
    *,
    frame_shape: tuple[int, int],
    rois: Mapping[str, Any],
    resolution: tuple[int, int],
    roi_def: Mapping[str, Any],
    min_w: int = 6,
    min_h: int = 6,
    min_in_bounds_area_ratio: float = 0.20,
) -> RoiPxResult:
    """Convert ROI definition into frame pixel coordinates.

    Supports:
    - Normalized ROIs: {x,y,w,h} floats in [0..1]
    - Source-pixel ROIs: {x,y,w,h} in px, either via `unit: "px"` or values not in [0..1]

    Applies:
    - Letterboxing (source_resolution -> frame)
    - Global HUD offset (rois["_roi_offset_px"]) in source pixels

    Returns clamped ROI in frame pixels plus debug meta and a reason if invalid.
    """

    frame_h, frame_w = int(frame_shape[0]), int(frame_shape[1])
    if frame_w <= 0 or frame_h <= 0:
        return RoiPxResult(
            ok=False,
            roi=None,
            reason="invalid_frame",
            in_bounds=False,
            unclamped_roi=None,
        )

    # Determine source resolution used to author the ROIs.
    src = rois.get("_source_resolution") if hasattr(rois, "get") else None
    if isinstance(src, (list, tuple)) and len(src) == 2 and src[0] and src[1]:
        source_w, source_h = int(src[0]), int(src[1])
    else:
        source_w, source_h = int(resolution[0]), int(resolution[1])

    if source_w <= 0 or source_h <= 0:
        source_w, source_h = frame_w, frame_h

    scale = min(frame_w / float(source_w), frame_h / float(source_h))
    content_w = float(source_w) * float(scale)
    content_h = float(source_h) * float(scale)
    offset_x = (float(frame_w) - float(content_w)) / 2.0
    offset_y = (float(frame_h) - float(content_h)) / 2.0

    unit = str(roi_def.get("unit", "") or "").strip().lower()
    x_val = roi_def.get("x")
    y_val = roi_def.get("y")
    w_val = roi_def.get("w")
    h_val = roi_def.get("h")

    is_norm = unit != "px" and _is_norm(x_val) and _is_norm(y_val) and _is_norm(w_val) and _is_norm(h_val)

    if is_norm:
        x_src = _f(x_val, 0.0) * float(source_w)
        y_src = _f(y_val, 0.0) * float(source_h)
        w_src = _f(w_val, 0.0) * float(source_w)
        h_src = _f(h_val, 0.0) * float(source_h)
    else:
        x_src = _f(x_val, 0.0)
        y_src = _f(y_val, 0.0)
        w_src = _f(w_val, 0.0)
        h_src = _f(h_val, 0.0)

    # Optional global offset in *source px*
    try:
        off = rois.get("_roi_offset_px") if hasattr(rois, "get") else None
        if isinstance(off, (list, tuple)) and len(off) >= 2:
            x_src += float(off[0])
            y_src += float(off[1])
    except Exception:
        pass

    # Unclamped ROI in frame pixels
    x_u = offset_x + (x_src * float(scale))
    y_u = offset_y + (y_src * float(scale))
    w_u = w_src * float(scale)
    h_u = h_src * float(scale)

    # Basic sanity on size (before clamping)
    if w_u < float(min_w) or h_u < float(min_h):
        return RoiPxResult(
            ok=False,
            roi=None,
            reason="invalid_roi:too_small",
            in_bounds=False,
            unclamped_roi=(x_u, y_u, w_u, h_u),
        )

    # Compute in-bounds overlap before clamping.
    try:
        x0 = float(x_u)
        y0 = float(y_u)
        x1 = float(x_u + w_u)
        y1 = float(y_u + h_u)
        ix0 = max(0.0, min(float(frame_w), x0))
        iy0 = max(0.0, min(float(frame_h), y0))
        ix1 = max(0.0, min(float(frame_w), x1))
        iy1 = max(0.0, min(float(frame_h), y1))
        inter_w = max(0.0, ix1 - ix0)
        inter_h = max(0.0, iy1 - iy0)
        inter_area = inter_w * inter_h
        area = max(1.0, float(w_u) * float(h_u))
        in_bounds = bool(inter_area >= area * float(min_in_bounds_area_ratio))
    except Exception:
        in_bounds = False

    if not in_bounds:
        return RoiPxResult(
            ok=False,
            roi=None,
            reason="invalid_roi:out_of_bounds",
            in_bounds=False,
            unclamped_roi=(x_u, y_u, w_u, h_u),
        )

    # Clamp to frame
    x = int(round(x_u))
    y = int(round(y_u))
    w = int(round(w_u))
    h = int(round(h_u))

    x = max(0, min(x, frame_w - 1))
    y = max(0, min(y, frame_h - 1))
    w = max(1, min(w, frame_w - x))
    h = max(1, min(h, frame_h - y))

    if w < int(min_w) or h < int(min_h):
        return RoiPxResult(
            ok=False,
            roi=None,
            reason="invalid_roi:too_small_after_clamp",
            in_bounds=False,
            unclamped_roi=(x_u, y_u, w_u, h_u),
        )

    return RoiPxResult(
        ok=True,
        roi=(x, y, w, h),
        reason="ok",
        in_bounds=True,
        unclamped_roi=(x_u, y_u, w_u, h_u),
    )


def roi_to_px(
    frame: Any,
    rois: Mapping[str, Any],
    resolution: tuple[int, int],
    roi_def: Mapping[str, Any],
) -> tuple[int, int, int, int]:
    """Backwards-compatible ROI conversion.

    Always returns a clamped ROI (never raises), falling back to a tiny ROI if invalid.
    Prefer `roi_to_px_result` when you need a reason / guards.
    """

    try:
        frame_h, frame_w = int(frame.shape[0]), int(frame.shape[1])
    except Exception:
        frame_h, frame_w = 1, 1

    res = roi_to_px_result(frame_shape=(frame_h, frame_w), rois=rois, resolution=resolution, roi_def=roi_def)
    if res.ok and res.roi is not None:
        return res.roi

    # Fail-safe: return a valid 1x1 ROI inside the frame.
    return (0, 0, 1, 1)
