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
from decision.signals import evaluate_signals
from navigation.route import load_route
from navigation.navigator import Navigator
from navigation.step_navigator import StepNavigator

from telemetry.replay import ReplayRecorder, crop_named_rois, default_replay_roi_names
from telemetry.jsonl_logger import JsonlLogger
from telemetry.jsonl_writer import JsonlWriter



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

    def _put_drop_oldest(q: Queue, item) -> None:
        """Put non-blocking; if full, drop one oldest and try again."""
        try:
            q.put_nowait(item)
            return
        except Exception:
            pass
        try:
            q.get_nowait()
        except Exception:
            return
        try:
            q.put_nowait(item)
        except Exception:
            pass

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

    replay = ReplayRecorder()
    jsonl = JsonlLogger()
    jsonl_writer = JsonlWriter()

    replay_write_q: Queue = Queue(maxsize=10)
    jsonl_write_q: Queue = Queue(maxsize=100)

    def writer_thread() -> None:
        # Thread dedicado a I/O (evita jitter en visión/decisión)
        while not stop_event.is_set():
            did = False
            try:
                kind, payload = replay_write_q.get(timeout=0.05)
                did = True
                if kind == "replay":
                    out_dir, crops, data = payload
                    try:
                        replay.record_crops(out_dir=out_dir, crops=crops, payload=data)
                    except Exception:
                        pass
            except Empty:
                pass
            except Exception:
                pass

            try:
                while True:
                    kind, payload = jsonl_write_q.get_nowait()
                    did = True
                    if kind == "jsonl":
                        out_file, event = payload
                        try:
                            jsonl_writer.append(out_file=out_file, event=event)
                        except Exception:
                            pass
            except Exception:
                pass

            if not did:
                # Pequeño respiro
                try:
                    time.sleep(0.01)
                except Exception:
                    pass

    # Cargar ROIs (se inicializa con el primer frame real para evitar desalineaciones)
    rois = None
    resolution = None

    # Thread de captura
    def capture_thread():
        print("📸 Thread de captura iniciado")
        profile = os.getenv("BOT_PROFILE", "").strip().lower() in {"1", "true", "yes"}
        prof_every_s = 5.0
        last_prof = time.time()
        n_cap = 0
        cap_ms_sum = 0.0
        fps_raw = os.getenv("CAPTURE_FPS", "").strip()
        target_fps = 10.0
        if fps_raw:
            try:
                target_fps = max(1.0, float(fps_raw))
            except Exception:
                target_fps = 10.0
        target_period = 1.0 / max(1.0, target_fps)
        while not stop_event.is_set():
            t0 = time.time()
            frame = capture.capture()
            if profile:
                cap_ms_sum += (time.time() - t0) * 1000.0
                n_cap += 1
            if frame is not None:
                nonlocal rois, resolution
                if rois is None or resolution is None:
                    resolution = (int(frame.shape[1]), int(frame.shape[0]))
                    rois_loaded, source_resolution = load_roi_config(resolution)
                    rois_loaded["_source_resolution"] = source_resolution
                    rois = rois_loaded
                _put_drop_oldest(frame_queue, frame)

            # Sleep adaptativo (mantiene FPS objetivo sin spin)
            elapsed = time.time() - t0
            to_sleep = max(0.0, target_period - elapsed)
            if to_sleep:
                time.sleep(to_sleep)

            if profile:
                now = time.time()
                if now - last_prof >= prof_every_s and n_cap:
                    avg = cap_ms_sum / max(1, n_cap)
                    print(f"⏱️  capture avg {avg:.1f}ms ({n_cap} frames/{prof_every_s:.0f}s)")
                    last_prof = now
                    n_cap = 0
                    cap_ms_sum = 0.0

    # Thread de visión
    def vision_thread():
        print("👁️  Thread de visión iniciado")
        profile = os.getenv("BOT_PROFILE", "").strip().lower() in {"1", "true", "yes"}
        prof_every_s = 5.0
        last_prof = time.time()
        n_vis = 0
        vis_ms_sum = 0.0
        last_force_seen = 0
        while not stop_event.is_set():
            try:
                frame = frame_queue.get(timeout=1)
                # Drena frames viejos para procesar el más reciente (menor latencia)
                try:
                    while True:
                        frame = frame_queue.get_nowait()
                except Exception:
                    pass
                if rois is None or resolution is None:
                    continue

                t0 = time.time()
                gamestate = gamestate_builder.update_from_frame(frame, rois, resolution)
                if profile:
                    vis_ms_sum += (time.time() - t0) * 1000.0
                    n_vis += 1

                # Replay (ROI crops + JSON) - opt-in via UI config.
                if runtime_config is not None:
                    try:
                        rep_cfg = runtime_config.replay_snapshot()
                        force = False
                        try:
                            cur_force = runtime_config.replay_force_counter_snapshot()
                            force = cur_force != last_force_seen
                            if force:
                                last_force_seen = cur_force
                        except Exception:
                            force = False

                        should = replay.should_record(enabled=rep_cfg.enabled, interval_ms=rep_cfg.interval_ms)
                        if rep_cfg.enabled and (force or should):
                            crops = crop_named_rois(
                                frame,
                                roi_to_px=gamestate_builder.ocr_processor._roi_to_px,
                                rois=rois,
                                resolution=resolution,
                                names=default_replay_roi_names(),
                            )
                            tel = runtime_config.telemetry_snapshot()
                            payload = {
                                "ts": time.time(),
                                "resolution": list(resolution),
                                "gamestate": {
                                    "hp_current": getattr(gamestate, "hp_current", None),
                                    "hp_max": getattr(gamestate, "hp_max", None),
                                    "hp_pct": getattr(gamestate, "hp_pct", None),
                                    "mp_current": getattr(gamestate, "mp_current", None),
                                    "mp_max": getattr(gamestate, "mp_max", None),
                                    "mp_pct": getattr(gamestate, "mp_pct", None),
                                },
                                "telemetry": {
                                    "hp_pct": tel.hp_pct,
                                    "mp_pct": tel.mp_pct,
                                    "low_hp": tel.low_hp,
                                    "low_mp": tel.low_mp,
                                    "paralyzed": tel.paralyzed,
                                    "haste_active": tel.haste_active,
                                    "utamo_active": tel.utamo_active,
                                    "hungry": tel.hungry,
                                    "target": tel.target,
                                    "recommendation": tel.recommendation,
                                    "cavebot_next": tel.cavebot_next,
                                    "cavebot_waypoint": tel.cavebot_waypoint,
                                    "cavebot_action": tel.cavebot_action,
                                    "note": tel.note,
                                },
                                "forced": bool(force),
                            }
                            _put_drop_oldest(replay_write_q, ("replay", (rep_cfg.out_dir, crops, payload)))
                    except Exception:
                        pass

                try:
                    _put_drop_oldest(gs_queue, gamestate)
                except Exception:
                    pass

                if profile:
                    now = time.time()
                    if now - last_prof >= prof_every_s and n_vis:
                        avg = vis_ms_sum / max(1, n_vis)
                        print(f"⏱️  vision avg {avg:.1f}ms ({n_vis} updates/{prof_every_s:.0f}s)")
                        last_prof = now
                        n_vis = 0
                        vis_ms_sum = 0.0
            except Exception:
                continue

    # Thread de decisión (simplificado)
    def decision_thread():
        print("🧠 Thread de decisión iniciado")
        profile = os.getenv("BOT_PROFILE", "").strip().lower() in {"1", "true", "yes"}
        prof_every_s = 5.0
        last_prof = time.time()
        n_dec = 0
        dec_ms_sum = 0.0
        last_healing_enabled: bool | None = None
        last_cavebot_enabled: bool | None = None
        selector = TargetSelector()
        last_target: Target | None = None
        bot_debug = os.getenv("BOT_DEBUG", "").strip().lower() in {"1", "true", "yes"}

        navigator: Navigator | None = None
        step_navigator: StepNavigator | None = None
        navigator_route_path: str | None = None
        last_advance_seen = 0
        last_beep_ts = 0.0
        last_pos_warn_ts = 0.0
        last_gs_ts = 0.0
        last_event_target = ""
        last_event_reco = ""
        last_event_wp = ""
        last_event_action = ""
        last_event_flags: tuple[bool, bool, bool, bool, bool, bool] | None = None
        last_block_log_ts = 0.0
        while not stop_event.is_set():
            loop_t0 = time.time()
            gamestate = None
            try:
                gamestate = gs_queue.get(timeout=1)
                # Drena estados viejos para actuar sobre el más reciente
                try:
                    while True:
                        gamestate = gs_queue.get_nowait()
                except Exception:
                    pass
            except Empty:
                gamestate = None
            except Exception:
                continue

            healing_cfg, cavebot_cfg = (
                runtime_config.snapshot() if runtime_config is not None else (None, None)
            )

            sim_cfg = runtime_config.simulation_snapshot() if runtime_config is not None else None

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
                # Update a minimal telemetry note if no gamestate arrives for a while.
                if runtime_config is not None:
                    now = time.time()
                    if now - last_gs_ts >= 2.0:
                        last_gs_ts = now
                        try:
                            runtime_config.update_telemetry(note="Sin GameState (stale)")
                        except Exception:
                            pass
                continue

            last_gs_ts = time.time()

            # Señales derivadas + simulación (no ejecuta nada, solo computa flags)
            sig = None
            try:
                sig = evaluate_signals(gamestate, healing_cfg, sim_cfg)
            except Exception:
                sig = None

            # Targeting de criaturas (si hay detecciones Roboflow).
            # Por defecto solo loggea cuando cambia el target.
            target_str = ""
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
                    target_str = format_target(last_target)
            except Exception:
                pass

            try:
                hp_pct_str = "?"
                mp_pct_str = "?"
                if sig is not None and sig.hp_pct is not None:
                    hp_pct_str = f"{sig.hp_pct:.1f}%"
                if sig is not None and sig.mp_pct is not None:
                    mp_pct_str = f"{sig.mp_pct:.1f}%"

                print(
                    f"🎮 Estado: HP {getattr(gamestate, 'hp_current', None)}/{getattr(gamestate, 'hp_max', None)} ({hp_pct_str}), "
                    f"MP {getattr(gamestate, 'mp_current', None)}/{getattr(gamestate, 'mp_max', None)} ({mp_pct_str})"
                )
            except Exception:
                print(
                    f"🎮 Estado: HP {getattr(gamestate, 'hp_current', None)}/{getattr(gamestate, 'hp_max', None)}, "
                    f"MP {getattr(gamestate, 'mp_current', None)}/{getattr(gamestate, 'mp_max', None)}"
                )

            # Cavebot básico (ruta + teclas) usando posición por env vars.
            cavebot_next = ""
            cavebot_waypoint = ""
            cavebot_action = ""
            assistant_cfg = runtime_config.assistant_snapshot() if runtime_config is not None else None

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
                        # Modo asistente: preview constante, pero solo consume/avanza con confirmación.
                        decision = step_navigator.preview()

                        should_advance = True
                        if assistant_cfg is not None and assistant_cfg.enabled and assistant_cfg.confirm_actions:
                            cur_adv = runtime_config.advance_counter_snapshot() if runtime_config is not None else 0
                            should_advance = cur_adv != last_advance_seen
                            if should_advance:
                                last_advance_seen = cur_adv

                        if should_advance:
                            decision = step_navigator.decide()

                        if decision.reached_waypoint and decision.waypoint is not None:
                            wp = decision.waypoint
                            label = wp.name or f"({wp.x},{wp.y})"
                            if wp.action:
                                print(f"🧭 Waypoint: {label} action={wp.action}")
                            else:
                                print(f"🧭 Waypoint: {label}")

                        if decision.waypoint is not None:
                            wp = decision.waypoint
                            cavebot_waypoint = wp.name or f"({wp.x},{wp.y})"
                            cavebot_action = wp.action or ""

                        if decision.direction:
                            cavebot_next = f"{decision.direction}"
                        elif decision.reached_waypoint and decision.waypoint is not None:
                            wp = decision.waypoint
                            label = wp.name or f"({wp.x},{wp.y})"
                            cavebot_next = f"reached {label}"
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
                            blocked_abs = None
                            try:
                                # If A* pathfinding is enabled, feed dynamic blockers.
                                if getattr(navigator, "pathfind_mode", "") in {"astar", "a*"}:
                                    offs = getattr(gamestate, "viewport_tile_offsets", None)
                                    if offs:
                                        blocked_abs = {(pos[0] + int(dx), pos[1] + int(dy)) for dx, dy in offs}

                                    # Debug observability (rate-limited): show what we feed into A*.
                                    if bot_debug:
                                        now = time.time()
                                        if now - last_block_log_ts >= 2.0:
                                            last_block_log_ts = now
                                            n_offs = len(offs) if offs else 0
                                            sample = []
                                            try:
                                                if offs:
                                                    # stable-ish sample: nearest first
                                                    sample = sorted(offs, key=lambda p: (abs(int(p[0])) + abs(int(p[1])), int(p[0]), int(p[1])))[:5]
                                            except Exception:
                                                sample = []
                                            print(f"🧱 A* blockers: {n_offs} sample={sample}")
                            except Exception:
                                blocked_abs = None

                            decision = navigator.decide(pos, blocked=blocked_abs)
                            if decision.reached_waypoint and decision.waypoint is not None:
                                wp = decision.waypoint
                                label = wp.name or f"({wp.x},{wp.y})"
                                if wp.action:
                                    print(f"🧭 Waypoint: {label} action={wp.action}")
                                else:
                                    print(f"🧭 Waypoint: {label}")

                            if decision.waypoint is not None:
                                wp = decision.waypoint
                                cavebot_waypoint = wp.name or f"({wp.x},{wp.y})"
                                cavebot_action = wp.action or ""

                            if decision.direction:
                                cavebot_next = f"{decision.direction}"
                            elif decision.reached_waypoint and decision.waypoint is not None:
                                wp = decision.waypoint
                                label = wp.name or f"({wp.x},{wp.y})"
                                cavebot_next = f"reached {label}"
                        except Exception:
                            pass

            # Recomendación humana (sin ejecutar inputs)
            recommendation = ""
            if sig is not None and healing_cfg is not None and healing_cfg.enabled and sig.healing_trigger:
                action = (healing_cfg.action or "").strip()
                recommendation = f"heal: {action}" if action else "heal"
            if cavebot_next:
                if recommendation:
                    recommendation = f"{recommendation} | move: {cavebot_next}"
                else:
                    recommendation = f"move: {cavebot_next}"

            # Alertas sonoras (solo asistente)
            try:
                if assistant_cfg is not None and assistant_cfg.enabled and assistant_cfg.sound_alerts:
                    danger = False
                    if sig is not None and (sig.low_hp or sig.paralyzed):
                        danger = True

                    if danger:
                        now = time.time()
                        if now - last_beep_ts >= 1.0:
                            last_beep_ts = now
                            try:
                                import winsound

                                winsound.Beep(880, 140)
                            except Exception:
                                try:
                                    print("\a", end="")
                                except Exception:
                                    pass
            except Exception:
                pass

            # Publicar telemetría para UI (si existe RuntimeConfig)
            if runtime_config is not None and sig is not None:
                try:
                    runtime_config.update_telemetry(
                        hp_current=sig.hp_current,
                        hp_max=sig.hp_max,
                        hp_pct=sig.hp_pct,
                        mp_current=sig.mp_current,
                        mp_max=sig.mp_max,
                        mp_pct=sig.mp_pct,
                        low_hp=sig.low_hp,
                        low_mp=sig.low_mp,
                        paralyzed=sig.paralyzed,
                        haste_active=sig.haste_active,
                        utamo_active=sig.utamo_active,
                        hungry=sig.hungry,
                        target=target_str,
                        recommendation=recommendation,
                        cavebot_next=cavebot_next,
                        cavebot_waypoint=cavebot_waypoint,
                        cavebot_action=cavebot_action,
                        note="",
                    )
                except Exception:
                    pass

            # Export JSONL de eventos (opt-in): cambios relevantes.
            if runtime_config is not None and sig is not None:
                try:
                    log_cfg = runtime_config.logging_snapshot()
                    if log_cfg.enabled:
                        flags = (
                            bool(sig.low_hp),
                            bool(sig.low_mp),
                            bool(sig.paralyzed),
                            bool(sig.haste_active),
                            bool(sig.utamo_active),
                            bool(sig.hungry),
                        )

                        def emit(kind: str, data: dict) -> None:
                            try:
                                _put_drop_oldest(jsonl_write_q, ("jsonl", (log_cfg.out_file, {"kind": kind, **data})))
                            except Exception:
                                pass

                        if target_str != last_event_target:
                            emit("event.target", {"target": target_str})
                            last_event_target = target_str

                        if recommendation != last_event_reco:
                            emit("event.recommendation", {"recommendation": recommendation})
                            last_event_reco = recommendation

                        if cavebot_waypoint != last_event_wp or cavebot_action != last_event_action:
                            emit(
                                "event.cavebot",
                                {"cavebot_waypoint": cavebot_waypoint, "cavebot_action": cavebot_action, "cavebot_next": cavebot_next},
                            )
                            last_event_wp = cavebot_waypoint
                            last_event_action = cavebot_action

                        if last_event_flags is None or flags != last_event_flags:
                            emit(
                                "event.flags",
                                {
                                    "low_hp": flags[0],
                                    "low_mp": flags[1],
                                    "paralyzed": flags[2],
                                    "haste_active": flags[3],
                                    "utamo_active": flags[4],
                                    "hungry": flags[5],
                                },
                            )
                            last_event_flags = flags
                except Exception:
                    pass

            # Export JSONL de telemetría (opt-in).
            if runtime_config is not None:
                try:
                    log_cfg = runtime_config.logging_snapshot()
                    if jsonl.should_log(enabled=log_cfg.enabled, interval_ms=log_cfg.interval_ms):
                        tel = runtime_config.telemetry_snapshot()
                        _put_drop_oldest(
                            jsonl_write_q,
                            (
                                "jsonl",
                                (
                                    log_cfg.out_file,
                                    {
                                        "kind": "telemetry",
                                        "hp_current": tel.hp_current,
                                        "hp_max": tel.hp_max,
                                        "hp_pct": tel.hp_pct,
                                        "mp_current": tel.mp_current,
                                        "mp_max": tel.mp_max,
                                        "mp_pct": tel.mp_pct,
                                        "low_hp": tel.low_hp,
                                        "low_mp": tel.low_mp,
                                        "paralyzed": tel.paralyzed,
                                        "haste_active": tel.haste_active,
                                        "utamo_active": tel.utamo_active,
                                        "hungry": tel.hungry,
                                        "target": tel.target,
                                        "recommendation": tel.recommendation,
                                        "cavebot_next": tel.cavebot_next,
                                        "cavebot_waypoint": tel.cavebot_waypoint,
                                        "cavebot_action": tel.cavebot_action,
                                        "note": tel.note,
                                    },
                                ),
                            ),
                        )
                except Exception:
                    pass

            # Healing en tiempo real (lógica mínima: solo decide + log)
            if sig is not None and healing_cfg is not None and healing_cfg.enabled:
                try:
                    if sig.healing_trigger:
                        action = (healing_cfg.action or "").strip()
                        if action:
                            print(f"🩹 Healing TRIGGER ({action})")
                        else:
                            print("🩹 Healing TRIGGER")
                except Exception:
                    pass

            # Aquí iría el BehaviorTree.tick()
            # Por ahora, solo log

            if profile:
                dec_ms_sum += (time.time() - loop_t0) * 1000.0
                n_dec += 1
                now = time.time()
                if now - last_prof >= prof_every_s and n_dec:
                    avg = dec_ms_sum / max(1, n_dec)
                    print(f"⏱️  decision avg {avg:.1f}ms ({n_dec} loops/{prof_every_s:.0f}s)")
                    last_prof = now
                    n_dec = 0
                    dec_ms_sum = 0.0

    # Iniciar threads
    threads = [
        threading.Thread(target=writer_thread, daemon=True),
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
