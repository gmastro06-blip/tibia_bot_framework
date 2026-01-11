from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np


@dataclass
class MinimapMotionConfig:
    tile_px: int = 4
    min_response: float = 0.15
    max_shift_px: float = 32.0
    max_step_per_frame: int = 3
    invert_x: bool = False
    invert_y: bool = False

    @staticmethod
    def from_env() -> "MinimapMotionConfig":
        def _int(name: str, default: int) -> int:
            try:
                return int(float(os.getenv(name, str(default)).strip() or str(default)))
            except Exception:
                return int(default)

        def _float(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, str(default)).strip() or str(default))
            except Exception:
                return float(default)

        def _bool(name: str, default: bool = False) -> bool:
            raw = os.getenv(name, "").strip().lower()
            if not raw:
                return bool(default)
            return raw in {"1", "true", "yes", "on"}

        cfg = MinimapMotionConfig(
            tile_px=max(1, _int("MINIMAP_TILE_PX", 4)),
            min_response=max(0.0, _float("MINIMAP_PHASECORR_MIN_RESPONSE", 0.15)),
            max_shift_px=max(1.0, _float("MINIMAP_MAX_SHIFT_PX", 32.0)),
            max_step_per_frame=max(1, _int("MINIMAP_MAX_STEP_PER_FRAME", 3)),
            invert_x=_bool("MINIMAP_INVERT_X", False),
            invert_y=_bool("MINIMAP_INVERT_Y", False),
        )
        return cfg


class MinimapMotionTracker:
    """Estima movimiento midiendo el desplazamiento del minimapa.

    Es una alternativa *best-effort* cuando no hay coordenadas visibles en pantalla.

    Idea:
    - Si el jugador se mueve, los tiles del minimapa se desplazan en dirección opuesta.
    - Estimamos el shift frame-a-frame usando phase correlation.

    Reglas de seguridad:
    - Solo emitimos pasos si `response >= min_response`.
    - Limitamos shift máximo en píxeles y pasos máximos por frame.

    La salida está en *pasos de tile* (dx, dy) donde típicamente:
    - dx > 0 significa "el jugador se movió al este" (x aumenta)
    - dy > 0 significa "el jugador se movió al sur" (y aumenta)

    Para obtener coords absolutas, igual necesitás una seed (x,y[,z]) vía env/archivo.
    """

    def __init__(self, cfg: MinimapMotionConfig | None = None) -> None:
        self.cfg = cfg or MinimapMotionConfig.from_env()
        self._prev: np.ndarray | None = None
        self._window: np.ndarray | None = None

    def reset(self) -> None:
        self._prev = None
        self._window = None

    @staticmethod
    def _to_gray_f32(img_bgr: np.ndarray) -> np.ndarray:
        import cv2

        if img_bgr.ndim == 2:
            gray = img_bgr
        else:
            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        return gray.astype(np.float32)

    def update(self, minimap_bgr: np.ndarray) -> tuple[int, int, float] | None:
        import cv2

        if minimap_bgr is None or getattr(minimap_bgr, "size", 0) == 0:
            return None

        cur = self._to_gray_f32(minimap_bgr)

        # Hanning window reduces edge artifacts for phase correlation.
        try:
            if self._window is None or self._window.shape != cur.shape:
                h, w = int(cur.shape[0]), int(cur.shape[1])
                wy = np.hanning(h).astype(np.float32)
                wx = np.hanning(w).astype(np.float32)
                self._window = (wy[:, None] * wx[None, :]).astype(np.float32)
        except Exception:
            self._window = None

        if self._window is not None:
            try:
                cur_w = cur * self._window
            except Exception:
                cur_w = cur
        else:
            cur_w = cur

        if self._prev is None:
            self._prev = cur_w
            return None

        prev = self._prev
        self._prev = cur_w

        try:
            (shift_x, shift_y), response = cv2.phaseCorrelate(prev, cur_w)
        except Exception:
            return None

        try:
            resp = float(response)
        except Exception:
            resp = 0.0

        if resp < float(self.cfg.min_response):
            return None

        dx_px = float(shift_x)
        dy_px = float(shift_y)

        # Safety clamps.
        if abs(dx_px) > float(self.cfg.max_shift_px) or abs(dy_px) > float(self.cfg.max_shift_px):
            return None

        tile_px = max(1, int(self.cfg.tile_px))

        # Map shift to player movement (opposite direction).
        dx_tiles = int(round((-dx_px) / float(tile_px)))
        dy_tiles = int(round((-dy_px) / float(tile_px)))

        if bool(self.cfg.invert_x):
            dx_tiles = -dx_tiles
        if bool(self.cfg.invert_y):
            dy_tiles = -dy_tiles

        if abs(dx_tiles) > int(self.cfg.max_step_per_frame) or abs(dy_tiles) > int(self.cfg.max_step_per_frame):
            return None

        if dx_tiles == 0 and dy_tiles == 0:
            return None

        return dx_tiles, dy_tiles, resp
