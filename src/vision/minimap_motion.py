from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, cast

import numpy as np


@dataclass
class MinimapMotionConfig:
    mode: str = "auto"  # auto | scroll | marker
    tile_px: int = 4
    min_response: float = 0.15
    max_shift_px: float = 32.0
    max_step_per_frame: int = 3
    invert_x: bool = False
    invert_y: bool = False
    marker_v_min: int = 220
    marker_s_max: int = 90
    marker_area_min: int = 3
    marker_area_max: int = 250
    marker_local_std_min: float = 5.0
    emit_threshold_tiles: float = 0.5
    deadband_tiles: float = 0.03
    smooth_n: int = 3

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

        def _str(name: str, default: str) -> str:
            try:
                v = (os.getenv(name, "") or "").strip()
                return v if v else str(default)
            except Exception:
                return str(default)

        def _clamp_int(v: int, lo: int, hi: int) -> int:
            try:
                return max(lo, min(hi, int(v)))
            except Exception:
                return max(lo, min(hi, int(lo)))

        mode_raw = _str("MINIMAP_MODE", "auto").strip().lower()
        if mode_raw not in {"auto", "scroll", "marker"}:
            mode_raw = "auto"

        cfg = MinimapMotionConfig(
            mode=mode_raw,
            tile_px=max(1, _int("MINIMAP_TILE_PX", 4)),
            min_response=max(0.0, _float("MINIMAP_PHASECORR_MIN_RESPONSE", 0.15)),
            max_shift_px=max(1.0, _float("MINIMAP_MAX_SHIFT_PX", 32.0)),
            max_step_per_frame=max(1, _int("MINIMAP_MAX_STEP_PER_FRAME", 3)),
            invert_x=_bool("MINIMAP_INVERT_X", False),
            invert_y=_bool("MINIMAP_INVERT_Y", False),
            marker_v_min=_clamp_int(_int("MINIMAP_MARKER_V_MIN", 220), 0, 255),
            marker_s_max=_clamp_int(_int("MINIMAP_MARKER_S_MAX", 90), 0, 255),
            marker_area_min=max(1, _int("MINIMAP_MARKER_AREA_MIN", 3)),
            marker_area_max=max(1, _int("MINIMAP_MARKER_AREA_MAX", 250)),
            marker_local_std_min=max(0.0, _float("MINIMAP_MARKER_LOCAL_STD_MIN", 5.0)),
            emit_threshold_tiles=max(0.10, min(1.00, _float("MINIMAP_EMIT_THRESHOLD_TILES", 0.50))),
            deadband_tiles=max(0.00, min(0.25, _float("MINIMAP_DEADBAND_TILES", 0.03))),
            smooth_n=max(1, _int("MINIMAP_SMOOTH_N", 3)),
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
        # Acumulador en tiles (float) para soportar scroll suave del minimapa.
        self._acc_dx: float = 0.0
        self._acc_dy: float = 0.0
        self._prev_marker: tuple[float, float] | None = None

        # Short smoothing for deltas (reduces jitter / cancellation when signal is intermittent).
        self._dx_hist: list[float] = []
        self._dy_hist: list[float] = []

        # Debug/observabilidad (no crítico)
        self.last_mode_used: str = ""
        self.last_response: float = 0.0
        self.last_shift_px: tuple[float, float] = (0.0, 0.0)
        self.last_marker_px: tuple[float, float] | None = None
        self.last_marker_dpx: tuple[float, float] = (0.0, 0.0)
        # Debug: último delta (en tiles fraccionales) que alimentó el acumulador.
        self.last_delta_tiles_f: tuple[float, float] = (0.0, 0.0)
        self.last_acc_tiles_f: tuple[float, float] = (0.0, 0.0)

    def reset(self) -> None:
        self._prev = None
        self._window = None
        self._acc_dx = 0.0
        self._acc_dy = 0.0
        self._prev_marker = None
        self._dx_hist = []
        self._dy_hist = []
        self.last_mode_used = ""
        self.last_response = 0.0
        self.last_shift_px = (0.0, 0.0)
        self.last_marker_px = None
        self.last_marker_dpx = (0.0, 0.0)
        self.last_delta_tiles_f = (0.0, 0.0)
        self.last_acc_tiles_f = (0.0, 0.0)

    @staticmethod
    def _emit_step_from_acc(acc: float, *, threshold_tiles: float) -> int:
        """Emite pasos enteros cuando el acumulador supera un umbral (en tiles)."""
        try:
            thr = float(threshold_tiles)
            if thr <= 0:
                thr = 0.5

            if acc >= thr:
                return int(np.floor(acc + 0.5))
            if acc <= -thr:
                return -int(np.floor(abs(acc) + 0.5))
        except Exception:
            return 0
        return 0

    def _apply_deadband(self, v_tiles_f: float) -> float:
        try:
            db = float(getattr(self.cfg, "deadband_tiles", 0.0) or 0.0)
        except Exception:
            db = 0.0
        try:
            if db > 0.0 and abs(float(v_tiles_f)) < db:
                return 0.0
        except Exception:
            return float(v_tiles_f)
        return float(v_tiles_f)

    def _smooth_delta(self, dx_tiles_f: float, dy_tiles_f: float) -> tuple[float, float]:
        """Smooth deltas using a tiny median window (keeps sign, kills spikes)."""
        try:
            n = int(getattr(self.cfg, "smooth_n", 1) or 1)
        except Exception:
            n = 1
        n = max(1, min(9, int(n)))
        if n <= 1:
            return float(dx_tiles_f), float(dy_tiles_f)

        try:
            self._dx_hist.append(float(dx_tiles_f))
            self._dy_hist.append(float(dy_tiles_f))
            if len(self._dx_hist) > n:
                self._dx_hist = self._dx_hist[-n:]
            if len(self._dy_hist) > n:
                self._dy_hist = self._dy_hist[-n:]

            dxs = sorted(self._dx_hist)
            dys = sorted(self._dy_hist)
            mid = len(dxs) // 2
            return float(dxs[mid]), float(dys[mid])
        except Exception:
            return float(dx_tiles_f), float(dy_tiles_f)

    @staticmethod
    def _to_gray_f32(img_bgr: np.ndarray) -> np.ndarray:
        import cv2

        if img_bgr.ndim == 2:
            gray = img_bgr
        else:
            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        return gray.astype(np.float32)

    def _detect_marker(self, minimap_bgr: np.ndarray) -> tuple[float, float] | None:
        """Detecta el marker del jugador en el minimapa (best-effort).

        Heurística simple: píxeles muy brillantes (V alto) y poca saturación (blanco).
        Escoge el blob más cercano al centro (o al marker previo si existe).
        """
        import cv2

        try:
            hsv = cv2.cvtColor(minimap_bgr, cv2.COLOR_BGR2HSV)
        except Exception:
            return None

        v_min = int(self.cfg.marker_v_min)
        s_max = int(self.cfg.marker_s_max)

        try:
            lower = np.array([0, 0, v_min], dtype=np.uint8)
            upper = np.array([180, s_max, 255], dtype=np.uint8)
            # OpenCV stubs type these params as cv2.Mat; runtime accepts numpy arrays.
            mask: Any = cv2.inRange(cast(Any, hsv), cast(Any, lower), cast(Any, upper))
        except Exception:
            return None

        try:
            mask = cv2.morphologyEx(cast(Any, mask), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
        except Exception:
            pass

        try:
            contours, _ = cv2.findContours(cast(Any, mask), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        except Exception:
            return None

        if not contours:
            return None

        h, w = int(minimap_bgr.shape[0]), int(minimap_bgr.shape[1])
        cx0 = float(w) / 2.0
        cy0 = float(h) / 2.0
        ref = self._prev_marker or (cx0, cy0)

        best: tuple[float, float] | None = None
        best_score = 1e18
        a_min = int(self.cfg.marker_area_min)
        a_max = int(self.cfg.marker_area_max)

        for cnt in contours:
            try:
                area = float(cv2.contourArea(cnt))
            except Exception:
                continue
            if area < float(a_min) or area > float(a_max):
                continue
            m = cv2.moments(cnt)
            if not m or float(m.get("m00", 0.0) or 0.0) <= 0.0:
                continue
            cx = float(m["m10"]) / float(m["m00"])
            cy = float(m["m01"]) / float(m["m00"])

            # Avoid static white UI dots: require some local texture in V channel.
            try:
                std_min = float(getattr(self.cfg, "marker_local_std_min", 0.0) or 0.0)
                if std_min > 0.0:
                    vx = int(round(cx))
                    vy = int(round(cy))
                    r = 4
                    x0 = max(0, vx - r)
                    y0 = max(0, vy - r)
                    x1 = min(int(w), vx + r + 1)
                    y1 = min(int(h), vy + r + 1)
                    patch_v = hsv[y0:y1, x0:x1, 2]
                    if patch_v is not None and getattr(patch_v, "size", 0) > 0:
                        if float(np.std(patch_v)) < std_min:
                            continue
            except Exception:
                pass
            dx = cx - float(ref[0])
            dy = cy - float(ref[1])
            score = (dx * dx + dy * dy) + (0.15 * abs(area - 20.0))
            if score < best_score:
                best_score = score
                best = (cx, cy)

        return best

    def update(self, minimap_bgr: np.ndarray) -> tuple[int, int, float] | None:
        import cv2

        if minimap_bgr is None or getattr(minimap_bgr, "size", 0) == 0:
            return None

        # Intento: modo marker (si está pedido) o auto (fallback si scroll no da señal).
        mode = str(self.cfg.mode or "auto").strip().lower()

        marker = None
        if mode in {"marker", "auto"}:
            try:
                marker = self._detect_marker(minimap_bgr)
            except Exception:
                marker = None

        self.last_marker_px = marker

        # Siempre calculamos gray para el modo scroll/auto.
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
            self._prev_marker = marker
            return None

        prev = self._prev
        self._prev = cur_w

        try:
            prev_pc = np.ascontiguousarray(prev, dtype=np.float32)
            cur_pc = np.ascontiguousarray(cur_w, dtype=np.float32)
            (shift_x, shift_y), response = cv2.phaseCorrelate(cast(Any, prev_pc), cast(Any, cur_pc))
        except Exception:
            return None

        try:
            resp = float(response)
        except Exception:
            resp = 0.0

        self.last_response = float(resp)
        self.last_shift_px = (float(shift_x), float(shift_y))

        # Modo scroll: usa phase shift.
        # Modo marker: usa marker.
        # Modo auto: usa marker si scroll no da señal, o si scroll se queda en (0,0)
        # pero el marker se mueve (caso típico: el minimapa no scrollea y solo se mueve el indicador).
        use_marker = False
        if mode == "marker":
            use_marker = True
        elif mode == "auto":
            min_resp = float(self.cfg.min_response)
            shift_mag = abs(float(shift_x)) + abs(float(shift_y))
            marker_mag = 0.0
            if marker is not None and self._prev_marker is not None:
                try:
                    mdx = float(marker[0] - float(self._prev_marker[0]))
                    mdy = float(marker[1] - float(self._prev_marker[1]))
                    marker_mag = abs(mdx) + abs(mdy)
                except Exception:
                    marker_mag = 0.0

            use_marker = bool(resp < min_resp) or (shift_mag < 0.20 and marker_mag >= 0.40)

        if use_marker and marker is not None and self._prev_marker is not None:
            dx_px = float(marker[0] - float(self._prev_marker[0]))
            dy_px = float(marker[1] - float(self._prev_marker[1]))
            self.last_marker_dpx = (dx_px, dy_px)
            self.last_mode_used = "marker"

            # Safety clamp en píxeles.
            if abs(dx_px) > float(self.cfg.max_shift_px) or abs(dy_px) > float(self.cfg.max_shift_px):
                self._prev_marker = marker
                return None

            tile_px = max(1, int(self.cfg.tile_px))
            dx_tiles_f = (dx_px) / float(tile_px)
            dy_tiles_f = (dy_px) / float(tile_px)

            if bool(self.cfg.invert_x):
                dx_tiles_f = -dx_tiles_f
            if bool(self.cfg.invert_y):
                dy_tiles_f = -dy_tiles_f

            dx_tiles_f = self._apply_deadband(dx_tiles_f)
            dy_tiles_f = self._apply_deadband(dy_tiles_f)

            dx_tiles_f, dy_tiles_f = self._smooth_delta(dx_tiles_f, dy_tiles_f)

            # Accumulate sub-tile motion
            self._acc_dx += float(dx_tiles_f)
            self._acc_dy += float(dy_tiles_f)

            self.last_delta_tiles_f = (float(dx_tiles_f), float(dy_tiles_f))
            self.last_acc_tiles_f = (float(self._acc_dx), float(self._acc_dy))

            self._prev_marker = marker

            # Usamos un pseudo-response para mantener la API (marker no tiene 'resp' real).
            resp_out = 1.0

            thr = float(getattr(self.cfg, "emit_threshold_tiles", 0.5) or 0.5)
            dx_tiles = self._emit_step_from_acc(self._acc_dx, threshold_tiles=thr)
            dy_tiles = self._emit_step_from_acc(self._acc_dy, threshold_tiles=thr)
            if dx_tiles == 0 and dy_tiles == 0:
                return None

            max_step = int(self.cfg.max_step_per_frame)
            if abs(dx_tiles) > max_step:
                dx_tiles = int(np.sign(dx_tiles)) * max_step
            if abs(dy_tiles) > max_step:
                dy_tiles = int(np.sign(dy_tiles)) * max_step

            self._acc_dx -= float(dx_tiles)
            self._acc_dy -= float(dy_tiles)

            if dx_tiles == 0 and dy_tiles == 0:
                return None
            return int(dx_tiles), int(dy_tiles), float(resp_out)

        # Si no usamos marker, seguimos con scroll.
        # Igual dejamos disponible el delta del marker (si se pudo detectar) para debug.
        if marker is not None and self._prev_marker is not None:
            try:
                self.last_marker_dpx = (float(marker[0] - float(self._prev_marker[0])), float(marker[1] - float(self._prev_marker[1])))
            except Exception:
                self.last_marker_dpx = (0.0, 0.0)
        else:
            self.last_marker_dpx = (0.0, 0.0)

        self._prev_marker = marker
        self.last_mode_used = "scroll"

        if resp < float(self.cfg.min_response):
            return None

        dx_px = float(shift_x)
        dy_px = float(shift_y)

        # Safety clamps.
        if abs(dx_px) > float(self.cfg.max_shift_px) or abs(dy_px) > float(self.cfg.max_shift_px):
            return None

        tile_px = max(1, int(self.cfg.tile_px))

        # Map shift to player movement (opposite direction) as *fractional tiles*.
        dx_tiles_f = (-dx_px) / float(tile_px)
        dy_tiles_f = (-dy_px) / float(tile_px)

        if bool(self.cfg.invert_x):
            dx_tiles_f = -dx_tiles_f
        if bool(self.cfg.invert_y):
            dy_tiles_f = -dy_tiles_f

        # Accumulate sub-tile movement (helps when minimap scrolls smoothly).
        try:
            dx_tiles_f = self._apply_deadband(dx_tiles_f)
            dy_tiles_f = self._apply_deadband(dy_tiles_f)

            dx_tiles_f, dy_tiles_f = self._smooth_delta(dx_tiles_f, dy_tiles_f)

            self._acc_dx += float(dx_tiles_f)
            self._acc_dy += float(dy_tiles_f)
            self.last_delta_tiles_f = (float(dx_tiles_f), float(dy_tiles_f))
            self.last_acc_tiles_f = (float(self._acc_dx), float(self._acc_dy))
        except Exception:
            return None

        thr = float(getattr(self.cfg, "emit_threshold_tiles", 0.5) or 0.5)
        dx_tiles = self._emit_step_from_acc(self._acc_dx, threshold_tiles=thr)
        dy_tiles = self._emit_step_from_acc(self._acc_dy, threshold_tiles=thr)

        if dx_tiles == 0 and dy_tiles == 0:
            return None

        max_step = int(self.cfg.max_step_per_frame)
        if abs(dx_tiles) > max_step:
            dx_tiles = int(np.sign(dx_tiles)) * max_step
        if abs(dy_tiles) > max_step:
            dy_tiles = int(np.sign(dy_tiles)) * max_step

        # Consume emitted steps from accumulator.
        try:
            self._acc_dx -= float(dx_tiles)
            self._acc_dy -= float(dy_tiles)
        except Exception:
            pass

        # Safety: ignore if clamped to 0.
        if dx_tiles == 0 and dy_tiles == 0:
            return None

        return int(dx_tiles), int(dy_tiles), resp
