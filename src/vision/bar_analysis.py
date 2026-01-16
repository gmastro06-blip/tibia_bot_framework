from __future__ import annotations

from typing import Optional, Tuple, Any, cast
import os

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
        # No reliable bar color detected.
        return None

    last = int(filled_cols.max())
    ratio = (last + 1) / float(w)
    ratio = max(0.0, min(1.0, ratio))
    return ratio


def estimate_bar_fill_ratio_with_reason(
    frame_bgr: np.ndarray,
    roi: Tuple[int, int, int, int],
    kind: str,
) -> tuple[Optional[float], str]:
    """Like `estimate_bar_fill_ratio`, but returns a stable reason string.

    Reasons are intended for telemetry/debug, not for user-facing UX.
    """

    x, y, w, h = _clamp_roi(frame_bgr, roi)
    if w < 2 or h < 2:
        return None, "invalid_roi"
    crop = frame_bgr[y : y + h, x : x + w]
    if crop is None or not isinstance(crop, np.ndarray) or crop.size == 0:
        return None, "empty_crop"

    try:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    except Exception:
        return None, "no_cv2"

    hsv_mat = cast(Any, hsv)

    # Tunables: allow overriding HSV thresholds without code changes.
    # Defaults match common Tibia HUD red/blue.
    k = str(kind or "").strip().lower()
    try:
        thr = float(os.getenv("BAR_COL_FRAC_THR", "0.15").strip() or "0.15")
    except Exception:
        thr = 0.15
    thr = float(max(0.01, min(0.80, thr)))

    try:
        min_presence = float(os.getenv("BAR_MIN_PRESENCE", "0.02").strip() or "0.02")
    except Exception:
        min_presence = 0.02
    min_presence = float(max(0.0, min(0.5, min_presence)))

    def _env_int(name: str, default: int) -> int:
        try:
            return int(float(os.getenv(name, str(default)).strip() or str(default)))
        except Exception:
            return int(default)

    if k == "hp":
        low1 = cast(Any, np.array([
            _env_int("HP_BAR_H1_LOW", 0),
            _env_int("HP_BAR_S_LOW", 70),
            _env_int("HP_BAR_V_LOW", 50),
        ], dtype=np.uint8))
        high1 = cast(Any, np.array([
            _env_int("HP_BAR_H1_HIGH", 10),
            _env_int("HP_BAR_S_HIGH", 255),
            _env_int("HP_BAR_V_HIGH", 255),
        ], dtype=np.uint8))
        low2 = cast(Any, np.array([
            _env_int("HP_BAR_H2_LOW", 170),
            _env_int("HP_BAR_S_LOW", 70),
            _env_int("HP_BAR_V_LOW", 50),
        ], dtype=np.uint8))
        high2 = cast(Any, np.array([
            _env_int("HP_BAR_H2_HIGH", 180),
            _env_int("HP_BAR_S_HIGH", 255),
            _env_int("HP_BAR_V_HIGH", 255),
        ], dtype=np.uint8))
        mask_low = cv2.inRange(hsv_mat, low1, high1)
        mask_high = cv2.inRange(hsv_mat, low2, high2)
        mask = cv2.bitwise_or(mask_low, mask_high)
    elif k == "mp":
        low = cast(Any, np.array([
            _env_int("MP_BAR_H_LOW", 90),
            _env_int("MP_BAR_S_LOW", 70),
            _env_int("MP_BAR_V_LOW", 50),
        ], dtype=np.uint8))
        high = cast(Any, np.array([
            _env_int("MP_BAR_H_HIGH", 135),
            _env_int("MP_BAR_S_HIGH", 255),
            _env_int("MP_BAR_V_HIGH", 255),
        ], dtype=np.uint8))
        mask = cv2.inRange(hsv_mat, low, high)
    else:
        return None, "unknown_kind"

    try:
        presence = float(np.count_nonzero(mask)) / float(mask.size)
    except Exception:
        presence = 0.0

    if presence < float(min_presence):
        return None, "no_bar_color"

    col_frac = (mask > 0).mean(axis=0)
    filled_cols = np.where(col_frac > float(thr))[0]
    if filled_cols.size == 0:
        return None, "no_filled_cols"

    last = int(filled_cols.max())
    ratio = (last + 1) / float(max(1, w))
    ratio = float(max(0.0, min(1.0, ratio)))

    # If the bar is basically always full, it might be ROI drifting into a blue/red panel.
    # Keep the raw ratio but mark a diagnostic reason.
    if ratio <= 0.02:
        return ratio, "ok_near_zero"
    if ratio >= 0.98:
        return ratio, "ok_near_one"
    return ratio, "ok"
