import cv2
import time
import numpy as np
from typing import Optional, Tuple

class ObsCapture:
    """
    Clase para capturar frames desde un stream RTMP de OBS Studio.
    OBS debe estar configurado para transmitir a un servidor RTMP local.
    """

    def __init__(self, rtmp_url: str = "rtmp://localhost/live/stream", timeout: float = 5.0):
        """
        Inicializa la captura desde OBS.

        Args:
            rtmp_url: URL del stream RTMP (por defecto localhost).
            timeout: Tiempo máximo para esperar conexión en segundos.
        """
        self.rtmp_url = rtmp_url
        self.timeout = timeout
        self.cap: Optional[cv2.VideoCapture] = None
        self.connected = False

    def connect(self) -> bool:
        """
        Conecta al stream RTMP de OBS.

        Returns:
            True si la conexión es exitosa, False en caso contrario.
        """
        try:
            self.cap = cv2.VideoCapture(self.rtmp_url, cv2.CAP_FFMPEG)
            if not self.cap.isOpened():
                print("Error: No se pudo abrir el stream RTMP. Verifica que OBS esté transmitiendo.")
                return False

            # Espera hasta que haya un frame disponible
            start_time = time.time()
            while not self.cap.isOpened() or self.cap.get(cv2.CAP_PROP_POS_FRAMES) < 1:
                if time.time() - start_time > self.timeout:
                    print(f"Timeout: No se recibió frame en {self.timeout} segundos.")
                    self.cap.release()
                    return False
                time.sleep(0.1)

            self.connected = True
            print("Conectado exitosamente al stream RTMP de OBS.")
            return True
        except Exception as e:
            print(f"Error al conectar: {e}")
            return False

    def capture(self) -> Optional[np.ndarray]:
        """
        Captura un frame del stream.

        Returns:
            Frame como array numpy (BGR), o None si falla.
        """
        if not self.connected or self.cap is None:
            print("Error: No conectado al stream.")
            return None

        try:
            ret, frame = self.cap.read()
            if not ret or frame is None:
                print("Error: No se pudo leer frame del stream.")
                return None

            # Validación básica: Verifica que no sea negro o uniforme
            if self._is_frame_valid(frame):
                return frame
            else:
                print("Frame inválido detectado (posiblemente negro o dummy).")
                return None
        except Exception as e:
            print(f"Error al capturar frame: {e}")
            return None

    def _is_frame_valid(self, frame: np.ndarray) -> bool:
        """
        Valida que el frame no sea negro o tenga poco contenido.

        Args:
            frame: Frame a validar.

        Returns:
            True si es válido.
        """
        # Verifica resolución mínima
        if frame.shape[0] < 100 or frame.shape[1] < 100:
            return False

        # Verifica variación de color (no uniforme)
        std_dev = np.std(frame)
        if std_dev < 5:  # Umbral bajo para detectar negro/uniforme
            return False

        return True

    def disconnect(self):
        """Cierra la conexión al stream."""
        if self.cap:
            self.cap.release()
            self.cap = None
        self.connected = False
        print("Desconectado del stream RTMP.")

    def __del__(self):
        self.disconnect()


# Ejemplo de uso para pruebas
if __name__ == "__main__":
    # Configura OBS para transmitir a rtmp://localhost/live/stream
    # (Crea una escena con fuente de captura de ventana de Tibia)

    capture = ObsCapture()
    if capture.connect():
        for _ in range(10):  # Captura 10 frames de prueba
            frame = capture.capture()
            if frame is not None:
                print(f"Frame capturado: {frame.shape}")
                # Aquí puedes procesar con OCR o mostrar
                # cv2.imshow("Frame", frame)
                # cv2.waitKey(1)
            time.sleep(0.5)  # Espera entre capturas
        capture.disconnect()
    else:
        print("No se pudo conectar. Verifica OBS.")