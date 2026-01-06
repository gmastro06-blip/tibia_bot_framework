from __future__ import annotations

from typing import Optional, Dict, List, Tuple
import time

import cv2
import numpy as np
import win32gui

import d3dshot
import psutil

try:
    import nvidia_smi  # poetry add nvidia-ml-py3
except Exception:
    nvidia_smi = None


class DXGICapture:
    """
    Captura por DXGI (via d3dshot) recortando el área cliente de una ventana.

    - Busca una ventana visible cuyo título CONTENGA `window_title_contains`
      (ej: "Tibia" o "Tibia -").
    - Soporta que el nombre del player cambie (no requiere match exacto).
    - Si no hay frame válido (timeout / minimizada / etc) devuelve None.
    """

    def __init__(self, window_title_contains: str = "Tibia", target_fps: int = 60):
        self.window_title_contains = window_title_contains

        self.d = d3dshot.create(capture_output="numpy")
        # Captura continua del display (más estable que screenshot puntual)
        try:
            self.d.capture(target_fps=target_fps)
        except Exception:
            # si falla, igual podemos intentar screenshot más adelante
            pass

        self.hwnd, self.window_title = self._find_hwnd_by_title_contains(window_title_contains)
        self._refresh_geometry()

        self.fps = 0.0
        self.latency_ms = 0.0
        self.dropped = 0
        self._last_ok: Optional[np.ndarray] = None

        self.gpu_handle = None
        if nvidia_smi is not None:
            try:
                nvidia_smi.nvmlInit()
                self.gpu_handle = nvidia_smi.nvmlDeviceGetHandleByIndex(0)
            except Exception:
                self.gpu_handle = None

    @staticmethod
    def _find_hwnd_by_title_contains(needle: str) -> Tuple[int, str]:
        wanted = (needle or "").lower().strip()
        matches: List[Tuple[int, str]] = []

        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return
            if wanted in title.lower():
                matches.append((hwnd, title))

        win32gui.EnumWindows(cb, None)

        if not matches:
            raise ValueError(f"No se encontró ninguna ventana que contenga: {needle!r}")

        # Preferir la ventana en foreground si coincide
        fg = win32gui.GetForegroundWindow()
        for hwnd, title in matches:
            if hwnd == fg:
                return hwnd, title

        # Si no, preferir títulos que empiezan por el patrón y más cortos
        def score(item: Tuple[int, str]):
            _hwnd, title = item
            t = title.lower()
            starts = t.startswith(wanted)
            return (0 if starts else 1, len(title))

        matches.sort(key=score)
        return matches[0][0], matches[0][1]

    def _refresh_geometry(self) -> None:
        # client rect relativo a ventana
        left, top, right, bottom = win32gui.GetClientRect(self.hwnd)
        self.client_w = right - left
        self.client_h = bottom - top
        # esquina cliente en coords de pantalla
        self.client_origin = win32gui.ClientToScreen(self.hwnd, (0, 0))

    @staticmethod
    def _is_valid_frame(arr: Optional[np.ndarray]) -> bool:
        if arr is None:
            return False
        if not isinstance(arr, np.ndarray):
            return False
        if arr.size == 0:
            return False
        if arr.ndim != 3:
            return False
        if arr.shape[2] not in (3, 4):
            return False
        return True

    def capture(self) -> Optional[cv2.Mat]:
        start = time.time()

        # Si la ventana se cerró, re-buscar
        if not win32gui.IsWindow(self.hwnd):
            self.hwnd, self.window_title = self._find_hwnd_by_title_contains(self.window_title_contains)
            self._refresh_geometry()

        # actualizar geometría (por si moviste/resize)
        self._refresh_geometry()

        x0, y0 = self.client_origin
        x1 = x0 + self.client_w
        y1 = y0 + self.client_h

        frame = None
        try:
            # preferir stream continuo
            if hasattr(self.d, "get_latest_frame"):
                frame = self.d.get_latest_frame()
            if frame is None:
                # fallback screenshot
                frame = self.d.screenshot(region=(x0, y0, x1, y1))
            else:
                # si vino full-screen, recortamos
                h, w = frame.shape[:2]
                # recorte con clamp
                cx0 = max(0, min(w, x0))
                cy0 = max(0, min(h, y0))
                cx1 = max(0, min(w, x1))
                cy1 = max(0, min(h, y1))
                frame = frame[cy0:cy1, cx0:cx1]
        except Exception:
            frame = None

        self.latency_ms = (time.time() - start) * 1000.0
        self.fps = (1000.0 / self.latency_ms) if self.latency_ms > 0 else 0.0

        if not self._is_valid_frame(frame):
            self.dropped += 1
            return None

        self._last_ok = frame

        # d3dshot suele dar RGB/RGBA -> OpenCV usa BGR/BGRA
        if frame.shape[2] == 4:
            return cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    def benchmark(self, duration: int = 10) -> Dict:
        start = time.time()
        frames = 0
        while time.time() - start < duration:
            _ = self.capture()
            frames += 1

        cpu = psutil.cpu_percent()
        gpu = None
        if self.gpu_handle is not None and nvidia_smi is not None:
            try:
                gpu_info = nvidia_smi.nvmlDeviceGetUtilizationRates(self.gpu_handle)
                gpu = gpu_info.gpu
            except Exception:
                gpu = None

        return {
            "window_title": self.window_title,
            "fps": self.fps,
            "latency_ms": self.latency_ms,
            "dropped": self.dropped,
            "cpu": cpu,
            "gpu": gpu,
        }
