from typing import Tuple, Optional, Sequence, Union
import cv2
import win32gui
import win32ui
import win32con
import numpy as np
import time
import os
from mss import mss

try:
    from input_focus_guard import update_client_state
except Exception:  # pragma: no cover
    update_client_state = None  # type: ignore[assignment]

class DXGICapture:
    def __init__(
        self,
        title_partial: Union[str, Sequence[str]] = "Tibia",
        force_monitor: Optional[int] = None,
        *,
        strict_force_monitor: bool = False,
    ):
        # Acepta un string o una lista de strings para matchear títulos de ventanas.
        if isinstance(title_partial, str):
            self.title_partials = [title_partial]
        else:
            self.title_partials = [p for p in title_partial if p]
        self.hwnd = self.find_window()
        # Client discovery (hwnd can exist even when background/maximized).
        self.client_hwnd: int = int(self.hwnd or 0)
        self.client_title: str = ""
        self.capture_bounds: Optional[tuple[int, int, int, int]] = None
        self.capture_state: str = ""
        self.force_monitor = force_monitor
        # Si el caller fuerza un monitor, por defecto permitimos fallback (útil para el bot).
        # Herramientas de calibración suelen preferir "estricto" para no cambiar de monitor
        # y producir ROIs incorrectas.
        env_strict = os.getenv("CAPTURE_STRICT_FORCE_MONITOR", "").strip().lower() in {"1", "true", "yes"}
        self.strict_force_monitor = bool(strict_force_monitor or env_strict)
        self.fps = 0.0
        self.latency_ms = 0.0
        self.dropped = 0
        self._verbose = os.getenv("CAPTURE_VERBOSE", "").strip().lower() in {"1", "true", "yes"}
        self._last_hwnd_state: Optional[bool] = None
        self._last_ok_log_ts = 0.0

    def _log_ok(self, msg: str) -> None:
        if not self._verbose:
            return
        now = time.time()
        # rate-limit para evitar spam
        if now - self._last_ok_log_ts < 2.0:
            return
        self._last_ok_log_ts = now
        try:
            print(msg)
        except Exception:
            pass

    def find_window(self) -> int:
        def enum_handler(hwnd, ctx):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                title_l = title.lower()
                for partial in self.title_partials:
                    if partial.lower() in title_l:
                        ctx.append(hwnd)
                        break
        hwnds: list[int] = []
        win32gui.EnumWindows(enum_handler, hwnds)
        return hwnds[0] if hwnds else 0

    def capture_fullscreen(self) -> Optional[np.ndarray]:
        """Captura toda la pantalla usando MSS como fallback, priorizando el monitor con Tibia"""
        try:
            with mss() as sct:
                # Determinar el orden de prioridad de monitores
                monitor_indices = []

                # Primero, intentar el monitor donde está la ventana de Tibia (si se encontró)
                tibia_monitor = self.find_window_monitor()
                if tibia_monitor is not None and tibia_monitor < len(sct.monitors) and tibia_monitor != 0:
                    monitor_indices.append(tibia_monitor)
                    if self._verbose:
                        print(f"Priorizando monitor {tibia_monitor} donde está la ventana")

                # Luego priorizar monitores individuales (1..n) antes del monitor 0 combinado
                for i in range(1, len(sct.monitors)):
                    if i not in monitor_indices:
                        monitor_indices.append(i)
                if 0 < len(sct.monitors) and 0 not in monitor_indices:
                    monitor_indices.append(0)

                # Intentar capturar en cada monitor en orden de prioridad
                for i in monitor_indices:
                    try:
                        monitor = sct.monitors[i]
                        screenshot = sct.grab(monitor)
                        frame = np.frombuffer(screenshot.bgra, dtype=np.uint8)
                        frame = frame.reshape((screenshot.height, screenshot.width, 4))
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR).astype(np.uint8, copy=False)

                        # Verificar que la captura es válida antes de retornarla
                        if self.validate_capture(frame):
                            monitor_info = f"{monitor.get('width', 'unknown')}x{monitor.get('height', 'unknown')}"
                            if self._verbose:
                                print(f"Captura de pantalla completa exitosa en monitor {i} ({monitor_info}): {frame.shape}")
                            return frame
                        else:
                            if self._verbose:
                                print(f"Monitor {i} no válido, intentando siguiente...")
                            continue

                    except Exception as e:
                        if self._verbose:
                            print(f"Error en monitor {i}: {e}")
                        continue

                if self._verbose:
                    print("No se pudo capturar en ningún monitor")
                return None

        except Exception as e:
            print(f"Fallback MSS falló completamente: {e}")
            return None

    def find_window_monitor(self) -> Optional[int]:
        """Encuentra el monitor donde está ubicada la ventana de Tibia"""
        if not self.hwnd:
            return None

        try:
            # Obtener las coordenadas de la ventana
            rect = win32gui.GetWindowRect(self.hwnd)
            window_x, window_y = rect[0], rect[1]
            window_width, window_height = rect[2] - rect[0], rect[3] - rect[1]
            if self._verbose:
                print(f"Ventana en posición: ({window_x}, {window_y}) tamaño: {window_width}x{window_height}")

            # Obtener información de todos los monitores
            with mss() as sct:
                if self._verbose:
                    print(f"Monitores disponibles: {len(sct.monitors)}")
                best_monitor = None
                best_overlap_ratio = 0

                for i, monitor in enumerate(sct.monitors):
                    mon_x, mon_y = monitor['left'], monitor['top']
                    mon_width, mon_height = monitor['width'], monitor['height']
                    if self._verbose:
                        print(f"  Monitor {i}: pos ({mon_x}, {mon_y}) tamaño {mon_width}x{mon_height}")

                    # Calcular overlap entre ventana y monitor
                    overlap_x = max(0, min(window_x + window_width, mon_x + mon_width) - max(window_x, mon_x))
                    overlap_y = max(0, min(window_y + window_height, mon_y + mon_height) - max(window_y, mon_y))
                    overlap_area = overlap_x * overlap_y

                    if overlap_area > 0:
                        monitor_area = mon_width * mon_height
                        overlap_ratio = overlap_area / monitor_area
                        if self._verbose:
                            print(f"    Overlap con monitor {i}: {overlap_ratio:.2f}")

                        if overlap_ratio > best_overlap_ratio:
                            best_overlap_ratio = overlap_ratio
                            best_monitor = i

                if best_monitor is not None:
                    print(f"Ventana asignada a monitor {best_monitor} (mejor overlap: {best_overlap_ratio:.2f})")
                    return best_monitor

                print("Ventana no encontrada en ningún monitor")

        except Exception as e:
            print(f"Error detectando monitor de ventana: {e}")

        return None

    def capture_specific_monitor(self, monitor_index: int) -> Optional[np.ndarray]:
        """Captura un monitor específico por índice"""
        try:
            with mss() as sct:
                if monitor_index >= len(sct.monitors):
                    print(f"Monitor {monitor_index} no existe")
                    return None

                monitor = sct.monitors[monitor_index]
                screenshot = sct.grab(monitor)
                frame = np.frombuffer(screenshot.bgra, dtype=np.uint8)
                frame = frame.reshape((screenshot.height, screenshot.width, 4))
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR).astype(np.uint8, copy=False)

                # Verificar que la captura es válida
                if self.validate_capture(frame):
                    monitor_info = f"{monitor.get('width', 'unknown')}x{monitor.get('height', 'unknown')}"
                    self._log_ok(
                        f"Captura de monitor específico exitosa en monitor {monitor_index} ({monitor_info}): {frame.shape}"
                    )
                    return frame
                else:
                    if self._verbose:
                        print(f"Monitor {monitor_index} no válido")
                    return None

        except Exception as e:
            if self._verbose:
                print(f"Error capturando monitor {monitor_index}: {e}")
            return None

    def find_most_active_monitor(self, sct) -> Optional[dict]:
        """Encuentra el monitor con más actividad/contenido basado en variación de color"""
        best_monitor = None
        max_variation = 0.0

        # Empezar desde el monitor 1 (índice 1 en la lista de monitors)
        for i in range(1, len(sct.monitors)):
            try:
                monitor = sct.monitors[i]
                # Tomar una captura rápida del monitor para analizar
                screenshot = sct.grab(monitor)
                frame = np.frombuffer(screenshot.bgra, dtype=np.uint8)
                frame = frame.reshape((screenshot.height, screenshot.width, 4))
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR).astype(np.uint8, copy=False)

                # Calcular variación de color como indicador de actividad
                if len(frame.shape) == 3:
                    variation = float(sum(float(np.std(frame[:, :, c])) for c in range(3)))
                    if variation > max_variation:
                        max_variation = variation
                        best_monitor = monitor

            except Exception as e:
                print(f"Error analizando monitor {i}: {e}")
                continue

        return best_monitor

    def capture(self) -> Optional[np.ndarray]:
        # Always refresh client snapshot (best-effort) so we can monitor even
        # when the client is not foreground and/or gets recreated.
        try:
            if callable(update_client_state):
                st = update_client_state()
                self.client_hwnd = int(getattr(st, "hwnd", 0) or 0)
                self.client_title = str(getattr(st, "title", "") or "")
                self.capture_bounds = getattr(st, "bounds", None)
                if bool(getattr(st, "is_minimized", False)):
                    self.capture_state = "minimized"
                    return None
        except Exception:
            pass

        # Prefer window-crop capture when we have bounds.
        try:
            if self.capture_bounds is not None:
                l, t, r, b = self.capture_bounds
                w = max(1, int(r) - int(l))
                h = max(1, int(b) - int(t))
                # Sanity bounds: avoid absurd sizes.
                if w >= 80 and h >= 80:
                    with mss() as sct:
                        shot = sct.grab({"left": int(l), "top": int(t), "width": int(w), "height": int(h)})
                        frame = np.frombuffer(shot.bgra, dtype=np.uint8)
                        frame = frame.reshape((shot.height, shot.width, 4))
                        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR).astype(np.uint8, copy=False)
                        # Window crops can be smaller than full monitors.
                        if frame is not None and frame.size > 0 and frame.shape[1] >= 80 and frame.shape[0] >= 80:
                            self.capture_state = "window_crop"
                            return frame
        except Exception:
            # Fall back to monitor/fullscreen capture.
            pass

        # Prefer forced monitor when provided, but fall back to scanning all monitors.
        if self.force_monitor is not None:
            if self._verbose:
                print(f"Forzando captura en monitor {self.force_monitor}")
            frame = self.capture_specific_monitor(self.force_monitor)
            if frame is not None:
                self.capture_state = "monitor_forced"
                return frame
            if self.strict_force_monitor:
                # No hacer fallback a otros monitores: evita que tools (ROI/minimap) capturen otra pantalla.
                if self._verbose:
                    print("Monitor forzado falló (modo estricto); devolviendo None")
                self.capture_state = "monitor_forced_failed"
                return None
            if self._verbose:
                print("Monitor forzado falló; usando búsqueda en todos los monitores")

        self.capture_state = "fullscreen"
        return self.capture_fullscreen()

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