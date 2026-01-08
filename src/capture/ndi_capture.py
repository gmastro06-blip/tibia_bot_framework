import cv2
import numpy as np
from typing import Optional

class NDICapture:
    """
    Clase para capturar frames desde un stream NDI usando OpenCV y FFmpeg.
    Requiere FFmpeg instalado con soporte NDI (descarga de ffmpeg.org).
    OBS debe tener el plugin NDI activado y transmitiendo.
    """

    def __init__(self, ndi_source: str = "OBS", timeout: float = 10.0):
        """
        Inicializa la captura desde NDI.

        Args:
            ndi_source: Nombre de la fuente NDI (ej. "OBS" por defecto).
            timeout: Tiempo máximo para esperar conexión.
        """
        self.ndi_source = ndi_source
        self.timeout = timeout
        self.cap: Optional[cv2.VideoCapture] = None
        self.connected = False

    def connect(self) -> bool:
        """
        Conecta al stream NDI usando FFmpeg.

        Returns:
            True si la conexión es exitosa.
        """
        try:
            # Usar FFmpeg para consumir NDI
            # Nota: Requiere FFmpeg con libndi_newtek instalado
            ffmpeg_cmd = f"ffmpeg -f libndi_newtek -i '{self.ndi_source}' -f rawvideo -pix_fmt bgr24 -"
            self.cap = cv2.VideoCapture(ffmpeg_cmd, cv2.CAP_FFMPEG)
            if not self.cap.isOpened():
                print(f"Error: No se pudo abrir el stream NDI '{self.ndi_source}'. Verifica FFmpeg y OBS NDI.")
                return False

            import time
            start_time = time.time()
            while not self.cap.isOpened() or self.cap.get(cv2.CAP_PROP_POS_FRAMES) < 1:
                if time.time() - start_time > self.timeout:
                    print(f"Timeout: No se recibió frame en {self.timeout} segundos.")
                    self.cap.release()
                    return False
                time.sleep(0.1)

            self.connected = True
            print(f"Conectado exitosamente al stream NDI '{self.ndi_source}'.")
            return True
        except Exception as e:
            print(f"Error al conectar: {e}")
            return False

    def capture(self) -> Optional[np.ndarray]:
        """
        Captura un frame del stream NDI.

        Returns:
            Frame como array numpy, o None si falla.
        """
        if not self.connected or self.cap is None:
            print("Error: No conectado al stream NDI.")
            return None

        try:
            ret, frame = self.cap.read()
            if not ret or frame is None:
                print("Error: No se pudo leer frame del stream NDI.")
                return None

            if self._is_frame_valid(frame):
                return frame
            else:
                print("Frame inválido detectado.")
                return None
        except Exception as e:
            print(f"Error al capturar frame: {e}")
            return None

    def _is_frame_valid(self, frame: np.ndarray) -> bool:
        """Valida el frame."""
        if frame.shape[0] < 100 or frame.shape[1] < 100:
            return False
        std_dev = np.std(frame)
        if std_dev < 5:
            return False
        return True

    def disconnect(self):
        """Cierra la conexión."""
        if self.cap:
            self.cap.release()
            self.cap = None
        self.connected = False
        print("Desconectado del stream NDI.")

    def __del__(self):
        self.disconnect()


# Ejemplo de uso
if __name__ == "__main__":
    capture = NDICapture()
    if capture.connect():
        for _ in range(10):
            frame = capture.capture()
            if frame is not None:
                print(f"Frame capturado: {frame.shape}")
            import time
            time.sleep(0.5)
        capture.disconnect()