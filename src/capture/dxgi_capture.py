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
            print(f"No se encontró ventana con título '{self.title_partial}', usando captura de pantalla completa")
            frame = self.capture_fullscreen()
        else:
            frame = self.capture_window()
            if frame is None:
                print(f"BitBlt failed, intentando captura de pantalla completa")
                frame = self.capture_fullscreen()

        # Validar que la captura es útil
        if frame is not None and self.validate_capture(frame):
            return frame
        else:
            print("Warning: Captura no válida, retornando None")
            return None

    def capture_window(self) -> Optional[np.ndarray]:
        """Captura la ventana específica usando BitBlt"""
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

        if frame is not None:
            self.latency_ms = (time.time() - start) * 1000
            self.fps = 1 / (self.latency_ms / 1000) if self.latency_ms > 0 else 0
        return frame
        """Captura toda la pantalla usando MSS como fallback"""
        try:
            with mss() as sct:
                screenshot = sct.grab(sct.monitors[0])
                frame = np.frombuffer(screenshot.bgra, dtype=np.uint8)
                frame = frame.reshape((screenshot.height, screenshot.width, 4))
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                print(f"Captura de pantalla completa exitosa: {frame.shape}")
                return frame
        except Exception as e:
            print(f"Fallback MSS también falló: {e}")
            return None

    def validate_capture(self, frame: np.ndarray) -> bool:
        """Valida que la captura contiene contenido real y no es negra/vacía"""
        if frame is None or frame.size == 0:
            return False

        # Verificar que no es una imagen completamente negra
        if np.all(frame == 0):
            print("Warning: Captura completamente negra detectada")
            return False

        # Verificar que no es una imagen uniforme (todos los píxeles iguales)
        if np.all(frame == frame[0, 0]):
            print("Warning: Captura uniforme detectada (todos los píxeles iguales)")
            return False

        # Verificar que tiene variación de color (no es monocromática)
        if len(frame.shape) == 3:
            # Para imágenes RGB/BGR
            std_per_channel = [np.std(frame[:, :, i]) for i in range(3)]
            if all(std < 1.0 for std in std_per_channel):  # Muy poca variación
                print("Warning: Captura con muy poca variación de color")
                return False

        # Verificar resolución mínima
        min_width, min_height = 800, 600
        if frame.shape[1] < min_width or frame.shape[0] < min_height:
            print(f"Warning: Resolución demasiado baja: {frame.shape}")
            return False

        return True