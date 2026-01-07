from typing import Tuple, Optional, Dict
import cv2
import win32gui
import win32con
import time
import numpy as np
from mss import mss

class DXGICapture:
    def __init__(self, title_partial: str = "Tibia -"):
        self.title_partial = title_partial
        self.hwnd = self.find_window()
        if not self.hwnd:
            raise ValueError("Ventana Tibia no encontrada")
        self.update_region()
        self.fps = 0.0
        self.latency_ms = 0.0
        self.dropped = 0

    def find_window(self) -> int:
        def enum_handler(hwnd, ctx):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if self.title_partial in title:
                    ctx.append(hwnd)
        hwnds = []
        win32gui.EnumWindows(enum_handler, hwnds)
        return hwnds[0] if hwnds else None

    def update_region(self):
        if win32gui.IsIconic(self.hwnd):
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
        rect = win32gui.GetWindowRect(self.hwnd)
        self.region = {"top": rect[1], "left": rect[0], "width": rect[2] - rect[0], "height": rect[3] - rect[1]}

    def capture(self) -> Optional[np.ndarray]:
        start = time.time()
        frame = None
        with mss() as sct:
            try:
                sct_img = sct.grab(self.region)
                frame = np.array(sct_img)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            except Exception as e:
                print(f"MSS falló: {e}")
                return None
        if frame is None or frame.size == 0:
            self.dropped += 1
            return None
        self.latency_ms = (time.time() - start) * 1000
        self.fps = 1 / (self.latency_ms / 1000) if self.latency_ms > 0 else 0
        return frame