import sys
import os

# Configurar path para imports absolutos desde el directorio del proyecto
project_root = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(project_root, 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

import cv2
import numpy as np
from gamestate.builder import GameStateBuilder

def create_tibia_interface_mock(resolution=(1920, 1080)):
    """Crea una imagen simulada de la interfaz de Tibia con HP/MP visibles"""
    import cv2
    import numpy as np

    # Crear imagen base (fondo gris oscuro simulando el juego)
    img = np.full((resolution[1], resolution[0], 3), (30, 30, 30), dtype=np.uint8)

    # Dibujar elementos de la interfaz de Tibia

    # Barra superior con HP/MP (gris claro)
    top_bar_height = int(0.037 * resolution[1])  # ~40 píxeles en 1080p
    cv2.rectangle(img, (0, 0), (resolution[0], top_bar_height), (60, 60, 60), -1)

    # Texto HP (posición basada en ROIs normalizadas)
    hp_x = int(0.0494 * resolution[0])  # hp_top_ocr x
    hp_y = int(0.037 * resolution[1])   # hp_top_ocr y + h
    cv2.putText(img, "160/180", (hp_x, hp_y-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    # Texto MP (posición basada en ROIs normalizadas)
    mp_x = int(0.5328 * resolution[0])  # mp_top_ocr x
    mp_y = int(0.037 * resolution[1])   # mp_top_ocr y + h
    cv2.putText(img, "85/85", (mp_x, mp_y-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    return img

def load_roi_config(resolution: tuple) -> tuple:
    """Carga configuración de ROIs según la resolución detectada"""
    width, height = resolution
    config_files = {
        (2048, 1076): "configs/rois_guess.json",
        (1920, 1080): "configs/rois_guess_1920x1080.json",
    }

    config_file = config_files.get((width, height), "configs/rois_guess.json")  # fallback

    try:
        import json
        with open(config_file, 'r') as f:
            config = json.load(f)
            print(f"Configuración cargada desde {config_file} para resolución {width}x{height}")
            return config["rois_guess_norm"], config["source_resolution"]
    except FileNotFoundError:
        print(f"Archivo de configuración {config_file} no encontrado, usando configuración por defecto")
        # Configuración por defecto (2048x1076)
        default_rois = {
            "hpmp_top_strip": {"x": 0.000000, "y": 0.000000, "w": 0.822754, "h": 0.037174},
            "hp_top_ocr": {"x": 0.052734, "y": 0.000000, "w": 0.107422, "h": 0.037174},
            "mp_top_ocr": {"x": 0.568359, "y": 0.000000, "w": 0.107422, "h": 0.037174},
        }
        return default_rois, [2048, 1076]

if __name__ == "__main__":
    print("Prueba con interfaz simulada...")

    # Crear imagen simulada con valores correctos
    mock_frame = create_tibia_interface_mock((1920, 1080))

    # Cargar configuración de ROIs
    resolution = (1920, 1080)
    rois, source_resolution = load_roi_config(resolution)

    # Inicializar procesadores
    gamestate_builder = GameStateBuilder()

    # Extraer HP/MP del frame simulado
    gamestate = gamestate_builder.update_from_frame(mock_frame, rois, resolution)

    # Mostrar resultados
    print("\n" + "="*60)
    print("RESULTADOS DE PRUEBA REALISTA:")
    print("="*60)
    print(f"HP Actual: {gamestate.hp_current}")
    print(f"MP Actual: {gamestate.mp_current}")
    print(f"Estado completo: {gamestate}")
    print("="*60)

    # Verificar si la detección fue exitosa
    success = gamestate.hp_current == 160 and gamestate.mp_current == 85
    if success:
        print("✅ ¡PRUEBA EXITOSA! HP/MP detectados correctamente")
    else:
        print("⚠️  Detección parcial - revisar ROIs o OCR")