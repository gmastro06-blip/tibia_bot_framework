from typing import Optional, Sequence, Union
import cv2
import win32api
import win32gui
import win32ui
import win32con
import numpy as np
import time
import os
from mss import mss

try:
    from input_focus_guard import update_capture_target_state
except Exception:  # pragma: no cover
    update_capture_target_state = None  # type: ignore[assignment]

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
        self.target_found: bool = False
        self.target_reason: str = ""
        self.capture_target: str = "auto"
        self.capture_backend: str = "dxgi"
        self.capture_bounds: Optional[tuple[int, int, int, int]] = None
        self.capture_state: str = ""
        # Best-effort: which monitor we believe we captured from.
        # (MSS indexes: 1..n for individual monitors, 0 is the combined virtual screen)
        self.capture_monitor_index: Optional[int] = None
        self.force_monitor = force_monitor
        # Si el caller fuerza un monitor, por defecto permitimos fallback (útil para el bot).
        # Herramientas de calibración suelen preferir "estricto" para no cambiar de monitor
        # y producir ROIs incorrectas. En el bot (runtime), NO activamos esto por env
        # para evitar quedarnos sin captura si el índice forzado no existe.
        self.strict_force_monitor = bool(strict_force_monitor)
        self.fps = 0.0
        self.latency_ms = 0.0
        self.dropped = 0
        self._verbose = os.getenv("CAPTURE_VERBOSE", "").strip().lower() in {"1", "true", "yes"}
        self._last_hwnd_state: Optional[bool] = None
        self._last_ok_log_ts = 0.0
        # Remember a known-good monitor index (MSS indexing).
        # Helps recover from wrong monitor selection / multi-monitor setups.
        self._last_good_monitor_index: Optional[int] = None

        # Optional: pick the most "active" monitor when window monitor cannot
        # be inferred. Default ON for runtime robustness.
        self._scan_active_monitor = os.getenv("CAPTURE_SCAN_ACTIVE_MONITOR", "1").strip().lower() not in {
            "0",
            "false",
            "no",
        }

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

    def capture_fullscreen(self, preferred_monitor: Optional[int] = None) -> Optional[np.ndarray]:
        """Captura toda la pantalla usando MSS como fallback.

        Prioridad:
        1) monitor preferido (si es válido)
        2) monitor donde está la ventana del cliente (si se puede inferir)
        3) monitores 1..n
        4) monitor 0 (virtual combinado)
        """
        try:
            with mss() as sct:
                # Determinar el orden de prioridad de monitores
                monitor_indices = []

                # Prefer explicit preferred monitor.
                try:
                    pm = int(preferred_monitor) if preferred_monitor is not None else None
                except Exception:
                    pm = None
                if pm is not None and 0 <= pm < len(sct.monitors):
                    # Prefer individual monitor indices when possible.
                    if pm != 0:
                        monitor_indices.append(pm)

                # Prefer last known-good monitor.
                try:
                    lg = int(self._last_good_monitor_index) if self._last_good_monitor_index is not None else None
                except Exception:
                    lg = None
                if lg is not None and 0 < lg < len(sct.monitors):
                    if lg not in monitor_indices:
                        monitor_indices.append(lg)

                # Primero, intentar el monitor donde está la ventana de Tibia (si se encontró)
                tibia_monitor = self.find_window_monitor()
                if tibia_monitor is not None and tibia_monitor < len(sct.monitors) and tibia_monitor != 0:
                    monitor_indices.append(tibia_monitor)
                    if self._verbose:
                        print(f"Priorizando monitor {tibia_monitor} donde está la ventana")

                # If we still don't have a good hint, optionally pick the most active monitor.
                try:
                    if self._scan_active_monitor:
                        best_idx = self._find_most_active_monitor_index(sct)
                        if best_idx is not None and best_idx not in monitor_indices:
                            monitor_indices.append(best_idx)
                except Exception:
                    pass

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
                        if self.validate_capture(frame, min_width=800, min_height=600):
                            monitor_info = f"{monitor.get('width', 'unknown')}x{monitor.get('height', 'unknown')}"
                            if self._verbose:
                                print(f"Captura de pantalla completa exitosa en monitor {i} ({monitor_info}): {frame.shape}")
                            self.capture_monitor_index = int(i)
                            self._last_good_monitor_index = int(i)
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
                self.capture_monitor_index = None
                return None

        except Exception as e:
            print(f"Fallback MSS falló completamente: {e}")
            return None

    def find_window_monitor(self, hwnd: Optional[int] = None) -> Optional[int]:
        """Encuentra el monitor donde está ubicada la ventana del cliente."""

        try:
            h = int(hwnd or 0)
        except Exception:
            h = 0
        if not h:
            try:
                h = int(self.client_hwnd or 0)
            except Exception:
                h = 0
        if not h:
            try:
                h = int(self.hwnd or 0)
            except Exception:
                h = 0
        if not h:
            return None

        try:
            # Obtener las coordenadas de la ventana
            rect = win32gui.GetWindowRect(int(h))
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
                if self.validate_capture(frame, min_width=800, min_height=600):
                    monitor_info = f"{monitor.get('width', 'unknown')}x{monitor.get('height', 'unknown')}"
                    self._log_ok(
                        f"Captura de monitor específico exitosa en monitor {monitor_index} ({monitor_info}): {frame.shape}"
                    )
                    self._last_good_monitor_index = int(monitor_index)
                    return frame
                else:
                    if self._verbose:
                        print(f"Monitor {monitor_index} no válido")
                    return None

        except Exception as e:
            if self._verbose:
                print(f"Error capturando monitor {monitor_index}: {e}")
            return None

    def _find_most_active_monitor_index(self, sct) -> Optional[int]:
        """Devuelve el índice MSS (1..n) del monitor con más "actividad".

        Heurística: suma de std-dev por canal en una captura rápida.
        Es un fallback para setups multi-monitor cuando no podemos inferir
        el monitor por HWND/bounds.
        """

        best_i: Optional[int] = None
        best_score = 0.0

        for i in range(1, len(sct.monitors)):
            try:
                monitor = sct.monitors[i]
                screenshot = sct.grab(monitor)
                frame = np.frombuffer(screenshot.bgra, dtype=np.uint8)
                frame = frame.reshape((screenshot.height, screenshot.width, 4))
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR).astype(np.uint8, copy=False)

                # Si es inválida según nuestra validación de fullscreen, no cuenta.
                if not self.validate_capture(frame, min_width=800, min_height=600):
                    continue

                variation = 0.0
                try:
                    variation = float(sum(float(np.std(frame[:, :, c])) for c in range(3)))
                except Exception:
                    variation = 0.0

                if variation > best_score:
                    best_score = variation
                    best_i = int(i)
            except Exception:
                continue

        return best_i

    def capture(self) -> Optional[np.ndarray]:
        frame: Optional[np.ndarray] = None
        # Always refresh capture target snapshot (best-effort) so we can monitor
        # even when the client is not foreground and/or gets recreated.
        try:
            if callable(update_capture_target_state):
                st = update_capture_target_state()
                self.capture_backend = str(getattr(st, "capture_backend", "dxgi") or "dxgi")
                self.capture_target = str(getattr(st, "capture_target", "auto") or "auto")
                self.client_hwnd = int(getattr(st, "target_hwnd", 0) or 0)
                self.client_title = str(getattr(st, "target_title", "") or "")
                self.capture_bounds = getattr(st, "target_bounds", None)
                self.target_found = bool(getattr(st, "target_found", False))
                self.target_reason = str(getattr(st, "reason", "") or "")
                if bool(getattr(st, "target_is_minimized", False)):
                    self.capture_state = "minimized"
                    self.capture_monitor_index = None
                    return None
        except Exception:
            pass

        # Best-effort: infer monitor index from current client hwnd.
        try:
            self.capture_monitor_index = self.find_window_monitor(self.client_hwnd)
        except Exception:
            self.capture_monitor_index = None

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
                            # IMPORTANT: window-crop capture can return black/empty
                            # content even when the window exists (occlusion/minimized/
                            # GPU surface quirks). Validate and fall back to fullscreen.
                            if self.validate_capture(frame, min_width=80, min_height=80):
                                if self.capture_monitor_index is not None:
                                    self.capture_state = f"window_crop@mon{int(self.capture_monitor_index)}"
                                else:
                                    self.capture_state = "window_crop"
                                return frame
                            if self._verbose:
                                print("Window-crop inválido; fallback a fullscreen")
                            self.capture_state = "window_crop_invalid"

                            # Fallback #1: try BitBlt against the target HWND.
                            # This can work when MSS returns a black surface.
                            try:
                                bb = self.capture_window(hwnd=self.client_hwnd)
                                if bb is not None and self.validate_capture(bb, min_width=80, min_height=80):
                                    self.capture_state = "bitblt"
                                    return bb
                            except Exception:
                                pass

                            # Fallback #2 (most general): capture from the desktop
                            # DC (screen) and crop to the window bounds.
                            # This works even when window DC capture fails.
                            try:
                                sc = self.capture_screen_crop(bounds=(int(l), int(t), int(r), int(b)))
                                if sc is not None and self.validate_capture(sc, min_width=80, min_height=80):
                                    self.capture_state = "screen_crop"
                                    return sc
                            except Exception:
                                pass
        except Exception:
            # Fall back to monitor/fullscreen capture.
            pass

        # Prefer forced monitor when provided, but fall back to scanning all monitors.
        if self.force_monitor is not None:
            if self._verbose:
                print(f"Forzando captura en monitor {self.force_monitor}")
            frame = self.capture_specific_monitor(self.force_monitor)
            if frame is not None:
                self.capture_monitor_index = int(self.force_monitor)
                self.capture_state = f"monitor_forced@mon{int(self.force_monitor)}"
                return frame
            if self.strict_force_monitor:
                # No hacer fallback a otros monitores: evita que tools (ROI/minimap) capturen otra pantalla.
                if self._verbose:
                    print("Monitor forzado falló (modo estricto); devolviendo None")
                self.capture_state = "monitor_forced_failed"
                self.capture_monitor_index = int(self.force_monitor)
                return None
            if self._verbose:
                print("Monitor forzado falló; usando búsqueda en todos los monitores")

        # Prefer the inferred window monitor (if available) for fullscreen capture.
        pref = self.capture_monitor_index
        self.capture_state = f"fullscreen@mon{int(pref)}" if pref is not None else "fullscreen"
        return self.capture_fullscreen(preferred_monitor=pref)

    def capture_window(self, hwnd: Optional[int] = None) -> Optional[np.ndarray]:
        """Captura una ventana específica usando BitBlt.

        Nota: BitBlt puede funcionar cuando MSS entrega frames negros.
        """
        start = time.time()
        frame = None
        wDC = dcObj = cDC = bitmap = None
        try:
            h = int(hwnd or 0)
            if not h:
                try:
                    h = int(self.client_hwnd or 0)
                except Exception:
                    h = 0
            if not h:
                try:
                    h = int(self.hwnd or 0)
                except Exception:
                    h = 0
            if not h:
                return None

            wDC = win32gui.GetWindowDC(h)
            dcObj = win32ui.CreateDCFromHandle(wDC)
            cDC = dcObj.CreateCompatibleDC()
            rect = win32gui.GetClientRect(h)
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
                if wDC is not None and (hwnd or self.client_hwnd or self.hwnd):
                    try:
                        h = int(hwnd or self.client_hwnd or self.hwnd or 0)
                    except Exception:
                        h = 0
                    if h:
                        win32gui.ReleaseDC(h, wDC)
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

    def capture_screen_crop(self, *, bounds: tuple[int, int, int, int]) -> Optional[np.ndarray]:
        """Captura el escritorio (screen DC) y recorta a `bounds`.

        `bounds` debe estar en coordenadas de pantalla (virtual screen), igual
        que las que entrega `update_capture_target_state`.
        """

        start = time.time()
        frame: Optional[np.ndarray] = None
        wDC = dcObj = cDC = bitmap = None

        try:
            l, t, r, b = bounds
            w = max(1, int(r) - int(l))
            h = max(1, int(b) - int(t))
            if w < 80 or h < 80:
                return None

            # Virtual screen origin (can be negative on multi-monitor setups).
            vx = int(win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN))
            vy = int(win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN))

            src_x = int(l) - int(vx)
            src_y = int(t) - int(vy)

            # Desktop DC
            wDC = win32gui.GetDC(0)
            dcObj = win32ui.CreateDCFromHandle(wDC)
            cDC = dcObj.CreateCompatibleDC()

            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(dcObj, int(w), int(h))
            cDC.SelectObject(bitmap)

            # Copy only the region we need (avoid huge virtual-screen bitmaps).
            cDC.BitBlt((0, 0), (int(w), int(h)), dcObj, (int(src_x), int(src_y)), win32con.SRCCOPY)

            signedIntsArray = bitmap.GetBitmapBits(True)
            img = np.frombuffer(signedIntsArray, dtype="uint8")
            img.shape = (int(h), int(w), 4)
            frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        except Exception as e:
            if self._verbose:
                try:
                    print(f"Screen-crop failed: {e}")
                except Exception:
                    pass
            frame = None
        finally:
            try:
                if wDC is not None:
                    win32gui.ReleaseDC(0, wDC)
            except Exception:
                pass
            try:
                if dcObj is not None:
                    dcObj.DeleteDC()
            except Exception:
                pass
            try:
                if cDC is not None:
                    cDC.DeleteDC()
            except Exception:
                pass
            try:
                if bitmap is not None:
                    win32gui.DeleteObject(bitmap.GetHandle())
            except Exception:
                pass

        if frame is not None:
            self.latency_ms = (time.time() - start) * 1000
            self.fps = 1 / (self.latency_ms / 1000) if self.latency_ms > 0 else 0
        return frame

    def validate_capture(self, frame: np.ndarray, *, min_width: int = 800, min_height: int = 600) -> bool:
        """Valida que la captura contiene contenido real y no es negra/vacía.

        `min_width/min_height` permite validar crops de ventana más pequeños
        sin forzar resolución de monitor.
        """
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
        if frame.shape[1] < min_width or frame.shape[0] < min_height:
            print(f"Warning: Resolución demasiado baja: {frame.shape}")
            return False

        return True