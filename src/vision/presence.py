from __future__ import annotations

from typing import Optional

import numpy as np


def detect_status_icons(crop: Optional[np.ndarray]) -> dict[str, float]:
    """Best-effort status icons detector.

    This is a thin wrapper over `vision.status_icons.detect_status_icons()` to keep
    'presence' as the central place for lightweight HUD/icon presence checks.

    Returns a dict{status_name: confidence}. Empty dict means "no detection possible".
    """

    if crop is None or not isinstance(crop, np.ndarray) or crop.size == 0:
        return {}
    try:
        from vision.status_icons import detect_status_icons as _detect

        return dict(_detect(crop) or {})
    except Exception:
        return {}


def _to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.ndim == 3 and img.shape[2] >= 3:
        # BGR -> gray approximation
        b = img[:, :, 0].astype(np.float32)
        g = img[:, :, 1].astype(np.float32)
        r = img[:, :, 2].astype(np.float32)
        gray = (0.114 * b + 0.587 * g + 0.299 * r).astype(np.uint8)
        return gray
    return img.reshape(-1).astype(np.uint8)


def _strip_border(gray: np.ndarray, *, frac: float = 0.125) -> np.ndarray:
    """Remove a thin border to avoid slot-frame artifacts.

    Many HUD slots have a high-contrast border even when empty.
    """

    try:
        h, w = int(gray.shape[0]), int(gray.shape[1])
        if h <= 0 or w <= 0:
            return gray
        pad = int(max(1, round(min(h, w) * float(frac))))
        if h > (pad * 2 + 1) and w > (pad * 2 + 1):
            return gray[pad:-pad, pad:-pad]
    except Exception:
        return gray
    return gray


def _saturation_pct(
    crop_bgr: np.ndarray,
    *,
    s_thr: int = 30,
    v_thr: int = 40,
    strip_border: bool = True,
) -> float:
    """Approximate HSV saturation percentage without OpenCV.

    Computes pct of pixels where:
      - V >= v_thr
      - S >= s_thr

    where V = max(R,G,B) and S ~= (V - min(R,G,B)) / max(V,1).
    """

    if crop_bgr is None or not isinstance(crop_bgr, np.ndarray) or crop_bgr.size == 0:
        return 0.0
    if crop_bgr.ndim != 3 or int(crop_bgr.shape[2]) < 3:
        return 0.0

    img = crop_bgr
    if strip_border:
        try:
            gray = _to_gray(img)
            gray2 = _strip_border(gray)
            # Apply same crop window to color image.
            if gray2.shape != gray.shape:
                pad_y = (int(gray.shape[0]) - int(gray2.shape[0])) // 2
                pad_x = (int(gray.shape[1]) - int(gray2.shape[1])) // 2
                img = img[pad_y : pad_y + gray2.shape[0], pad_x : pad_x + gray2.shape[1]]
        except Exception:
            img = crop_bgr

    try:
        b = img[:, :, 0].astype(np.float32)
        g = img[:, :, 1].astype(np.float32)
        r = img[:, :, 2].astype(np.float32)
        vmax = np.maximum(np.maximum(r, g), b)
        vmin = np.minimum(np.minimum(r, g), b)
        # Avoid divide-by-zero for dark pixels.
        s = np.where(vmax > 1.0, (vmax - vmin) / vmax, 0.0) * 255.0
        v_ok = vmax >= float(v_thr)
        s_ok = s >= float(s_thr)
        ok = v_ok & s_ok
        return float(np.count_nonzero(ok)) / float(ok.size)
    except Exception:
        return 0.0


def is_nonempty_icon(
    crop: Optional[np.ndarray],
    *,
    min_std: float = 12.0,
    min_mean: float = 4.0,
) -> bool:
    """Best-effort heuristic: decide if a small HUD/icon crop is 'present' (non-empty).

    This is intentionally simple and robust:
    - Empty slots/areas tend to be very uniform => low std.
    - We also guard against pure-black crops.

    You should calibrate tight ROIs for best results.
    """

    if crop is None:
        return False
    if not isinstance(crop, np.ndarray):
        return False
    if crop.size == 0:
        return False

    gray = _to_gray(crop)
    # Many HUD slots have a high-contrast border. To avoid false positives on
    # empty slots, measure texture on the inner area (border-stripped).
    gray = _strip_border(gray)
    try:
        mean = float(gray.mean())
        std = float(gray.std())
    except Exception:
        return False

    if mean < float(min_mean):
        return False
    return std >= float(min_std)


def is_nonempty_equipment_slot(
    crop: Optional[np.ndarray],
    *,
    min_std: float = 18.0,
    min_mean: float = 8.0,
    min_sat_pct: float = 0.006,
    sat_thr: int = 30,
    v_thr: int = 40,
    high_std: float = 45.0,
) -> bool:
    """Heuristic for equipment slots (ring/amulet) that reduces false positives.

    Problem: empty slots can still have texture/gradients that trigger std-based
    checks. We require either:
      - meaningful color content (HSV saturation pct), or
      - very high grayscale texture (high_std), to keep gray items possible.
    """

    if crop is None or not isinstance(crop, np.ndarray) or crop.size == 0:
        return False

    gray = _to_gray(crop)
    gray = _strip_border(gray)
    try:
        mean = float(gray.mean())
        std = float(gray.std())
    except Exception:
        return False

    if mean < float(min_mean):
        return False
    if std < float(min_std):
        return False

    # Prefer colored pixels as a strong indicator of an item icon.
    try:
        sat_pct = _saturation_pct(crop, s_thr=int(sat_thr), v_thr=int(v_thr), strip_border=True)
    except Exception:
        sat_pct = 0.0
    if float(sat_pct) >= float(min_sat_pct):
        return True

    # Allow high-texture grayscale icons (some rings/amulets can be mostly gray).
    return std >= float(high_std)


def is_hungry_hsv(
    crop: Optional[np.ndarray],
    *,
    min_pct: float = 0.012,
    low_h: int = 8,
    low_s: int = 80,
    low_v: int = 80,
    high_h: int = 35,
    high_s: int = 255,
    high_v: int = 255,
) -> bool:
    """Heuristic hunger-icon detector using an HSV color band.

    This is a fallback when a dedicated `hungry_icon` ROI isn't available.
    It flags True when the crop contains a noticeable amount of orange/yellow.

    Returns False if OpenCV isn't available.
    """

    if crop is None or not isinstance(crop, np.ndarray) or crop.size == 0:
        return False

    try:
        import cv2

        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        lower = np.array([int(low_h), int(low_s), int(low_v)], dtype=np.uint8)
        upper = np.array([int(high_h), int(high_s), int(high_v)], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)  # type: ignore[arg-type]
        pct = float(np.count_nonzero(mask)) / float(mask.size)
        return pct >= float(min_pct)
    except Exception:
        return False
