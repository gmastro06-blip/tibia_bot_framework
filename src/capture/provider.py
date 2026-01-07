from typing import Optional, Tuple, Dict
import time
import cv2
import d3dshot  # pip install d3dshot for DXGI
import mss
import numpy as np
import win32gui
import obsws_python as obs  # Fallback OBS


class CaptureProvider:
    def __init__(self, window_title: str = "Tibia Clone", resolution: Tuple[int, int] = (1920, 1080)):
        self.window_title = window_title
        self.resolution = resolution
        self.mode = self._init_mode()
        self.fps = 0.0
        self.latency_ms = 0.0
        self.cpu_usage = 0.0  # Placeholder, use psutil for real
        self.gpu_usage = 0.0
        self.dropped_frames = 0

    def _init_mode(self) -> str:
        try:
            self.d3d = d3dshot.create_capture()
            self.hwnd = win32gui.FindWindow(None, self.window_title)
            if self.hwnd:
                return "dxgi"
        except Exception:
            pass
        try:
            self.sct = mss.mss()
            return "mss"
        except Exception:
            pass
        try:
            self.obs_cl = obs.ReqClient(host='localhost', port=4455)
            return "obs"
        except Exception:
            raise ValueError("No capture mode available")

    def capture(self) -> Optional[np.ndarray]:
        start = time.time()
        frame = None
        if self.mode == "dxgi":
            rect = win32gui.GetClientRect(self.hwnd)
            offset = win32gui.ClientToScreen(self.hwnd, (0, 0))
            region = (offset[0], offset[1], offset[0] + rect[2], offset[1] + rect[3])
            frame = self.d3d.screenshot(region=region)
            if frame is None:
                self.dropped_frames += 1
        elif self.mode == "mss":
            monitor = {"top": 0, "left": 0, "width": self.resolution[0], "height": self.resolution[1]}
            sct_img = self.sct.grab(monitor)
            frame = np.array(sct_img)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        elif self.mode == "obs":
            cap = cv2.VideoCapture(0)  # Virtual cam index
            ret, frame = cap.read()
            cap.release()
            if not ret:
                self.dropped_frames += 1
        self.latency_ms = (time.time() - start) * 1000
        self.fps = 1 / max(self.latency_ms / 1000, 1e-6)
        return frame

    def benchmark(self, duration: int = 60) -> Dict:
        start = time.time()
        frames = 0
        while time.time() - start < duration:
            self.capture()
            frames += 1
        return {
            "fps": self.fps,
            "latency_ms": self.latency_ms,
            "dropped": self.dropped_frames,
            "cpu": self.cpu_usage,
            "gpu": self.gpu_usage
        }
