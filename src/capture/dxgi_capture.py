from typing import Tuple, Optional
import cv2
import win32gui
import win32ui
import win32con
import numpy as np
import time
from mss import mss

class DXGICapture:
    def __init__(self, title_partial: str = "Tibia -"):
        self.title_partial = title_partial
        self.hwnd = self.find_window()
        self.fps = 0.0
        self.latency_ms = 0.0
        self.dropped = 0
        self.mss_fallback = mss()

    def find_window(self) -> int:
        def enum_handler(hwnd, ctx):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if self.title_partial in title:
                    ctx.append(hwnd)
        hwnds: list[int] = []
        win32gui.EnumWindows(enum_handler, hwnds)
        return hwnds[0] if hwnds else 0

    def capture(self) -> Optional[np.ndarray]:
        if not self.hwnd:
            return None  # No window found, return None for testing
        start = time.time()
        frame = None
        wDC = dcObj = cDC = bitmap = None
        try:
            wDC = win32gui.GetWindowDC(self.hwnd)
            dcObj = win32ui.CreateDCFromHandle(wDC)
            cDC = dcObj.CreateCompatibleDC()
            rect = win32gui.GetClientRect(self.hwnd)
            width, height = rect[2], rect[3]
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(dcObj, width, height)
            cDC.SelectObject(bitmap)
            cDC.BitBlt((0, 0), (width, height), dcObj, (0, 0), win32con.SRCCOPY)
            signedIntsArray = bitmap.GetBitmapBits(True)
            img = np.frombuffer(signedIntsArray, dtype='uint8')
            img.shape = (height, width, 4)
            frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        except Exception as e:
            print(f"BitBlt failed: {e}")
            time.sleep(1.0)  # Sleep to prevent spam when window is invalid
            return None  # Don't try MSS fallback for now
        finally:
            try:
                if wDC is not None and self.hwnd:
                    win32gui.ReleaseDC(self.hwnd, wDC)
            except:
                pass
            try:
                if dcObj is not None:
                    dcObj.DeleteDC()
            except:
                pass
            try:
                if cDC is not None:
                    cDC.DeleteDC()
            except:
                pass
            try:
                if bitmap is not None:
                    win32gui.DeleteObject(bitmap.GetHandle())
            except:
                pass
        if frame is None or frame.size == 0:
            self.dropped += 1
            return None
        self.latency_ms = (time.time() - start) * 1000
        self.fps = 1 / (self.latency_ms / 1000) if self.latency_ms > 0 else 0
        return frame