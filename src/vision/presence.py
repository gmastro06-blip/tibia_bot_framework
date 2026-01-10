from __future__ import annotations

from typing import Optional

import numpy as np


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
    try:
        h, w = int(gray.shape[0]), int(gray.shape[1])
        pad = max(1, min(h, w) // 8)
        if h > (pad * 2 + 1) and w > (pad * 2 + 1):
            gray = gray[pad:-pad, pad:-pad]
    except Exception:
        pass
    try:
        mean = float(gray.mean())
        std = float(gray.std())
    except Exception:
        return False

    if mean < float(min_mean):
        return False
    return std >= float(min_std)


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
