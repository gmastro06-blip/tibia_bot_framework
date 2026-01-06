from typing import Optional, Tuple
import time
import cv2
import dxcam
import mss
import numpy as np

class CaptureProvider:
    def __init__(self, mode: str = "dxcam", window_title: str = "Tibia - Loterinne", resolution: Tuple[int, int] = (1920, 1080)):
        self.mode = mode.lower()
        self.window_title = window_title
        self.resolution = resolution
        self.rect = (0, 0, resolution[0], resolution[1])
        self.fps = 0
        self.last_time = time.time()

        if self.mode == "dxcam":
            self.camera = dxcam.create(output_idx=0, output_color="BGR", max_buffer_len=64)
            print("Captura dxcam inicializada (high FPS).")
        elif self.mode == "mss":
            self.sct = mss.mss()
            print("Fallback MSS.")
        else:
            raise ValueError("Modo inválido: 'dxcam' o 'mss'")

    def capture(self) -> Optional[np.ndarray]:
        retries = 3
        frame = None
        while retries > 0:
            if self.mode == "dxcam":
                frame = self.camera.grab(region=self.rect)
            elif self.mode == "mss":
                monitor = {"top": self.rect[1], "left": self.rect[0], "width": self.rect[2], "height": self.rect[3]}
                sct_img = self.sct.grab(monitor)
                frame = np.array(sct_img)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            if frame is not None:
                break
            retries -= 1
            time.sleep(0.01)
        self._update_fps()
        return frame

    def _update_fps(self):
        current = time.time()
        if current - self.last_time > 0:
            self.fps = 1 / (current - self.last_time)
        self.last_time = current

    def get_fps(self) -> float:
        return self.fps