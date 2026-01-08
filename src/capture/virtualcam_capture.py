import cv2
import numpy as np
from typing import Optional

class VirtualCamCapture:
    """
    Clase para capturar frames desde la webcam virtual creada por OBS Virtual Camera.
    Requiere el plugin OBS-VirtualCam instalado y activado.
    """

    def __init__(self, camera_index: int = 2, timeout: float = 5.0):
        """
        Inicializa la captura desde la webcam virtual.

        Args:
            camera_index: Índice de la webcam (0 por defecto, ajusta si hay múltiples).
            timeout: Tiempo máximo para esperar conexión en segundos.
        """
        self.camera_index = camera_index
        self.timeout = timeout
        self.cap: Optional[cv2.VideoCapture] = None
        self.connected = False

    def connect(self) -> bool:
        """
        Conecta a la webcam virtual.

        Returns:
            True si la conexión es exitosa, False en caso contrario.
        """
        try:
            self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_MSMF)  # Usar Media Foundation para Windows
            if not self.cap.isOpened():
                print(f"Error: No se pudo abrir la webcam virtual en índice {self.camera_index}. Verifica que OBS VirtualCam esté activo.")
                return False

            # Verificar que reciba frames
            import time
            start_time = time.time()
            while not self.cap.isOpened() or self.cap.get(cv2.CAP_PROP_POS_FRAMES) < 1:
                if time.time() - start_time > self.timeout:
                    print(f"Timeout: No se recibió frame en {self.timeout} segundos.")
                    self.cap.release()
                    return False
                time.sleep(0.1)

            self.connected = True
            print(f"Conectado exitosamente a la webcam virtual (índice {self.camera_index}).")
            return True
        except Exception as e:
            print(f"Error al conectar: {e}")
            return False

    def capture(self) -> Optional[np.ndarray]:
        """
        Captura un frame de la webcam virtual.

        Returns:
            Frame como array numpy (BGR), o None si falla.
        """
        if not self.connected or self.cap is None:
            print("Error: No conectado a la webcam virtual.")
            return None

        try:
            ret, frame = self.cap.read()
            if not ret or frame is None:
                print("Error: No se pudo leer frame de la webcam virtual.")
                return None

            # Validación básica
            if self._is_frame_valid(frame):
                return frame
            else:
                print("Frame inválido detectado.")
                return None
        except Exception as e:
            print(f"Error al capturar frame: {e}")
            return None

    def _is_frame_valid(self, frame: np.ndarray) -> bool:
        """
        Valida que el frame no sea negro o tenga poco contenido.
        """
        if frame.shape[0] < 100 or frame.shape[1] < 100:
            return False
        std_dev = np.std(frame)
        if std_dev < 5:
            return False
        return True

    def disconnect(self):
        """Cierra la conexión a la webcam."""
        if self.cap:
            self.cap.release()
            self.cap = None
        self.connected = False
        print("Desconectado de la webcam virtual.")

    def __del__(self):
        self.disconnect()


# Ejemplo de uso
if __name__ == "__main__":
    capture = VirtualCamCapture()
    if capture.connect():
        for _ in range(10):
            frame = capture.capture()
            if frame is not None:
                print(f"Frame capturado: {frame.shape}")
            import time
            time.sleep(0.5)
        capture.disconnect()