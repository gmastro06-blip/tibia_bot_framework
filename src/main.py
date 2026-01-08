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
from queue import Queue, Empty
import time
import json
from capture.dxgi_capture import DXGICapture
from gamestate.builder import GameStateBuilder
from runtime_config import RuntimeConfig
from decision.targeting import TargetSelector, format_target, Target
from navigation.route import load_route
from navigation.navigator import Navigator
from navigation.step_navigator import StepNavigator
from action.movement import MoveExecutor


def _toggle_transition_lines(
    healing_cfg,
    cavebot_cfg,
    last_healing_enabled: bool | None,
    last_cavebot_enabled: bool | None,
) -> tuple[list[str], bool | None, bool | None]:
    lines: list[str] = []

    if healing_cfg is not None:
        if last_healing_enabled is None or last_healing_enabled != healing_cfg.enabled:
            lines.append(f"🩹 Healing {'ON' if healing_cfg.enabled else 'OFF'}")
            last_healing_enabled = healing_cfg.enabled

    if cavebot_cfg is not None:
        if last_cavebot_enabled is None or last_cavebot_enabled != cavebot_cfg.enabled:
            lines.append(f"🧭 Cavebot {'ON' if cavebot_cfg.enabled else 'OFF'}")
            if cavebot_cfg.enabled:
                lines.append(f"🧭 Ruta: {cavebot_cfg.route_path}")
            last_cavebot_enabled = cavebot_cfg.enabled

    return lines, last_healing_enabled, last_cavebot_enabled

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


def run_bot(stop_event: threading.Event | None = None, runtime_config: RuntimeConfig | None = None) -> None:
    """Ejecuta el bot completo con pipeline threaded.

    Si `stop_event` se provee, el bot se detiene cuando el evento está seteado.
    Esto permite controlarlo desde una UI.
    """
    print("🚀 Iniciando Tibia Bot Framework...")

    if stop_event is None:
        stop_event = threading.Event()

    # Configurar queues
    frame_queue: Queue = Queue(maxsize=5)  # Capture → Vision
    gs_queue: Queue = Queue(maxsize=5)     # Vision → Decision

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
        while not stop_event.is_set():
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
        while not stop_event.is_set():
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
        last_healing_enabled: bool | None = None
        last_cavebot_enabled: bool | None = None
        selector = TargetSelector()
        last_target: Target | None = None
        bot_debug = os.getenv("BOT_DEBUG", "").strip().lower() in {"1", "true", "yes"}

        navigator: Navigator | None = None
        step_navigator: StepNavigator | None = None
        navigator_route_path: str | None = None
        mover = MoveExecutor(step_interval_s=float(os.getenv("CAVEBOT_STEP_INTERVAL_S", "0.35")))
        last_pos_warn_ts = 0.0
        while not stop_event.is_set():
            gamestate = None
            try:
                gamestate = gs_queue.get(timeout=1)
            except Empty:
                gamestate = None
            except Exception:
                continue

            healing_cfg, cavebot_cfg = (
                runtime_config.snapshot() if runtime_config is not None else (None, None)
            )

            # Log de cambios de toggles (para ver que aplica en tiempo real)
            lines, last_healing_enabled, last_cavebot_enabled = _toggle_transition_lines(
                healing_cfg,
                cavebot_cfg,
                last_healing_enabled,
                last_cavebot_enabled,
            )
            for line in lines:
                print(line)

            if gamestate is None:
                continue

            # Targeting de criaturas (si hay detecciones Roboflow).
            # Por defecto solo loggea cuando cambia el target.
            try:
                if resolution is not None:
                    new_target = selector.select_target(
                        gamestate.roboflow_boxes or [],
                        resolution,
                        prev=last_target,
                    )
                    if new_target != last_target:
                        last_target = new_target
                        print(f"🎯 Target: {format_target(last_target)}")
                    elif bot_debug and last_target is not None:
                        print(f"🎯 Target (sticky): {format_target(last_target)}")
            except Exception:
                pass

            print(f"🎮 Estado: HP {gamestate.hp_current}/{gamestate.hp_max}, MP {gamestate.mp_current}/{gamestate.mp_max}")

            # Cavebot básico (ruta + teclas) usando posición por env vars.
            if cavebot_cfg is not None and cavebot_cfg.enabled:
                cavebot_mode = os.getenv("CAVEBOT_MODE", "pos").strip().lower()
                # Reload route if needed
                if (navigator is None and step_navigator is None) or navigator_route_path != cavebot_cfg.route_path:
                    try:
                        route = load_route(cavebot_cfg.route_path)
                        navigator = None
                        step_navigator = None
                        if cavebot_mode in {"steps", "step"}:
                            step_navigator = StepNavigator(route)
                        else:
                            navigator = Navigator(route)
                        navigator_route_path = cavebot_cfg.route_path
                        print(f"🧭 Cavebot: ruta cargada ({len(route)} waypoints)")
                    except Exception as e:
                        navigator = None
                        step_navigator = None
                        navigator_route_path = None
                        print(f"🧭 Cavebot: no pude cargar ruta ({cavebot_cfg.route_path}): {e}")

                if step_navigator is not None:
                    try:
                        decision = step_navigator.decide()
                        if decision.reached_waypoint and decision.waypoint is not None:
                            wp = decision.waypoint
                            label = wp.name or f"({wp.x},{wp.y})"
                            if wp.action:
                                print(f"🧭 Waypoint: {label} action={wp.action}")
                            else:
                                print(f"🧭 Waypoint: {label}")

                        stepped = mover.maybe_step(decision.direction)
                        if bot_debug and decision.direction and not stepped:
                            print(f"🧭 Move (cooldown): {decision.direction}")
                    except Exception:
                        pass

                elif navigator is not None:
                    # Current position (temporary): PLAYER_X/PLAYER_Y
                    px_raw = os.getenv("PLAYER_X", "").strip()
                    py_raw = os.getenv("PLAYER_Y", "").strip()
                    pos = None
                    if px_raw and py_raw:
                        try:
                            pos = (int(px_raw), int(py_raw))
                        except Exception:
                            pos = None

                    if pos is None:
                        now = time.time()
                        if now - last_pos_warn_ts >= 5.0:
                            last_pos_warn_ts = now
                            print("🧭 Cavebot: setea PLAYER_X y PLAYER_Y o usa CAVEBOT_MODE=steps")
                    else:
                        try:
                            decision = navigator.decide(pos)
                            if decision.reached_waypoint and decision.waypoint is not None:
                                wp = decision.waypoint
                                label = wp.name or f"({wp.x},{wp.y})"
                                if wp.action:
                                    print(f"🧭 Waypoint: {label} action={wp.action}")
                                else:
                                    print(f"🧭 Waypoint: {label}")

                            stepped = mover.maybe_step(decision.direction)
                            if bot_debug and decision.direction and not stepped:
                                print(f"🧭 Move (cooldown): {decision.direction}")
                        except Exception:
                            pass

            # Healing en tiempo real (lógica mínima: solo decide + log)
            if healing_cfg is not None and healing_cfg.enabled:
                try:
                    if gamestate.hp_current is not None and gamestate.hp_max:
                        hp_pct = (gamestate.hp_current / gamestate.hp_max) * 100.0
                    else:
                        hp_pct = None

                    if gamestate.mp_current is not None and gamestate.mp_max:
                        mp_pct = (gamestate.mp_current / gamestate.mp_max) * 100.0
                    else:
                        mp_pct = None

                    trigger = False
                    if hp_pct is not None and hp_pct < float(healing_cfg.hp_below_pct):
                        trigger = True
                    if mp_pct is not None and mp_pct < float(healing_cfg.mp_below_pct):
                        trigger = True

                    if trigger:
                        action = healing_cfg.action.strip()
                        if action:
                            print(f"🩹 Healing TRIGGER ({action})")
                        else:
                            print("🩹 Healing TRIGGER")
                except Exception:
                    pass

            # Aquí iría el BehaviorTree.tick()
            # Por ahora, solo log

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
        while not stop_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        print("🛑 Deteniendo bot...")
        stop_event.set()

    # Esperar un poco a que los threads terminen
    try:
        for t in threads:
            try:
                t.join(timeout=2)
            except Exception:
                pass
    except KeyboardInterrupt:
        # Si el usuario presiona Ctrl+C durante el join, salir sin traceback.
        stop_event.set()

    print("✅ Bot detenido.")


if __name__ == "__main__":
    main()
