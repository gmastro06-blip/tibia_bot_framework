from typing import Tuple, Optional, Dict
import d3dshot
import cv2
import win32gui
import time
import psutil  # Para uso CPU
import nvidia_smi  # Para GPU, pip install nvidia-ml-py3

class DXGICapture:
    def __init__(self, window_title: str):
        self.d = d3dshot.create_capture()
        self.hwnd = win32gui.FindWindow(None, window_title)
        if not self.hwnd:
            raise ValueError("Ventana no encontrada")
        self.rect = win32gui.GetClientRect(self.hwnd)
        self.offset = win32gui.ClientToScreen(self.hwnd, (0, 0))
        self.fps = 0.0
        self.latency_ms = 0.0
        self.dropped = 0
        nvidia_smi.nvmlInit()
        self.gpu_handle = nvidia_smi.nvmlDeviceGetHandleByIndex(0)

    def capture(self) -> Optional[cv2.Mat]:
        start = time.time()
        frame = self.d.screenshot(region=(self.offset[0], self.offset[1], self.offset[0] + self.rect[2], self.offset[1] + self.rect[3]))
        if frame is None:
            self.dropped += 1
            return None
        self.latency_ms = (time.time() - start) * 1000
        self.fps = 1 / (self.latency_ms / 1000) if self.latency_ms > 0 else 0
        return cv2.cvtColor(np.array(frame), cv2.COLOR_RGBA2BGR)

    def benchmark(self, duration: int = 60) -> Dict:
        start = time.time()
        frames = 0
        while time.time() - start < duration:
            self.capture()
            frames += 1
        cpu = psutil.cpu_percent()
        gpu_info = nvidia_smi.nvmlDeviceGetUtilizationRates(self.gpu_handle)
        gpu = gpu_info.gpu
        return {"fps": self.fps, "latency_ms": self.latency_ms, "dropped": self.dropped, "cpu": cpu, "gpu": gpu}