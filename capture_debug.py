import sys
import os

# Configurar path para imports absolutos desde el directorio del proyecto
project_root = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(project_root, 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

import cv2
import json
from capture.obs_websocket_capture import OBSWebSocketCapture
import time

def load_roi_config(resolution: tuple) -> tuple:
    """Carga configuración de ROIs según la resolución detectada"""
    width, height = resolution
    config_files = {
        (2048, 1076): "configs/rois_guess.json",
        (1920, 1080): "configs/rois_guess_1920x1080.json",
    }

    config_file = config_files.get((width, height), "configs/rois_guess.json")  # fallback

    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
            print(f"Configuración cargada desde {config_file} para resolución {width}x{height}")
            return config["rois_guess_norm"], config["source_resolution"]
    except FileNotFoundError:
        print(f"Archivo de configuración {config_file} no encontrado, usando configuración por defecto")
        return {}, [2048, 1076]

if __name__ == "__main__":
    print("Capturando ROIs de debug...")

    # Crear directorio para guardar imágenes de debug
    debug_dir = "debug_images_real"
    if not os.path.exists(debug_dir):
        os.makedirs(debug_dir)

    # Probar la captura desde OBS via WebSocket
    capture = OBSWebSocketCapture()
    print("Conectando a OBS via WebSocket...")

    if not capture.connect():
        print("❌ No se pudo conectar a OBS WebSocket.")
        exit(1)

    # Intentar capturar
    frame = capture.capture()
    if frame is not None:
        print(f"✅ Captura exitosa: {frame.shape}")

        # Guardar imagen completa para análisis
        cv2.imwrite(os.path.join(debug_dir, f"captura_completa_{time.time()}.png"), frame)

        # Cargar configuración de ROIs
        resolution = (frame.shape[1], frame.shape[0])
        rois, source_resolution = load_roi_config(resolution)
        print(f"Resolución detectada: {resolution}, ROIs cargadas para: {source_resolution}")

        # Función para convertir coordenadas normalizadas a píxeles
        def normalize_to_px(roi_norm: dict) -> tuple:
            x = int(roi_norm['x'] * resolution[0])
            y = int(roi_norm['y'] * resolution[1])
            w = int(roi_norm['w'] * resolution[0])
            h = int(roi_norm['h'] * resolution[1])
            return x, y, w, h

        # Extraer y guardar ROIs de HP/MP
        if 'hp_top_ocr' in rois:
            hp_roi = normalize_to_px(rois['hp_top_ocr'])
            hp_crop = frame[hp_roi[1]:hp_roi[1]+hp_roi[3], hp_roi[0]:hp_roi[0]+hp_roi[2]]
            cv2.imwrite(os.path.join(debug_dir, "hp_roi_real.png"), hp_crop)
            print(f"HP ROI guardada: {hp_roi}")

        if 'mp_top_ocr' in rois:
            mp_roi = normalize_to_px(rois['mp_top_ocr'])
            mp_crop = frame[mp_roi[1]:mp_roi[1]+mp_roi[3], mp_roi[0]:mp_roi[0]+mp_roi[2]]
            cv2.imwrite(os.path.join(debug_dir, "mp_roi_real.png"), mp_crop)
            print(f"MP ROI guardada: {mp_roi}")

        print(f"Imágenes guardadas en: {debug_dir}")

    else:
        print("❌ No se pudo capturar frame.")
    
    capture.disconnect()