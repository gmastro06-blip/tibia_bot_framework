from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple

import time
import numpy as np
import cv2

import d3dshot
import win32gui
import win32con
import win32api
import psutil

try:
    import nvidia_smi  # pip/poetry: nvidia-ml-py3
except Exception:
    nvidia_smi = None


@dataclass
class _WindowPick:
    hwnd: int
    title: str


class DXGICapture:
    """
    Captura el CLIENT AREA de una ventana cuyo título CONTENGA un substring (case-insensitive).
    - Usa d3dshot en modo captura continua: capture() + get_latest_frame().
    - Maneja timeouts/frames vacíos devolviendo None (para que el caller los ignore).
    """

    def __init__(
        self,
        window_title_contains: str,
        target_fps: int = 60,
        prefer_foreground: bool = True,
    ):
        self.window_title_contains = window_title_contains
        self.target_fps = int(target_fps)
        self.prefer_foreground = bool(prefer_foreground)

        # d3dshot: salida numpy para evitar conversiones extra
        self.d = d3dshot.create(capture_output="numpy")

        self.hwnd: int = 0
        self.window_title: str = ""

        self.monitor_rect: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self.client_rect_screen: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self.region_display: Tuple[int, int, int, int] = (0, 0, 0, 0)

        self.fps: float = 0.0
        self.latency_ms: float = 0.0
        self.dropped: int = 0

        self._last_ok_frame: Optional[np.ndarray] = None
        self._capturing_started = False
        self._display_index: Optional[int] = None

        # GPU metrics (opcional)
        self.gpu_handle = None
        if nvidia_smi is not None:
            try:
                nvidia_smi.nvmlInit()
                self.gpu_handle = nvidia_smi.nvmlDeviceGetHandleByIndex(0)
            except Exception:
                self.gpu_handle = None

        self.refresh_window(restart_capture=True)

    # -------------------- window selection --------------------

    @staticmethod
    def _enum_windows_title_contains(needle: str) -> List[_WindowPick]:
        needle = (needle or "").strip().lower()
        matches: List[_WindowPick] = []

        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
            if not title:
                return
            if needle in title.lower():
                matches.append(_WindowPick(hwnd=hwnd, title=title))

        win32gui.EnumWindows(cb, None)
        return matches

    def _pick_window(self) -> _WindowPick:
        matches = self._enum_windows_title_contains(self.window_title_contains)
        if not matches:
            raise ValueError(f"No se encontró ninguna ventana cuyo título contenga: {self.window_title_contains!r}")

        if self.prefer_foreground:
            fg = win32gui.GetForegroundWindow()
            for m in matches:
                if m.hwnd == fg:
                    return m

        # fallback: título más corto primero (suele ser el principal)
        matches.sort(key=lambda m: len(m.title))
        return matches[0]

    @staticmethod
    def _get_client_rect_in_screen(hwnd: int) -> Tuple[int, int, int, int]:
        # Client rect (0,0,w,h) -> convertir a coords pantalla
        l, t, r, b = win32gui.GetClientRect(hwnd)
        x0, y0 = win32gui.ClientToScreen(hwnd, (l, t))
        x1, y1 = win32gui.ClientToScreen(hwnd, (r, b))
        return (x0, y0, x1, y1)

    @staticmethod
    def _get_monitor_rect_for_hwnd(hwnd: int) -> Tuple[int, int, int, int]:
        mon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        info = win32api.GetMonitorInfo(mon)
        # "Monitor": (left, top, right, bottom) en coords del escritorio virtual
        return tuple(info["Monitor"])

    # -------------------- d3dshot wiring --------------------

    def _guess_display_index_by_resolution(self, mon_rect: Tuple[int, int, int, int]) -> List[int]:
        mw = int(mon_rect[2] - mon_rect[0])
        mh = int(mon_rect[3] - mon_rect[1])

        candidates: List[int] = []
        for i, disp in enumerate(self.d.displays):
            # d3dshot displays suelen tener .resolution (w,h)
            res = getattr(disp, "resolution", None)
            if not res or len(res) != 2:
                continue
            dw, dh = int(res[0]), int(res[1])
            if dw == mw and dh == mh:
                candidates.append(i)

        # si no hay match por resolución, probar todos
        if not candidates:
            candidates = list(range(len(self.d.displays)))
        return candidates

    def _compute_region_for_display(self, client_rect_screen: Tuple[int, int, int, int], mon_rect: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        # convertir coords de pantalla (escritorio virtual) -> coords relativas al monitor
        left = int(client_rect_screen[0] - mon_rect[0])
        top = int(client_rect_screen[1] - mon_rect[1])
        right = int(client_rect_screen[2] - mon_rect[0])
        bottom = int(client_rect_screen[3] - mon_rect[1])
        return (left, top, right, bottom)

    def _start_capture(self) -> None:
        # detener captura previa si existiera
        try:
            if self._capturing_started:
                self.d.stop()
        except Exception:
            pass

        candidates = self._guess_display_index_by_resolution(self.monitor_rect)
        region = self._compute_region_for_display(self.client_rect_screen, self.monitor_rect)

        # intentar displays candidatos hasta que salga un frame válido
        last_err: Optional[Exception] = None
        for idx in candidates:
            try:
                self.d.display = self.d.displays[idx]
                self._display_index = idx
                self.region_display = region

                # captura continua
                self.d.capture(target_fps=self.target_fps, region=self.region_display)
                self._capturing_started = True

                # esperar un poquito a que haya buffer
                time.sleep(0.05)

                frame = self.d.get_latest_frame()
                if self._is_valid_frame(frame):
                    self._last_ok_frame = self._to_bgr(frame)
                    return

                # si no hay frame aún, damos un poco más de margen
                time.sleep(0.10)
                frame = self.d.get_latest_frame()
                if self._is_valid_frame(frame):
                    self._last_ok_frame = self._to_bgr(frame)
                    return

                # no funcionó: parar y probar otro display
                self.d.stop()
                self._capturing_started = False
            except Exception as e:
                last_err = e
                try:
                    self.d.stop()
                except Exception:
                    pass
                self._capturing_started = False

        raise RuntimeError(f"No pude iniciar captura estable (display/region). Último error: {last_err!r}")

    @staticmethod
    def _is_valid_frame(frame) -> bool:
        if frame is None:
            return False
        if isinstance(frame, np.ndarray):
            return frame.size > 0 and frame.ndim in (2, 3)
        # fallback (PIL, etc.)
        try:
            arr = np.array(frame)
            return arr.size > 0
        except Exception:
            return False

    @staticmethod
    def _to_bgr(frame) -> np.ndarray:
        if not isinstance(frame, np.ndarray):
            frame = np.array(frame)

        if frame.ndim == 2:
            return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

        if frame.ndim == 3 and frame.shape[2] == 4:
            return cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)

        # asumimos RGB
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    # -------------------- public API --------------------

    def refresh_window(self, restart_capture: bool = False) -> None:
        pick = self._pick_window()
        self.hwnd = pick.hwnd
        self.window_title = pick.title

        # recalcular geometría
        self.client_rect_screen = self._get_client_rect_in_screen(self.hwnd)
        self.monitor_rect = self._get_monitor_rect_for_hwnd(self.hwnd)

        if restart_capture:
            self._start_capture()

    def capture(self) -> Optional[np.ndarray]:
        """
        Devuelve frame BGR (np.ndarray) o None si no hay frame válido.
        Nunca lanza cv2.cvtColor sobre un frame vacío.
        """
        start = time.time()

        # Intentar frame nuevo (o último) del buffer interno
        frame = None
        try:
            frame = self.d.get_latest_frame()
        except Exception:
            frame = None

        if not self._is_valid_frame(frame):
            self.dropped += 1
            # devolvemos el último frame bueno si existe (opcional)
            return self._last_ok_frame

        bgr = self._to_bgr(frame)
        if bgr is None or bgr.size == 0:
            self.dropped += 1
            return self._last_ok_frame

        self._last_ok_frame = bgr

        self.latency_ms = (time.time() - start) * 1000.0
        self.fps = 1000.0 / self.latency_ms if self.latency_ms > 0 else 0.0
        return bgr

    def stop(self) -> None:
        try:
            self.d.stop()
        finally:
            self._capturing_started = False

    def benchmark(self, duration: int = 10) -> Dict:
        t0 = time.time()
        frames = 0
        while time.time() - t0 < duration:
            _ = self.capture()
            frames += 1
            time.sleep(0.0)

        cpu = psutil.cpu_percent()
        gpu = None
        if nvidia_smi is not None and self.gpu_handle is not None:
            try:
                gpu_info = nvidia_smi.nvmlDeviceGetUtilizationRates(self.gpu_handle)
                gpu = gpu_info.gpu
            except Exception:
                gpu = None

        return {
            "window_title": self.window_title,
            "display_index": self._display_index,
            "fps": self.fps,
            "latency_ms": self.latency_ms,
            "dropped": self.dropped,
            "cpu": cpu,
            "gpu": gpu,
        }
