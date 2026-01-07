import sys
import os

# Configurar path para imports absolutos desde el directorio del proyecto
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import pyautogui
from typing import Optional, Dict, List
import threading
from queue import Queue
import time
import numpy as np
import json
from capture.dxgi_capture import DXGICapture
# from calibration.ui_calibrator import UICalibrator
# from vision.inference import VisionInference
from vision.ocr import OCRProcessor
from gamestate.builder import GameState, GameStateBuilder
# from battlelist.extractor import BattlelistExtractor
# from bestiary.matcher import BestiaryMatcher
# from navigation.navigator import Navigator
# from decision.behavior_tree import BehaviorTree
# from action.executor import ActionExecutor
# from safety.manager import SafetyManager
# from telemetry.replay import Replay


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
        # Configuración por defecto (2048x1076)
        default_rois = {
            "hpmp_top_strip": {"x": 0.000000, "y": 0.000000, "w": 0.822754, "h": 0.037174},
            "hp_top_ocr": {"x": 0.052734, "y": 0.000000, "w": 0.107422, "h": 0.037174},
            "mp_top_ocr": {"x": 0.568359, "y": 0.000000, "w": 0.107422, "h": 0.037174},
            "skills_panel": {"x": 0.822754, "y": 0.000000, "w": 0.084961, "h": 0.375464},
            "battlelist_panel": {"x": 0.822754, "y": 0.375464, "w": 0.084961, "h": 0.251860},
            "battlelist_rows": {"x": 0.822754, "y": 0.397769, "w": 0.084961, "h": 0.229554},
            "right_hud_panel": {"x": 0.909668, "y": 0.002788, "w": 0.088867, "h": 0.408921},
            "minimap_content": {"x": 0.915039, "y": 0.004647, "w": 0.052734, "h": 0.104089},
            "equipment_slots": {"x": 0.909668, "y": 0.105020, "w": 0.053711, "h": 0.130111},
            "states_icons": {"x": 0.909668, "y": 0.249072, "w": 0.053711, "h": 0.032527},
            "hpmp_low_panel": {"x": 0.909668, "y": 0.264872, "w": 0.088867, "h": 0.055762},
            "hp_low_bar": {"x": 0.919434, "y": 0.276952, "w": 0.078125, "h": 0.013011},
            "mp_low_bar": {"x": 0.919434, "y": 0.289967, "w": 0.078125, "h": 0.013011},
            "game_viewport": {"x": 0.000000, "y": 0.037174, "w": 0.822754, "h": 0.780669},
            "chat_panel": {"x": 0.000000, "y": 0.817843, "w": 0.822754, "h": 0.182157}
        }
        return default_rois, [2048, 1076]


def main() -> None:
    print("Iniciando sistema de captura y análisis de HP/MP...")

    # Probar la captura
    capture = DXGICapture("Tibia - Loterinne")  # Título específico encontrado
    print(f"Buscando ventana con título que contenga: '{capture.title_partial}'")

    # Intentar capturar
    frame = capture.capture()
    if frame is not None:
        print(f"✅ Captura exitosa: {frame.shape}")

        # Cargar configuración de ROIs
        resolution = (frame.shape[1], frame.shape[0])  # (width, height)
        rois, source_resolution = load_roi_config(resolution)
        print(f"Resolución detectada: {resolution}, ROIs cargadas para: {source_resolution}")

        # Inicializar procesadores
        gamestate_builder = GameStateBuilder()

        # Extraer HP/MP del frame
        gamestate = gamestate_builder.update_from_frame(frame, rois, resolution)

        # Mostrar valores por consola
        print("\n" + "="*50)
        print("VALORES EXTRAÍDOS:")
        print("="*50)
        print(f"HP Actual: {gamestate.hp_current}")
        print(f"MP Actual: {gamestate.mp_current}")
        print(f"Estado completo: {gamestate}")
        print("="*50)

    else:
        print("❌ No se pudo capturar")

    print("Sistema de análisis probado.")


if __name__ == "__main__":
    main()
