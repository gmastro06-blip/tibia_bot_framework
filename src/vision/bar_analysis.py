from __future__ import annotations

from typing import Optional, Tuple, Any, cast

import cv2
import numpy as np


def _clamp_roi(frame: np.ndarray, roi: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    x, y, w, h = roi
    frame_h, frame_w = frame.shape[0], frame.shape[1]
    x = max(0, min(int(x), frame_w - 1))
    y = max(0, min(int(y), frame_h - 1))
    w = max(1, min(int(w), frame_w - x))
    h = max(1, min(int(h), frame_h - y))
    return x, y, w, h


def estimate_bar_fill_ratio(frame_bgr: np.ndarray, roi: Tuple[int, int, int, int], kind: str) -> Optional[float]:
    """Estima el fill ratio (0..1) de una barra HP/MP dentro del ROI.

    kind:
      - "hp" (rojo)
      - "mp" (azul)
    """
    x, y, w, h = _clamp_roi(frame_bgr, roi)
    crop = frame_bgr[y : y + h, x : x + w]
    if crop.size == 0:
        return None

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)

    # OpenCV stubs are overly strict about argument types (Mat vs ndarray).
    hsv_mat = cast(Any, hsv)

    if kind.lower() == "hp":
        # Rojo: doble rango por wrap de Hue.
        low1 = cast(Any, np.array([0, 70, 50], dtype=np.uint8))
        high1 = cast(Any, np.array([10, 255, 255], dtype=np.uint8))
        low2 = cast(Any, np.array([170, 70, 50], dtype=np.uint8))
        high2 = cast(Any, np.array([180, 255, 255], dtype=np.uint8))
        mask_low = cv2.inRange(hsv_mat, low1, high1)
        mask_high = cv2.inRange(hsv_mat, low2, high2)
        mask = cv2.bitwise_or(mask_low, mask_high)
    elif kind.lower() == "mp":
        # Azul típico (puede ajustarse si tu UI usa otro tono)
        low = cast(Any, np.array([90, 70, 50], dtype=np.uint8))
        high = cast(Any, np.array([135, 255, 255], dtype=np.uint8))
        mask = cv2.inRange(hsv_mat, low, high)
    else:
        return None

    # Column-wise: buscar hasta dónde llega la zona "rellena".
    col_frac = (mask > 0).mean(axis=0)  # shape: (w,)
    filled_cols = np.where(col_frac > 0.15)[0]
    if filled_cols.size == 0:
        return 0.0

    last = int(filled_cols.max())
    ratio = (last + 1) / float(w)
    ratio = max(0.0, min(1.0, ratio))
    return ratio
