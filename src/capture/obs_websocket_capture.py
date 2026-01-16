import obsws_python as obs
from typing import Optional, Dict, Any, cast
import numpy as np
import base64
import cv2
from io import BytesIO
from PIL import Image

class OBSWebSocketCapture:
    """
    Clase para integrar con OBS via WebSocket y capturar frames.
    Requiere OBS con el plugin obs-websocket instalado y activado.
    Combina control de OBS con captura de frames (ej. VirtualCam).
    """

    def __init__(self, host: str = "localhost", port: int = 4455, password: str = "", capture_method: str = "obs_source", source_name: str = "Tibia_Fuente"):
        """
        Inicializa la conexión WebSocket a OBS.

        Args:
            host: Host de OBS (localhost por defecto).
            port: Puerto WebSocket (4455 por defecto).
            password: Contraseña si está configurada en OBS.
            capture_method: Método de captura ("obs_source", "virtualcam", "rtmp", etc.).
            source_name: Nombre de la fuente en OBS para captura.
        """
        self.host = host
        self.port = port
        self.password = password
        self.capture_method = capture_method
        self.source_name = source_name
        self.client: Optional[obs.ReqClient] = None
        self.capture_client: Optional[Any] = None  # Para el método de captura
        self.connected = False

    def connect(self) -> bool:
        """
        Conecta a OBS via WebSocket y configura la captura.

        Returns:
            True si la conexión es exitosa.
        """
        try:
            self.client = obs.ReqClient(host=self.host, port=self.port, password=self.password)
            # Verificar conexión obteniendo la versión
            version = self.client.get_version()
            obs_version = getattr(version, "obs_version", None)
            print(f"Conectado a OBS WebSocket: {obs_version}")

            # Configurar método de captura
            if self.capture_method == "obs_source":
                # No necesita capture_client, usa WebSocket directamente
                pass
            elif self.capture_method == "virtualcam":
                from capture.virtualcam_capture import VirtualCamCapture

                self.capture_client = VirtualCamCapture()
            elif self.capture_method == "rtmp":
                from capture.obs_capture import ObsCapture
                self.capture_client = ObsCapture()
            elif self.capture_method == "ndi":
                from capture.ndi_capture import NDICapture
                self.capture_client = NDICapture()
            elif self.capture_method == "dxcam":
                from capture.dxgi_capture import DXGICapture
                # Intentar varios títulos posibles del Proyector OBS (según idioma/configuración)
                title_candidates = [
                    f"- {self.source_name}",
                    self.source_name,
                    "Proyector en ventana",
                    "Projector",
                    "Proyector",
                    "ventana (Fuente)",
                ]
                self.capture_client = DXGICapture(title_candidates)
                # DXGICapture no tiene connect(). Si no encuentra ventana, hará fallback (MSS) en capture().
                if not getattr(self.capture_client, "hwnd", 0):
                    print("⚠️  Ventana del proyector no encontrada aún; usando fallback en captura (MSS/monitores)")

            # Conectar el cliente de captura si tiene método connect
            capture_client = self.capture_client
            if capture_client is not None and hasattr(capture_client, 'connect'):
                if not capture_client.connect():
                    print("Error: No se pudo conectar el método de captura.")
                    return False
            # Si no tiene connect (como DXCam), asumir que está listo

            self.connected = True
            print(f"Integración completa: OBS WebSocket + {self.capture_method}")
            return True
        except Exception as e:
            print(f"Error al conectar a OBS WebSocket: {e}")
            return False

    def get_obs_status(self) -> Dict[str, Any]:
        """
        Obtiene el estado actual de OBS (escena activa, streaming, etc.).

        Returns:
            Dict con información de OBS.
        """
        if not self.client:
            return {}
        try:
            scene = self.client.get_current_program_scene()
            streaming = self.client.get_stream_status()
            return {
                "current_scene": getattr(scene, "current_program_scene_name", None),
                "is_streaming": getattr(streaming, "output_active", False),
                "is_recording": getattr(streaming, "output_active", False)  # Ajustar si es recording
            }
        except Exception as e:
            print(f"Error obteniendo estado OBS: {e}")
            return {}

    def set_scene(self, scene_name: str):
        """
        Cambia la escena activa en OBS.

        Args:
            scene_name: Nombre de la escena.
        """
        if self.client:
            try:
                self.client.set_current_program_scene(scene_name)
                print(f"Escena cambiada a: {scene_name}")
            except Exception as e:
                print(f"Error cambiando escena: {e}")

    def start_streaming(self):
        """Inicia el streaming en OBS."""
        if self.client:
            try:
                self.client.start_stream()
                print("Streaming iniciado en OBS.")
            except Exception as e:
                print(f"Error iniciando streaming: {e}")

    def capture(self) -> Optional[np.ndarray]:
        """
        Captura un frame usando el método configurado.

        Returns:
            Frame como array numpy, o None si falla.
        """
        if not self.connected:
            print("Error: No conectado.")
            return None
        
        if self.capture_method == "obs_source":
            return self._capture_from_obs_source()
        elif self.capture_client:
            return self.capture_client.capture()
        else:
            print("Error: Método de captura no configurado.")
            return None

    def _capture_from_obs_source(self) -> Optional[np.ndarray]:
        """
        Captura un frame tomando screenshot de la fuente OBS especificada.

        Returns:
            Frame como array numpy.
        """
        if self.client is None:
            return None
        try:
            # Tomar screenshot de la fuente
            if not hasattr(self.client, "call"):
                print("Error: obsws-python ReqClient no expone 'call'; revisa la versión del paquete.")
                return None

            call_fn = cast(Any, getattr(self.client, "call"))
            response = call_fn("TakeSourceScreenshot", {
                "sourceName": self.source_name,
                "imageFormat": "png",
                "imageWidth": 1920,  # Ajustar resolución si necesario
                "imageHeight": 1080
            })
            
            if response and "imageData" in response.datain:
                # Decodificar base64
                image_data = base64.b64decode(response.datain["imageData"])
                
                # Convertir a PIL Image
                image = Image.open(BytesIO(image_data))
                
                # Convertir a numpy array (BGR para OpenCV)
                frame = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
                
                return frame
            else:
                print(f"Error: No se pudo obtener screenshot de la fuente '{self.source_name}'")
                return None
        except Exception as e:
            print(f"Error capturando desde OBS fuente: {e}")
            return None

    def disconnect(self):
        """Cierra las conexiones."""
        if self.capture_client and hasattr(self.capture_client, 'disconnect'):
            self.capture_client.disconnect()
        if self.client:
            self.client.disconnect()
        self.connected = False
        print("Desconectado de OBS WebSocket e integración.")

    def __del__(self):
        self.disconnect()


# Ejemplo de uso
if __name__ == "__main__":
    capture = OBSWebSocketCapture(password="tu_password")  # Si tienes password
    if capture.connect():
        status = capture.get_obs_status()
        print(f"Estado OBS: {status}")
        frame = capture.capture()
        if frame is not None:
            print(f"Frame capturado: {frame.shape}")
        capture.disconnect()