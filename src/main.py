import os
import sys

# Configurar sys.path para imports absolutos del proyecto.
# Este repo usa imports tipo `from capture...` (módulos dentro de `src/`).
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src_dir = os.path.dirname(os.path.abspath(__file__))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import threading
from queue import Queue
import time
import json
from capture.dxgi_capture import DXGICapture
from gamestate.builder import GameStateBuilder

def load_roi_config(resolution: tuple) -> tuple:
    """Carga configuración de ROIs según la resolución detectada"""
    width, height = resolution
    config_files = {
        (2048, 1076): "configs/rois_guess.json",
        (1920, 1080): "configs/rois_guess_1920x1080.json",
        (1920, 1009): "configs/rois_guess_1920x1080.json",
    }

    config_file = config_files.get((width, height), "configs/rois_guess_1920x1080.json")  # fallback

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
            "hp_top_ocr": {"x": 0.052734, "y": 0.060000, "w": 0.107422, "h": 0.037174},
            "mp_top_ocr": {"x": 0.568359, "y": 0.060000, "w": 0.107422, "h": 0.037174},
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
    """Entry-point estable: ejecuta el bot."""
    run_bot()


def run_bot():
    """Ejecuta el bot completo con pipeline threaded usando OBS WebSocket + DXCam"""
    print("🚀 Iniciando Tibia Bot Framework...")

    # Configurar queues
    frame_queue = Queue(maxsize=5)  # Capture → Vision
    gs_queue = Queue(maxsize=5)     # Vision → Decision

    # Inicializar componentes
    # Preferimos monitor 2 por defecto (proyector), pero mantenemos fallback:
    # si ese monitor falla, DXGICapture captura buscando en todos los monitores.
    force_monitor_raw = os.getenv("FORCE_MONITOR", "").strip()
    force_monitor = 2
    if force_monitor_raw:
        try:
            force_monitor = int(force_monitor_raw)
        except Exception:
            force_monitor = 2
        print(f"🖥️  FORCE_MONITOR activo: {force_monitor}")
    else:
        print("🖥️  FORCE_MONITOR no configurado; usando monitor 2 por defecto")

    capture = DXGICapture(force_monitor=force_monitor)
    gamestate_builder = GameStateBuilder()

    # Cargar ROIs (se inicializa con el primer frame real para evitar desalineaciones)
    rois = None
    resolution = None

    # Thread de captura
    def capture_thread():
        print("📸 Thread de captura iniciado")
        while True:
            frame = capture.capture()
            if frame is not None:
                nonlocal rois, resolution
                if rois is None or resolution is None:
                    resolution = (int(frame.shape[1]), int(frame.shape[0]))
                    rois_loaded, source_resolution = load_roi_config(resolution)
                    rois_loaded["_source_resolution"] = source_resolution
                    rois = rois_loaded
                try:
                    frame_queue.put_nowait(frame)
                except Exception:
                    pass  # Drop old frames
            time.sleep(0.1)  # ~10 FPS

    # Thread de visión
    def vision_thread():
        print("👁️  Thread de visión iniciado")
        while True:
            try:
                frame = frame_queue.get(timeout=1)
                if rois is None or resolution is None:
                    continue

                gamestate = gamestate_builder.update_from_frame(frame, rois, resolution)
                try:
                    gs_queue.put_nowait(gamestate)
                except Exception:
                    pass
            except Exception:
                continue

    # Thread de decisión (simplificado)
    def decision_thread():
        print("🧠 Thread de decisión iniciado")
        while True:
            try:
                gamestate = gs_queue.get(timeout=1)
                print(f"🎮 Estado: HP {gamestate.hp_current}, MP {gamestate.mp_current}")
                # Aquí iría el BehaviorTree.tick()
                # Por ahora, solo log
            except Exception:
                continue

    # Iniciar threads
    threads = [
        threading.Thread(target=capture_thread, daemon=True),
        threading.Thread(target=vision_thread, daemon=True),
        threading.Thread(target=decision_thread, daemon=True)
    ]

    for t in threads:
        t.start()

    print("✅ Bot ejecutándose. Presiona Ctrl+C para detener.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("🛑 Deteniendo bot...")
        print("✅ Bot detenido.")


if __name__ == "__main__":
    main()
