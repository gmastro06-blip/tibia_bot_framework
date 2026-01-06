from __future__ import annotations

from typing import Optional, Dict, List, Tuple

import cv2
import d3dshot
import win32gui
import time
import psutil
import nvidia_smi  # poetry add nvidia-ml-py3
import numpy as np


class DXGICapture:
    def __init__(self, window_title_contains: str = "Tibia -"):
        # API correcta de d3dshot
        self.d = d3dshot.create(capture_output="numpy")

        self.hwnd, self.window_title = self._find_hwnd_by_title_contains(window_title_contains)

        # client rect (left, top, right, bottom) relativo a la ventana
        self.rect = win32gui.GetClientRect(self.hwnd)
        # esquina superior izq del cliente en coordenadas de pantalla
        self.offset = win32gui.ClientToScreen(self.hwnd, (0, 0))

        self.fps = 0.0
        self.latency_ms = 0.0
        self.dropped = 0

        # GPU metrics (puede fallar si no hay NVIDIA/NVML)
        try:
            nvidia_smi.nvmlInit()
            self.gpu_handle = nvidia_smi.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            self.gpu_handle = None

    @staticmethod
    def _find_hwnd_by_title_contains(needle: str) -> Tuple[int, str]:
        wanted = needle.lower().strip()
        matches: List[Tuple[int, str]] = []

        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if title and wanted in title.lower():
                matches.append((hwnd, title))

        win32gui.EnumWindows(cb, None)

        if not matches:
            raise ValueError(f"No se encontró ninguna ventana que contenga: {needle!r}")

        # 1) Preferir la ventana activa si coincide
        fg = win32gui.GetForegroundWindow()
        for hwnd, title in matches:
            if hwnd == fg:
                return hwnd, title

        # 2) Si no, preferir títulos que EMPIECEN por el patrón, y más cortos primero
        def score(item: Tuple[int, str]):
            _hwnd, title = item
            t = title.lower()
            starts = t.startswith(wanted)
            return (0 if starts else 1, len(title))

        matches.sort(key=score)
        return matches[0][0], matches[0][1]

    def capture(self) -> Optional[cv2.Mat]:
        start = time.time()

        left = self.offset[0]
        top = self.offset[1]
        right = left + self.rect[2]
        bottom = top + self.rect[3]

        frame = self.d.screenshot(region=(left, top, right, bottom))
        if frame is None:
            self.dropped += 1
            return None

        self.latency_ms = (time.time() - start) * 1000
        self.fps = 1000.0 / self.latency_ms if self.latency_ms > 0 else 0.0

        # frame normalmente viene como numpy RGB (H,W,3)
        if isinstance(frame, np.ndarray):
            if frame.ndim == 3 and frame.shape[2] == 4:
                return cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)
            return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        # fallback (por si alguna versión devuelve PIL Image)
        arr = np.array(frame)
        if arr.ndim == 3 and arr.shape[2] == 4:
            return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

    def benchmark(self, duration: int = 60) -> Dict:
        start = time.time()
        frames = 0
        while time.time() - start < duration:
            _ = self.capture()
            frames += 1

        cpu = psutil.cpu_percent()
        gpu = None
        if self.gpu_handle is not None:
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
