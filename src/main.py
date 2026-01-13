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
from decision.scheduler import PeriodicTrigger
from decision.waypoint_actions import build_requests_from_waypoint_action, evaluate_waypoint_requirements
from navigation.route import load_route
from navigation.navigator import Navigator
from navigation.step_navigator import StepNavigator

from telemetry.replay import ReplayRecorder, crop_named_rois, default_replay_roi_names, env_replay_enabled
from telemetry.overlay_export import OverlayExporter, overlay_config_from_env
from telemetry.jsonl_logger import JsonlLogger
from telemetry.jsonl_writer import JsonlWriter
from vision.anchor_tracker import AnchorTracker
from vision.viewport_tracker import ViewportTracker

try:
    from console_sanitize import maybe_install_no_emoji_output

    maybe_install_no_emoji_output()
except Exception:
    # Never break the bot due to console/output customization.
    pass

# Assistant-first design: no real input injection.
# If you want to test “actions we would take”, use a mock driver:
#   from action.input_driver import MockInputDriver, ActionRequest
# and record/log ActionRequest objects instead of sending OS/game inputs.



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

    # Optional override: allow selecting a specific ROI config file.
    # Useful when the HUD/bars were moved and you keep multiple profiles.
    # Examples:
    #   $env:ROIS_CONFIG = 'configs/rois_guess_1920x1080_mi_layout.json'
    #   $env:ROIS_CONFIG = 'data/ROIs_resueltos.json'
    rois_override = os.getenv("ROIS_CONFIG", "").strip()
    if rois_override:
        # Support relative paths from repo root.
        cand = rois_override
        try:
            if not os.path.isabs(cand):
                cand = os.path.join(project_root, cand)
        except Exception:
            cand = rois_override
        try:
            with open(cand, "r") as f:
                config = json.load(f)
                print(f"Configuración ROIs cargada desde override {rois_override}")
                return config.get("rois_guess_norm", config.get("rois", config)), config.get(
                    "source_resolution", [width, height]
                )
        except Exception as e:
            print(f"ROIS_CONFIG inválido ({rois_override}): {e}. Usando configuración por resolución.")

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
            "coords_ocr": {"x": 0.909668, "y": 0.108000, "w": 0.070000, "h": 0.025000},
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
    try:
        from console_sanitize import maybe_install_no_emoji_output

        maybe_install_no_emoji_output()
    except Exception:
        pass

    print("🚀 Iniciando Tibia Bot Framework...")

    if stop_event is None:
        stop_event = threading.Event()

    # Configurar queues
    frame_queue: Queue = Queue(maxsize=5)  # Capture → Vision
    gs_queue: Queue = Queue(maxsize=5)     # Vision → Decision

    # Salud del pipeline (stability/observability). Mantenerlo simple y barato.
    health_lock = threading.Lock()
    health: dict[str, float] = {
        "start_ts": time.time(),
        "last_frame_ts": 0.0,
        "last_gs_ts": 0.0,
        "last_dec_ts": 0.0,
        "capture_ok": 0.0,
        "capture_none": 0.0,
        "vision_ok": 0.0,
        "vision_ex": 0.0,
        "decision_ok": 0.0,
        "decision_ex": 0.0,
        "drop_frame_queue": 0.0,
        "drop_gs_queue": 0.0,
        "drop_replay_queue": 0.0,
        "drop_jsonl_queue": 0.0,
        "capture_ms_last": 0.0,
        "vision_ms_last": 0.0,
        "decision_ms_last": 0.0,
    }

    def _h_set(key: str, value: float) -> None:
        try:
            with health_lock:
                health[key] = float(value)
        except Exception:
            pass

    def _h_inc(key: str, delta: float = 1.0) -> None:
        try:
            with health_lock:
                health[key] = float(health.get(key, 0.0)) + float(delta)
        except Exception:
            pass

    def _h_snapshot() -> dict[str, float]:
        try:
            with health_lock:
                return dict(health)
        except Exception:
            return dict(health)

    def _put_drop_oldest(q: Queue, item, *, drop_key: str | None = None) -> None:
        """Put non-blocking; if full, drop one oldest and try again."""
        try:
            q.put_nowait(item)
            return
        except Exception:
            pass

        if drop_key:
            _h_inc(drop_key)
        try:
            q.get_nowait()
        except Exception:
            return
        try:
            q.put_nowait(item)
        except Exception:
            pass

    # Cross-thread correlation: latest planned/committed ActionRequest summary.
    # Vision (overlay/replay) can read this even when there's no RuntimeConfig/UI.
    action_lock = threading.Lock()
    action_state: dict[str, float | str | bool] = {
        "ts": 0.0,
        "action_request": "",
        "action_committed": False,
        "input_plan": "",
    }

    def _a_set(action_request: str, action_committed: bool, input_plan: str = "") -> None:
        try:
            with action_lock:
                action_state["ts"] = float(time.time())
                action_state["action_request"] = str(action_request or "")
                action_state["action_committed"] = bool(action_committed)
                action_state["input_plan"] = str(input_plan or "")
        except Exception:
            pass

    def _a_snapshot() -> tuple[str, bool, float, str]:
        try:
            with action_lock:
                return (
                    str(action_state.get("action_request", "") or ""),
                    bool(action_state.get("action_committed", False)),
                    float(action_state.get("ts", 0.0) or 0.0),
                    str(action_state.get("input_plan", "") or ""),
                )
        except Exception:
            return "", False, 0.0, ""

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

    anchor_tracker = AnchorTracker()
    viewport_tracker = ViewportTracker()

    replay = ReplayRecorder()
    overlay = OverlayExporter(overlay_config_from_env())
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
            _h_set("capture_ms_last", (time.time() - t0) * 1000.0)
            if profile:
                cap_ms_sum += (time.time() - t0) * 1000.0
                n_cap += 1
            if frame is not None:
                _h_inc("capture_ok")
                _h_set("last_frame_ts", time.time())
                nonlocal rois, resolution
                if rois is None or resolution is None:
                    resolution = (int(frame.shape[1]), int(frame.shape[0]))
                    rois_loaded, source_resolution = load_roi_config(resolution)
                    rois_loaded["_source_resolution"] = source_resolution
                    rois = rois_loaded
                _put_drop_oldest(frame_queue, frame, drop_key="drop_frame_queue")
            else:
                _h_inc("capture_none")

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

        # Env-based replay config (used when no RuntimeConfig/UI is attached).
        rep_enabled_env = env_replay_enabled()
        try:
            rep_interval_ms_env = int(float(os.getenv("REPLAY_INTERVAL_MS", "2000").strip() or "2000"))
        except Exception:
            rep_interval_ms_env = 2000
        rep_out_dir_env = os.getenv("REPLAY_OUT_DIR", "logs/replay").strip() or "logs/replay"
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

                # Auto-align ROIs if an anchor is configured (handles HUD moves).
                try:
                    anchor_tracker.maybe_update(
                        frame=frame,
                        rois=rois,
                        resolution=resolution,
                        roi_to_px=gamestate_builder.ocr_processor._roi_to_px,
                    )
                except Exception:
                    pass

                # Auto-adjust game_viewport if side panels/bars change.
                try:
                    viewport_tracker.maybe_update(
                        frame=frame,
                        rois=rois,
                        resolution=resolution,
                        roi_to_px=gamestate_builder.ocr_processor._roi_to_px,
                    )
                except Exception:
                    pass

                t0 = time.time()
                gamestate = gamestate_builder.update_from_frame(frame, rois, resolution)
                _h_set("vision_ms_last", (time.time() - t0) * 1000.0)
                _h_inc("vision_ok")
                _h_set("last_gs_ts", time.time())
                if profile:
                    vis_ms_sum += (time.time() - t0) * 1000.0
                    n_vis += 1

                # Replay (ROI crops + JSON) - opt-in via UI config.
                try:
                    # Determine replay config source.
                    if runtime_config is not None:
                        rep_cfg = runtime_config.replay_snapshot()
                        enabled = bool(rep_cfg.enabled)
                        interval_ms = int(rep_cfg.interval_ms)
                        out_dir = str(rep_cfg.out_dir)
                        force = False
                        try:
                            cur_force = runtime_config.replay_force_counter_snapshot()
                            force = cur_force != last_force_seen
                            if force:
                                last_force_seen = cur_force
                        except Exception:
                            force = False
                    else:
                        enabled = bool(rep_enabled_env)
                        interval_ms = int(rep_interval_ms_env)
                        out_dir = str(rep_out_dir_env)
                        force = False

                    should = replay.should_record(enabled=enabled, interval_ms=interval_ms)
                    if enabled and (force or should):
                        crops = crop_named_rois(
                            frame,
                            roi_to_px=gamestate_builder.ocr_processor._roi_to_px,
                            rois=rois,
                            resolution=resolution,
                            names=default_replay_roi_names(),
                        )

                        # Use UI telemetry when available; otherwise record a minimal payload.
                        if runtime_config is not None:
                            tel = runtime_config.telemetry_snapshot()
                            tel_payload = {
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
                                "action_request": getattr(tel, "action_request", ""),
                                "action_committed": bool(getattr(tel, "action_committed", False)),
                                "note": tel.note,
                            }
                        else:
                            tel_payload = {}

                        # Always attach latest action request for end-to-end correlation.
                        try:
                            ar, ac, ats, ip = _a_snapshot()
                            if ar:
                                tel_payload["action_request"] = ar
                                tel_payload["action_committed"] = bool(ac)
                                tel_payload["action_ts"] = float(ats)
                                if ip:
                                    tel_payload["input_plan"] = str(ip)
                        except Exception:
                            pass

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
                                "cap_current": getattr(gamestate, "cap_current", None),
                                "pos_x": getattr(gamestate, "pos_x", None),
                                "pos_y": getattr(gamestate, "pos_y", None),
                                "pos_z": getattr(gamestate, "pos_z", None),
                            },
                            "rois_state": {
                                "roi_offset_px": rois.get("_roi_offset_px") if isinstance(rois, dict) else None,
                                "roi_offset_score": rois.get("_roi_offset_score") if isinstance(rois, dict) else None,
                                "viewport_auto": rois.get("_viewport_auto") if isinstance(rois, dict) else None,
                                "game_viewport": rois.get("game_viewport") if isinstance(rois, dict) else None,
                            },
                            "telemetry": tel_payload,
                            "forced": bool(force),
                        }
                        _put_drop_oldest(replay_write_q, ("replay", (out_dir, crops, payload)), drop_key="drop_replay_queue")
                except Exception:
                    pass

                # Debug overlay exporter (writes annotated frames) - opt-in via env.
                try:
                    viewport_rect = None
                    try:
                        if rois is not None and resolution is not None and "game_viewport" in rois:
                            x, y, w, h = gamestate_builder.ocr_processor._roi_to_px(
                                frame, rois, resolution, rois["game_viewport"]
                            )
                            viewport_rect = (int(x), int(y), int(w), int(h))
                    except Exception:
                        viewport_rect = None

                    boxes = getattr(gamestate, "roboflow_boxes", None)
                    blocked = getattr(gamestate, "viewport_tile_offsets", None)

                    target_label = ""
                    try:
                        if runtime_config is not None:
                            target_label = str(runtime_config.telemetry_snapshot().target or "")
                    except Exception:
                        target_label = ""

                    # Extra overlay diagnostics (optional, env-controlled).
                    info_lines: list[str] = []
                    roi_rects: list[tuple[str, tuple[int, int, int, int]]] = []
                    try:
                        # ROIs to draw (comma-separated). Defaults are safe + useful.
                        raw = (os.getenv("OVERLAY_ROIS", "") or "").strip()
                        if raw:
                            roi_names = [s.strip() for s in raw.split(",") if s.strip()]
                        else:
                            roi_names = [
                                "coords_ocr",
                                "minimap_content",
                                "hp_top_ocr",
                                "mp_top_ocr",
                                "hp_low_bar",
                                "mp_low_bar",
                                "states_icons",
                                "equipment_slots",
                                "battlelist_rows",
                            ]

                        if isinstance(rois, dict) and resolution is not None:
                            for name in roi_names:
                                if name not in rois:
                                    continue
                                try:
                                    rx, ry, rw, rh = gamestate_builder.ocr_processor._roi_to_px(
                                        frame, rois, resolution, rois[name]
                                    )
                                    roi_rects.append((str(name), (int(rx), int(ry), int(rw), int(rh))))
                                except Exception:
                                    continue
                    except Exception:
                        roi_rects = []

                    try:
                        if isinstance(rois, dict):
                            off = rois.get("_roi_offset_px")
                            score = rois.get("_roi_offset_score")
                            if isinstance(off, (list, tuple)) and len(off) >= 2:
                                try:
                                    s_score = "" if score is None else f" score={float(score):.3f}"
                                except Exception:
                                    s_score = ""
                                info_lines.append(
                                    f"roi_offset_px=({float(off[0]):.1f},{float(off[1]):.1f}){s_score}"
                                )

                            vauto = rois.get("_viewport_auto")
                            if isinstance(vauto, dict):
                                method = str(vauto.get("method", ""))
                                frozen = bool(vauto.get("frozen", False))
                                conf = vauto.get("confidence", None)
                                try:
                                    conf_s = "" if conf is None else f" conf={float(conf):.2f}"
                                except Exception:
                                    conf_s = ""
                                info_lines.append(f"viewport_auto={method}{conf_s} frozen={int(frozen)}")
                    except Exception:
                        pass

                    # Minimap debug from GameState (if present).
                    try:
                        mm_mode = getattr(gamestate, "minimap_mode_used", None)
                        mm_resp = getattr(gamestate, "minimap_response", None)
                        mm_dx = getattr(gamestate, "minimap_delta_dx", None)
                        mm_dy = getattr(gamestate, "minimap_delta_dy", None)
                        mm_ax = getattr(gamestate, "minimap_acc_dx", None)
                        mm_ay = getattr(gamestate, "minimap_acc_dy", None)
                        if any(v is not None for v in [mm_mode, mm_resp, mm_dx, mm_dy, mm_ax, mm_ay]):
                            try:
                                info_lines.append(
                                    "minimap "
                                    f"mode={mm_mode or ''} "
                                    f"resp={'' if mm_resp is None else f'{float(mm_resp):.2f}'} "
                                    f"d=({'' if mm_dx is None else f'{float(mm_dx):.2f}'},{'' if mm_dy is None else f'{float(mm_dy):.2f}'}) "
                                    f"acc=({'' if mm_ax is None else f'{float(mm_ax):.1f}'},{'' if mm_ay is None else f'{float(mm_ay):.1f}'})"
                                )
                            except Exception:
                                pass
                    except Exception:
                        pass

                    # Coords/provider snapshot.
                    try:
                        cp = getattr(gamestate, "coords_provider", None)
                        gx = getattr(gamestate, "pos_x", None)
                        gy = getattr(gamestate, "pos_y", None)
                        gz = getattr(gamestate, "pos_z", None)
                        pos_s = ""
                        if gx is not None and gy is not None:
                            pos_s = f" pos=({int(gx)},{int(gy)}" + (f",{int(gz)}" if gz is not None else "") + ")"
                        if cp or pos_s:
                            info_lines.append(f"coords_provider={cp or ''}{pos_s}")
                    except Exception:
                        pass

                    # Action correlation (planned vs committed), from decision thread.
                    try:
                        ar, ac, _ats, ip = _a_snapshot()
                        if ar:
                            s = str(ar)
                            if len(s) > 160:
                                s = s[:157] + "..."
                            info_lines.append(f"action={'*' if bool(ac) else ''}{s}")
                        if ip:
                            s2 = str(ip)
                            if len(s2) > 160:
                                s2 = s2[:157] + "..."
                            info_lines.append(f"inputs={s2}")
                    except Exception:
                        pass

                    overlay.maybe_export(
                        frame,
                        viewport_rect=viewport_rect,
                        boxes=boxes,
                        blocked_offsets=blocked,
                        target_label=target_label,
                        info_lines=info_lines,
                        roi_rects=roi_rects,
                    )
                except Exception:
                    pass

                try:
                    _put_drop_oldest(gs_queue, gamestate, drop_key="drop_gs_queue")
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
                _h_inc("vision_ex")
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

        # Optional coordinate recording (for building cavebot routes).
        record_route_path = os.getenv("RECORD_ROUTE_PATH", "").strip()
        record_route_enabled = os.getenv("RECORD_ROUTE_ENABLED", "").strip().lower() in {"1", "true", "yes"}
        record_samples_path = os.getenv("RECORD_ROUTE_SAMPLES_PATH", "").strip()
        route_recorder = None
        if record_route_enabled and record_route_path:
            try:
                from navigation.coords_recorder import CoordsRecorder

                try:
                    rec_min_manhattan = int(os.getenv("RECORD_ROUTE_MIN_MANHATTAN", "1").strip() or "1")
                except Exception:
                    rec_min_manhattan = 1
                try:
                    rec_min_interval = float(os.getenv("RECORD_ROUTE_MIN_INTERVAL_S", "0").strip() or "0")
                except Exception:
                    rec_min_interval = 0.0

                route_recorder = CoordsRecorder(
                    min_manhattan=rec_min_manhattan,
                    min_interval_s=rec_min_interval,
                )
                print(f"🧾 Recording route to: {record_route_path}")
                if record_samples_path:
                    print(f"🧾 Recording samples JSONL to: {record_samples_path}")
            except Exception:
                route_recorder = None

        # Cap leave check (assistant recommendation).
        cap_leave_enabled = os.getenv("CAP_LEAVE_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
        try:
            cap_leave_threshold = int(float(os.getenv("CAP_LEAVE_THRESHOLD", "50").strip() or "50"))
        except Exception:
            cap_leave_threshold = 50

        # Supplies (assistant-only, manual operator inputs via UI/env).
        potions_stop_enabled = os.getenv("POTIONS_STOP_ENABLED", "0").strip().lower() in {"1", "true", "yes"}
        try:
            potions_min_threshold = int(float(os.getenv("POTIONS_MIN", "0").strip() or "0"))
        except Exception:
            potions_min_threshold = 0

        navigator: Navigator | None = None
        step_navigator: StepNavigator | None = None
        navigator_route_path: str | None = None
        last_advance_seen = 0
        last_step_jump_seen = 0
        last_beep_ts = 0.0
        last_pos_warn_ts = 0.0
        # Idle detection (safe): warn/beep when position doesn't change for a while.
        try:
            idle_alert_s = float(os.getenv("ASSIST_IDLE_ALERT_S", "0").strip() or "0")
        except Exception:
            idle_alert_s = 0.0
        idle_alert_s = max(0.0, float(idle_alert_s))
        try:
            idle_repeat_s = float(os.getenv("ASSIST_IDLE_REPEAT_S", "10").strip() or "10")
        except Exception:
            idle_repeat_s = 10.0
        idle_repeat_s = max(1.0, float(idle_repeat_s))

        # Assistant-only recovery policy (no input injection): when the cavebot is
        # trying to move but the position doesn't change for a while, suggest a
        # perpendicular sidestep for a few attempts.
        recovery_enabled = os.getenv("ASSIST_RECOVERY_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
        try:
            recovery_idle_s = float(os.getenv("ASSIST_RECOVERY_IDLE_S", "").strip() or "0")
        except Exception:
            recovery_idle_s = 0.0
        if recovery_idle_s <= 0.0:
            # Default: piggyback on idle detection threshold if configured.
            recovery_idle_s = float(idle_alert_s) if float(idle_alert_s) > 0.0 else 8.0
        try:
            recovery_min_interval_s = float(os.getenv("ASSIST_RECOVERY_MIN_INTERVAL_S", "1").strip() or "1")
        except Exception:
            recovery_min_interval_s = 1.0
        recovery_min_interval_s = max(0.1, float(recovery_min_interval_s))
        try:
            recovery_max_attempts = int(float(os.getenv("ASSIST_RECOVERY_MAX_ATTEMPTS", "4").strip() or "4"))
        except Exception:
            recovery_max_attempts = 4
        recovery_max_attempts = max(1, int(recovery_max_attempts))
        recovery_stop_on_fail = os.getenv("ASSIST_RECOVERY_STOP_ON_FAIL", "0").strip().lower() in {"1", "true", "yes"}

        recovery_attempts = 0
        recovery_last_pos_seen: tuple[int, int, int | None] | None = None
        recovery_last_applied_ts = 0.0
        last_pos_key: tuple[int, int, int | None] | None = None
        last_pos_change_ts = 0.0
        last_idle_warn_ts = 0.0
        last_idle_beep_ts = 0.0
        last_gs_ts = 0.0
        # Coords confidence (OCR can jitter): keep last 'good' coords to compute jumps.
        last_good_coords: tuple[int, int, int | None] | None = None

        # Coords source/provider (affects diagnostics).
        coords_provider = (os.getenv("COORDS_PROVIDER", "ocr") or "ocr").strip().lower()
        coords_provider_disabled = coords_provider in {"disabled", "none", "off", "0", "false", "no"}
        coords_provider_minimap = coords_provider in {"minimap", "map", "minimap_motion"}
        last_coords_warn_ts = 0.0
        try:
            coords_warn_jump = int(float(os.getenv("ASSIST_COORDS_WARN_JUMP", "6").strip() or "6"))
        except Exception:
            coords_warn_jump = 6
        try:
            coords_fail_jump = int(float(os.getenv("ASSIST_COORDS_FAIL_JUMP", "25").strip() or "25"))
        except Exception:
            coords_fail_jump = 25
        coords_warn_jump = max(1, int(coords_warn_jump))
        coords_fail_jump = max(coords_warn_jump, int(coords_fail_jump))
        last_event_target = ""
        last_event_reco = ""
        last_event_wp = ""
        last_event_action = ""
        last_event_action_req = ""
        last_event_action_committed: bool | None = None
        last_event_flags: tuple[bool, bool, bool, bool, bool, bool] | None = None
        last_event_note = ""
        last_block_log_ts = 0.0

        # Env-based JSONL logging (used when no RuntimeConfig/UI is attached).
        log_enabled_env_raw = os.getenv("LOG_JSONL_ENABLED", "").strip().lower()
        if not log_enabled_env_raw:
            log_enabled_env_raw = os.getenv("LOG_ENABLED", "").strip().lower()
        log_enabled_env = log_enabled_env_raw in {"1", "true", "yes"}
        try:
            log_interval_ms_env = int(float(os.getenv("LOG_JSONL_INTERVAL_MS", "250").strip() or "250"))
        except Exception:
            log_interval_ms_env = 250
        log_out_file_env = os.getenv("LOG_JSONL_OUT_FILE", "logs/telemetry.jsonl").strip() or "logs/telemetry.jsonl"

        # Safe action sink (records what we'd do, no real input injection).
        try:
            from action.input_driver import ActionRequest, MockInputDriver

            mock_driver = MockInputDriver(max_items=500)
        except Exception:
            ActionRequest = None  # type: ignore[assignment]
            mock_driver = None

        # Optional BehaviorTree planner (assistant-only). Defaults ON but is fail-safe.
        bt_runner = None
        try:
            from decision.behavior_tree import BehaviorTreeRunner

            if BehaviorTreeRunner.enabled_from_env():
                bt_runner = BehaviorTreeRunner()
        except Exception:
            bt_runner = None

        # Periodic maintenance (assistant mode) e.g. eat food.
        try:
            food_interval_s = float(os.getenv("ASSIST_EAT_FOOD_INTERVAL_S", "0").strip() or "0")
        except Exception:
            food_interval_s = 0.0
        food_trigger = PeriodicTrigger(interval_s=max(0.0, food_interval_s))

        while not stop_event.is_set():
            loop_t0 = time.time()
            _h_set("last_dec_ts", loop_t0)
            # Always-initialized per-tick flags (avoid possibly-unbound locals).
            should_advance = False
            commit_flag = False
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
            assistant_cfg = runtime_config.assistant_snapshot() if runtime_config is not None else None

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
                            runtime_config.update_telemetry(
                                note="⛔ Stuck: STALE_GS | sin GameState",
                                stuck_reason="STALE_GS",
                                stuck_idle_s=0.0,
                                stuck_blockers=0,
                                stuck_extra="",
                            )
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

            # Anti-stuck inputs-free: track last position change and compute idle duration.
            note_out = ""
            pos_key: tuple[int, int, int | None] | None = None
            idle_for_s: float | None = None
            coords_status = ""
            coords_jump = 0
            # Structured stuck fields (sent to UI regardless of note throttling)
            stuck_reason_tick = ""
            stuck_idle_s_tick = 0.0
            stuck_blockers_tick = 0
            stuck_extra_tick = ""
            now = time.time()
            try:
                gx = getattr(gamestate, "pos_x", None)
                gy = getattr(gamestate, "pos_y", None)
                gz = getattr(gamestate, "pos_z", None)
                if gx is not None and gy is not None:
                    pos_key = (int(gx), int(gy), int(gz) if gz is not None else None)

                    # Coords confidence vs OCR jitter.
                    if last_good_coords is None:
                        coords_status = "OK"
                        coords_jump = 0
                        last_good_coords = pos_key
                    else:
                        dx = abs(int(pos_key[0]) - int(last_good_coords[0]))
                        dy = abs(int(pos_key[1]) - int(last_good_coords[1]))
                        z_changed = (pos_key[2] is not None and last_good_coords[2] is not None and int(pos_key[2]) != int(last_good_coords[2]))
                        coords_jump = int(dx + dy + (50 if z_changed else 0))
                        if coords_jump >= coords_fail_jump:
                            coords_status = "BAD_JUMP"
                        elif coords_jump >= coords_warn_jump:
                            coords_status = "UNSTABLE"
                        else:
                            coords_status = "OK"
                            last_good_coords = pos_key

                    if last_pos_key is None:
                        last_pos_key = pos_key
                        last_pos_change_ts = now
                    elif pos_key != last_pos_key:
                        last_pos_key = pos_key
                        last_pos_change_ts = now
                    else:
                        idle_for_s = max(0.0, now - float(last_pos_change_ts or now))

                    # Recovery attempts reset whenever position changes.
                    try:
                        if pos_key != recovery_last_pos_seen:
                            recovery_last_pos_seen = pos_key
                            recovery_attempts = 0
                    except Exception:
                        pass
                else:
                    pos_key = None
                    idle_for_s = None
                    coords_status = "DISABLED" if coords_provider_disabled else "NO_COORDS"
                    coords_jump = 0
            except Exception:
                pos_key = None
                idle_for_s = None
                coords_status = "DISABLED" if coords_provider_disabled else "NO_COORDS"
                coords_jump = 0

            # Diagnóstico específico para minimap (seed/ROI), rate-limited.
            try:
                if coords_provider_minimap and pos_key is None and not coords_provider_disabled:
                    seed_file = (os.getenv("COORDS_SEED_FILE", "") or "").strip()
                    seed_x = (os.getenv("COORDS_SEED_X", "") or "").strip()
                    seed_y = (os.getenv("COORDS_SEED_Y", "") or "").strip()
                    has_seed = bool(seed_file or (seed_x and seed_y))

                    has_minimap_roi = False
                    try:
                        if isinstance(rois, dict):
                            has_minimap_roi = "minimap_content" in rois
                    except Exception:
                        has_minimap_roi = False

                    if not has_minimap_roi:
                        coords_status = "NO_MINIMAP_ROI"
                    elif not has_seed:
                        coords_status = "NO_SEED"
                    else:
                        coords_status = "NO_COORDS"

                    now = time.time()
                    if now - last_coords_warn_ts >= 5.0:
                        last_coords_warn_ts = now
                        msg = ""
                        if coords_status == "NO_MINIMAP_ROI":
                            msg = (
                                "⚠️  minimap (experimental): falta ROI 'minimap_content' (ajusta ROIS_CONFIG). "
                                "Si no tienes coords visibles: COORDS_PROVIDER=disabled + CAVEBOT_MODE=steps"
                            )
                        elif coords_status == "NO_SEED":
                            msg = (
                                "⚠️  minimap (experimental): falta seed (COORDS_SEED_X/COORDS_SEED_Y o COORDS_SEED_FILE). "
                                "Si no tienes coords visibles: COORDS_PROVIDER=disabled + CAVEBOT_MODE=steps"
                            )
                        if msg:
                            try:
                                print(msg)
                            except Exception:
                                pass
                            try:
                                if runtime_config is not None:
                                    runtime_config.update_telemetry(note=msg)
                            except Exception:
                                pass
            except Exception:
                pass

            cap_current = getattr(gamestate, "cap_current", None)
            ring_equipped = getattr(gamestate, "ring_equipped", None)
            amulet_equipped = getattr(gamestate, "amulet_equipped", None)
            low_cap = None
            try:
                if cap_leave_enabled and cap_current is not None:
                    low_cap = bool(int(cap_current) <= int(cap_leave_threshold))
            except Exception:
                low_cap = None

            # Manual potions remaining (operator-provided). If unset, stays None.
            potions_remaining: int | None = None
            low_potions: bool | None = None
            try:
                raw = (os.getenv("POTIONS_REMAINING", "") or "").strip()
                if raw:
                    potions_remaining = int(float(raw))
            except Exception:
                potions_remaining = None
            try:
                if potions_stop_enabled and potions_remaining is not None:
                    low_potions = bool(int(potions_remaining) <= int(potions_min_threshold))
                else:
                    low_potions = None
            except Exception:
                low_potions = None

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

                cap_str = "?"
                try:
                    if cap_current is not None:
                        cap_str = str(int(cap_current))
                except Exception:
                    cap_str = "?"

                extras: list[str] = []
                try:
                    if ring_equipped is not None:
                        extras.append(f"Ring {'Y' if bool(ring_equipped) else 'N'}")
                    if amulet_equipped is not None:
                        extras.append(f"Amulet {'Y' if bool(amulet_equipped) else 'N'}")
                    if sig is not None and getattr(sig, "hungry", None) is not None:
                        extras.append(f"Hungry {'Y' if bool(sig.hungry) else 'N'}")
                except Exception:
                    extras = []
                extra_str = (", " + ", ".join(extras)) if extras else ""

                print(
                    f"🎮 Estado: HP {getattr(gamestate, 'hp_current', None)}/{getattr(gamestate, 'hp_max', None)} ({hp_pct_str}), "
                    f"MP {getattr(gamestate, 'mp_current', None)}/{getattr(gamestate, 'mp_max', None)} ({mp_pct_str}), "
                    f"Cap {cap_str}{extra_str}"
                )
            except Exception:
                extras: list[str] = []
                try:
                    if ring_equipped is not None:
                        extras.append(f"Ring {'Y' if bool(ring_equipped) else 'N'}")
                    if amulet_equipped is not None:
                        extras.append(f"Amulet {'Y' if bool(amulet_equipped) else 'N'}")
                    if sig is not None and getattr(sig, "hungry", None) is not None:
                        extras.append(f"Hungry {'Y' if bool(sig.hungry) else 'N'}")
                except Exception:
                    extras = []
                extra_str = (", " + ", ".join(extras)) if extras else ""
                print(
                    f"🎮 Estado: HP {getattr(gamestate, 'hp_current', None)}/{getattr(gamestate, 'hp_max', None)}, "
                    f"MP {getattr(gamestate, 'mp_current', None)}/{getattr(gamestate, 'mp_max', None)}, "
                    f"Cap {cap_current}{extra_str}"
                )

            # Cavebot básico (ruta + teclas) usando posición por env vars.
            cavebot_next = ""
            cavebot_waypoint = ""
            cavebot_action = ""
            cavebot_step_idx = 0
            cavebot_step_next_idx = 0
            cavebot_step_total = 0

            cavebot_finished = False
            cavebot_finish_reason = ""

            # Waypoint gating (assistant-only): e.g. require:<expr> blocks advancing.
            cavebot_blocked = False
            cavebot_block_reason = ""

            # Navigation observability (pos mode).
            nav_mode = ""
            nav_blockers = None
            nav_astar_found = None
            nav_astar_path_len = None
            nav_astar_visited = None

            # Recovery info (surfaced in telemetry when we emit a stuck reason)
            recovery_info = ""

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
                        # Keep loop behavior in sync with env (UI applies per-run, but this allows live toggles too).
                        try:
                            step_navigator.loop = (
                                os.getenv("CAVEBOT_LOOP", "1").strip().lower() in {"1", "true", "yes"}
                            )
                        except Exception:
                            pass

                        # Determine end/stop reasons (assistant-only).
                        reasons: list[str] = []
                        try:
                            if not bool(getattr(step_navigator, "loop", False)):
                                # When not looping, _current() becomes None once idx is out of range.
                                try:
                                    if step_navigator._current() is None:  # noqa: SLF001
                                        reasons.append("ROUTE_END")
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        try:
                            if low_cap is True:
                                reasons.append("LOW_CAP")
                        except Exception:
                            pass
                        try:
                            if low_potions is True:
                                reasons.append("LOW_POTIONS")
                        except Exception:
                            pass

                        if reasons:
                            cavebot_finished = True
                            cavebot_finish_reason = "|".join(reasons)

                        # UI-driven "jump to step" (only affects pointer/preview).
                        try:
                            if runtime_config is not None:
                                jc, jidx = runtime_config.step_jump_snapshot()
                                if jc != last_step_jump_seen:
                                    last_step_jump_seen = int(jc)
                                    ok = bool(step_navigator.jump_to(int(jidx)))
                                    if ok:
                                        print(f"🧭 Step jump -> {int(jidx):03d}")
                                    else:
                                        print(f"🧭 Step jump inválido: {int(jidx)}")
                        except Exception:
                            pass

                        if cavebot_finished:
                            # Stop emitting actions when finished; keep UI updated.
                            decision = step_navigator.preview()
                            cavebot_next = "stop"
                        else:
                            # Modo asistente: preview constante, pero solo consume/avanza con confirmación.
                            decision = step_navigator.preview()

                        if cavebot_finished:
                            should_advance = False
                        elif assistant_cfg is not None and assistant_cfg.enabled and assistant_cfg.confirm_actions:
                            cur_adv = runtime_config.advance_counter_snapshot() if runtime_config is not None else 0
                            should_advance = cur_adv != last_advance_seen
                            if should_advance:
                                last_advance_seen = cur_adv
                        else:
                            should_advance = True

                        # Require-gates: block advancing when requirements are not met.
                        if (not cavebot_finished) and should_advance:
                            try:
                                ok, why = evaluate_waypoint_requirements(
                                    cavebot_action,
                                    low_hp=getattr(sig, "low_hp", None) if sig is not None else None,
                                    low_mp=getattr(sig, "low_mp", None) if sig is not None else None,
                                    paralyzed=getattr(sig, "paralyzed", None) if sig is not None else None,
                                    haste_active=getattr(sig, "haste_active", None) if sig is not None else None,
                                    utamo_active=getattr(sig, "utamo_active", None) if sig is not None else None,
                                    hungry=getattr(sig, "hungry", None) if sig is not None else None,
                                    low_cap=low_cap,
                                    low_potions=low_potions,
                                    coords_status=coords_status,
                                    target=target_str,
                                )
                                if not ok:
                                    cavebot_blocked = True
                                    cavebot_block_reason = str(why or "require_failed")
                                    should_advance = False
                                    # Keep decision in preview mode.
                                    cavebot_next = "blocked"
                                    note_out = f"⛔ Blocked: {cavebot_block_reason}"
                                    try:
                                        print(note_out)
                                    except Exception:
                                        pass
                            except Exception:
                                pass

                        if (not cavebot_finished) and should_advance:
                            decision = step_navigator.decide()

                        # Only mark as "committed" when assistant confirm mode is on and the user advanced.
                        if assistant_cfg is not None and assistant_cfg.enabled and assistant_cfg.confirm_actions:
                            commit_flag = bool(should_advance)
                        else:
                            commit_flag = False

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

                        if not cavebot_finished and decision.direction:
                            cavebot_next = f"{decision.direction}"
                        elif decision.reached_waypoint and decision.waypoint is not None:
                            wp = decision.waypoint
                            label = wp.name or f"({wp.x},{wp.y})"
                            cavebot_next = f"reached {label}"

                        # StepNavigator index exposure for UI checklist.
                        try:
                            cavebot_step_total = int(len(getattr(step_navigator, "route", []) or []))
                        except Exception:
                            cavebot_step_total = 0
                        try:
                            cavebot_step_idx = int(getattr(step_navigator, "idx", 0) or 0)
                        except Exception:
                            cavebot_step_idx = 0
                        try:
                            if cavebot_step_total > 0:
                                cavebot_step_idx = min(max(0, int(cavebot_step_idx)), cavebot_step_total - 1)
                        except Exception:
                            pass
                        try:
                            if cavebot_step_total > 0:
                                j = cavebot_step_idx + 1
                                loop = bool(getattr(step_navigator, "loop", False))
                                if j >= cavebot_step_total:
                                    cavebot_step_next_idx = 0 if loop else cavebot_step_total - 1
                                else:
                                    cavebot_step_next_idx = j
                            else:
                                cavebot_step_next_idx = 0
                        except Exception:
                            cavebot_step_next_idx = 0
                    except Exception:
                        pass

                elif navigator is not None:
                    # Current position: prefer OCR coords from gamestate; fall back to env vars.
                    gx = getattr(gamestate, "pos_x", None)
                    gy = getattr(gamestate, "pos_y", None)
                    gz = getattr(gamestate, "pos_z", None)

                    px_raw = os.getenv("PLAYER_X", "").strip()
                    py_raw = os.getenv("PLAYER_Y", "").strip()
                    pz_raw = os.getenv("PLAYER_Z", "").strip()
                    pos = None
                    if gx is not None and gy is not None:
                        try:
                            if gz is not None:
                                pos = (int(gx), int(gy), int(gz))
                            else:
                                pos = (int(gx), int(gy))
                        except Exception:
                            pos = None
                    elif px_raw and py_raw:
                        try:
                            if pz_raw:
                                pos = (int(px_raw), int(py_raw), int(pz_raw))
                            else:
                                pos = (int(px_raw), int(py_raw))
                        except Exception:
                            pos = None

                    # Degradación/gating por coords: no usar coords si NO_COORDS o jitter/saltos.
                    if pos is None or coords_status in {"NO_COORDS", "BAD_JUMP", "UNSTABLE", "DISABLED", "NO_SEED", "NO_MINIMAP_ROI"}:
                        now = time.time()
                        if now - last_pos_warn_ts >= 5.0:
                            last_pos_warn_ts = now
                            if coords_status in {"NO_COORDS", "DISABLED", "NO_SEED", "NO_MINIMAP_ROI"}:
                                print(
                                    "🧭 Cavebot: no coords (pos_x/pos_y). Ajusta provider/seed o usa CAVEBOT_MODE=steps"
                                )
                            else:
                                print(
                                    f"🧭 Cavebot: coords inestables ({coords_status}, jump={coords_jump}). Ajusta ROI coords_ocr o usa CAVEBOT_MODE=steps"
                                )
                        # Degradación explícita: sin coords no tomamos decisiones de navegación.
                        cavebot_next = "no coords"
                        cavebot_waypoint = ""
                        cavebot_action = ""
                    else:
                        # Record coordinates for route building (pos mode).
                        try:
                            if route_recorder is not None:
                                if len(pos) >= 3:
                                    route_recorder.maybe_add(
                                        x=int(pos[0]),
                                        y=int(pos[1]),
                                        z=int(pos[2]),
                                        hp_current=getattr(gamestate, "hp_current", None),
                                        mp_current=getattr(gamestate, "mp_current", None),
                                    )
                                else:
                                    route_recorder.maybe_add(
                                        x=int(pos[0]),
                                        y=int(pos[1]),
                                        z=None,
                                        hp_current=getattr(gamestate, "hp_current", None),
                                        mp_current=getattr(gamestate, "mp_current", None),
                                    )
                        except Exception:
                            pass
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

                            # Navigation observability snapshot
                            try:
                                nav_mode = str(getattr(navigator, "pathfind_mode", "") or "")
                                nav_blockers = int(getattr(navigator, "last_blockers_n", 0) or 0)
                                nav_astar_found = getattr(navigator, "last_astar_found", None)
                                nav_astar_path_len = getattr(navigator, "last_astar_path_len", None)
                                nav_astar_visited = getattr(navigator, "last_astar_visited", None)
                            except Exception:
                                pass
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

            # Assistant-only recovery: if we have trusted coords and haven't moved
            # for a while *while cavebot wants to move*, suggest a sidestep.
            try:
                if (
                    recovery_enabled
                    and not cavebot_finished
                    and cavebot_next in {"north", "south", "east", "west"}
                    and pos_key is not None
                    and coords_status == "OK"
                    and idle_for_s is not None
                    and float(idle_for_s) >= float(recovery_idle_s)
                ):
                    now = time.time()
                    if (now - float(recovery_last_applied_ts or 0.0)) >= float(recovery_min_interval_s):
                        from decision.recovery import choose_sidestep_direction

                        override = choose_sidestep_direction(str(cavebot_next), attempt=int(recovery_attempts))
                        if override:
                            recovery_info = (
                                f"recovery=sidestep intent={cavebot_next} use={override} "
                                f"attempt={int(recovery_attempts) + 1}/{int(recovery_max_attempts)}"
                            )
                            cavebot_next = str(override)
                            recovery_last_applied_ts = float(now)
                            recovery_attempts = int(recovery_attempts) + 1

                            if recovery_stop_on_fail and int(recovery_attempts) >= int(recovery_max_attempts):
                                cavebot_finished = True
                                cavebot_finish_reason = (
                                    f"{cavebot_finish_reason}|STUCK" if cavebot_finish_reason else "STUCK"
                                )
                                cavebot_next = "stop"
            except Exception:
                pass

            # Recomendación humana (sin ejecutar inputs)
            recommendation = ""
            if sig is not None and healing_cfg is not None and healing_cfg.enabled and sig.healing_trigger:
                action = (healing_cfg.action or "").strip()
                recommendation = f"heal: {action}" if action else "heal"

            if low_cap:
                if recommendation:
                    recommendation = f"{recommendation} | leave depot"
                else:
                    recommendation = "leave depot"
            if cavebot_next:
                if recommendation:
                    recommendation = f"{recommendation} | move: {cavebot_next}"
                else:
                    recommendation = f"move: {cavebot_next}"

            # Build mock action requests (for logging/replay) without doing real inputs.
            action_req_str = ""
            action_committed = False
            input_plan_str = ""
            try:
                reqs = []

                eat_food_now = False
                try:
                    eat_food_now = bool(food_trigger.interval_s > 0.0 and food_trigger.should_fire())
                except Exception:
                    eat_food_now = False

                if bt_runner is not None and ActionRequest is not None:
                    target_cls = ""
                    target_conf = None
                    try:
                        if last_target is not None:
                            target_cls = str(getattr(last_target, "cls", "") or "")
                            target_conf = float(getattr(last_target, "confidence", 0.0) or 0.0)
                    except Exception:
                        target_cls = ""
                        target_conf = None

                    reqs = bt_runner.tick(
                        sig=sig,
                        healing_cfg=healing_cfg,
                        target_cls=target_cls,
                        target_conf=target_conf,
                        cavebot_next=cavebot_next,
                        cavebot_action=cavebot_action,
                        commit_flag=bool(commit_flag),
                        eat_food=bool(eat_food_now),
                    )
                else:
                    # Fallback: inline planner.
                    try:
                        if last_target is not None and ActionRequest is not None:
                            tcls = str(getattr(last_target, "cls", "") or "").strip().lower()
                            if tcls:
                                reqs.append(ActionRequest(kind="target", value=tcls, note="preview"))
                    except Exception:
                        pass

                    if sig is not None and healing_cfg is not None and healing_cfg.enabled and sig.healing_trigger:
                        act = (healing_cfg.action or "").strip() or "heal"
                        if ActionRequest is not None:
                            reqs.append(ActionRequest(kind="heal", value=act, note="preview"))

                    if cavebot_next in {"north", "south", "east", "west"}:
                        note = "committed" if commit_flag else "preview"
                        if ActionRequest is not None:
                            reqs.append(ActionRequest(kind="move", value=str(cavebot_next), note=note))

                    # Waypoint 'action' string can request loot/tools/trade/etc.
                    try:
                        if cavebot_action:
                            reqs.extend(build_requests_from_waypoint_action(cavebot_action, committed=bool(commit_flag)))
                    except Exception:
                        pass

                    # Periodic maintenance: eat food.
                    try:
                        if eat_food_now:
                            if ActionRequest is not None:
                                reqs.append(ActionRequest(kind="maintenance", value="eat_food", note="preview"))
                    except Exception:
                        pass

                if reqs:
                    action_committed = any(getattr(r, "note", "") == "committed" for r in reqs)
                    action_req_str = ";".join(
                        f"{r.kind}:{r.value}{'*' if getattr(r, 'note', '') == 'committed' else ''}" for r in reqs
                    )

                    # Assistant-only notification: allow waypoint action to request a beep.
                    try:
                        want_beep_committed = any(
                            str(getattr(r, "kind", "") or "").lower() == "beep"
                            and str(getattr(r, "note", "") or "") == "committed"
                            for r in reqs
                        )
                        want_beep_preview = any(
                            str(getattr(r, "kind", "") or "").lower() == "beep"
                            and str(getattr(r, "note", "") or "") != "committed"
                            for r in reqs
                        )

                        allow_preview = os.getenv("ASSIST_BEEP_PREVIEW", "0").strip().lower() in {"1", "true", "yes"}

                        should_beep = False
                        if want_beep_committed and bool(commit_flag):
                            should_beep = True
                        elif want_beep_preview and allow_preview:
                            should_beep = True

                        if (
                            should_beep
                            and assistant_cfg is not None
                            and assistant_cfg.enabled
                            and assistant_cfg.sound_alerts
                        ):
                            try:
                                import winsound

                                # Pick a tone based on the planned beep request value.
                                # This is still assistant-only (no input injection).
                                beep_value = ""
                                try:
                                    for r in reqs:
                                        if str(getattr(r, "kind", "") or "").lower() != "beep":
                                            continue
                                        rv = str(getattr(r, "value", "") or "")
                                        rn = str(getattr(r, "note", "") or "")
                                        if want_beep_committed and rn == "committed":
                                            beep_value = rv
                                            break
                                        if want_beep_preview and rn != "committed":
                                            beep_value = rv
                                            break
                                        if not beep_value:
                                            beep_value = rv
                                except Exception:
                                    beep_value = ""

                                def _env_int(name: str, default: int) -> int:
                                    try:
                                        return int(float(os.getenv(name, str(default)).strip() or str(default)))
                                    except Exception:
                                        return int(default)

                                freq = _env_int("ASSIST_BEEP_FREQ_DEFAULT", 740)
                                dur = _env_int("ASSIST_BEEP_DUR_MS", 120)

                                v = (beep_value or "").strip().lower()
                                if v == "target_reliable":
                                    freq = _env_int("ASSIST_BEEP_FREQ_TARGET_RELIABLE", 880)
                                elif v == "target_probable":
                                    freq = _env_int("ASSIST_BEEP_FREQ_TARGET_PROBABLE", 740)
                                elif v == "target_ambiguous":
                                    freq = _env_int("ASSIST_BEEP_FREQ_TARGET_AMBIGUOUS", 660)
                                elif v == "target_unknown":
                                    freq = _env_int("ASSIST_BEEP_FREQ_TARGET_UNKNOWN", 520)
                                else:
                                    # keep default
                                    pass

                                winsound.Beep(int(freq), int(dur))
                            except Exception:
                                try:
                                    print("\a", end="")
                                except Exception:
                                    pass
                    except Exception:
                        pass

                    # LOG-ONLY planned input sequence (no injection).
                    try:
                        from action.input_planner import InputPlanner, format_plan

                        planner = InputPlanner()
                        input_plan_str = format_plan(planner.plan_many(reqs))
                    except Exception:
                        input_plan_str = ""

                    if mock_driver is not None:
                        for r in reqs:
                            try:
                                mock_driver.send(r)
                            except Exception:
                                pass
            except Exception:
                action_req_str = ""
                action_committed = False
                input_plan_str = ""

            # Publish latest action snapshot for overlay/replay correlation.
            try:
                _a_set(action_req_str, bool(action_committed), input_plan_str)
            except Exception:
                pass

            # Anti-stuck diagnosis (no action): infer probable cause and publish as a note.
            try:
                if idle_alert_s > 0.0:
                    require_cavebot = os.getenv("ASSIST_STUCK_REQUIRE_CAVEBOT", "1").strip().lower() not in {
                        "0",
                        "false",
                        "no",
                    }
                    cavebot_mode = os.getenv("CAVEBOT_MODE", "pos").strip().lower()
                    cavebot_pos_mode = cavebot_mode not in {"steps", "step"}

                    stuck_reason: str | None = None
                    extra = ""

                    cavebot_enabled_now = bool(cavebot_cfg is not None and cavebot_cfg.enabled)
                    if require_cavebot and not cavebot_enabled_now:
                        stuck_reason = None
                        extra = ""

                    # If cavebot wants coords (pos mode) but we don't have them, call it out.
                    if cavebot_enabled_now and cavebot_pos_mode and (
                        pos_key is None or coords_status in {"NO_COORDS", "BAD_JUMP", "UNSTABLE", "DISABLED", "NO_SEED", "NO_MINIMAP_ROI"}
                    ):
                        stuck_reason = "NO_COORDS"
                        if coords_status == "DISABLED":
                            extra = "coords provider disabled (COORDS_PROVIDER=disabled)"
                        elif coords_status == "NO_SEED":
                            extra = "minimap (experimental) sin seed (COORDS_SEED_X/Y o COORDS_SEED_FILE)"
                        elif coords_status == "NO_MINIMAP_ROI":
                            extra = "minimap (experimental) sin ROI minimap_content (ROIS_CONFIG)"
                        else:
                            extra = (
                                "ajusta provider/seed o usa CAVEBOT_MODE=steps"
                                if coords_status == "NO_COORDS"
                                else f"coords={coords_status} jump={coords_jump}"
                            )
                        stuck_reason_tick = str(stuck_reason)
                        stuck_idle_s_tick = 0.0
                        stuck_blockers_tick = 0
                        stuck_extra_tick = str(extra)

                    # If we have coords and haven't moved for a while, attempt a cause.
                    elif cavebot_enabled_now and idle_for_s is not None and idle_for_s >= float(idle_alert_s):
                        offs = getattr(gamestate, "viewport_tile_offsets", None)
                        n_offs = 0
                        try:
                            n_offs = len(offs) if offs else 0
                        except Exception:
                            n_offs = 0
                        stuck_idle_s_tick = float(idle_for_s or 0.0)
                        stuck_blockers_tick = int(n_offs or 0)

                        if n_offs > 0:
                            stuck_reason = "BLOCKED"
                            extra = f"blockers={n_offs}"
                        else:
                            committed_move = False
                            try:
                                # committed move requests are serialized with a '*' suffix
                                committed_move = bool(action_committed) and ("move:" in action_req_str) and ("*" in action_req_str)
                            except Exception:
                                committed_move = False

                            if committed_move:
                                stuck_reason = "MOVE_COMMITTED_NO_CHANGE"
                            else:
                                stuck_reason = "IDLE"

                        if stuck_reason is not None:
                            try:
                                if recovery_info:
                                    extra = f"{extra} | {recovery_info}" if extra else str(recovery_info)
                            except Exception:
                                pass
                            stuck_reason_tick = str(stuck_reason)
                            stuck_extra_tick = str(extra)

                    if stuck_reason is not None and (now - last_idle_warn_ts) >= float(idle_repeat_s):
                        last_idle_warn_ts = now

                        pos_s = "?"
                        try:
                            if pos_key is not None:
                                pos_s = f"{pos_key[0]},{pos_key[1]}" + (f",{pos_key[2]}" if pos_key[2] is not None else "")
                        except Exception:
                            pos_s = "?"

                        dur_s = "?"
                        try:
                            dur_s = f"{float(idle_for_s):.0f}s" if idle_for_s is not None else "?"
                        except Exception:
                            dur_s = "?"

                        msg = f"⛔ Stuck: {stuck_reason} | pos {pos_s} | idle {dur_s}"
                        if extra:
                            msg = f"{msg} | {extra}"
                        note_out = msg
                        try:
                            print(msg)
                        except Exception:
                            pass

                        # Optional beep, only if assistant sound alerts are enabled.
                        try:
                            if assistant_cfg is not None and assistant_cfg.enabled and assistant_cfg.sound_alerts:
                                if now - last_idle_beep_ts >= max(1.0, float(idle_repeat_s)):
                                    last_idle_beep_ts = now
                                    try:
                                        import winsound

                                        winsound.Beep(660, 120)
                                    except Exception:
                                        try:
                                            print("\a", end="")
                                        except Exception:
                                            pass
                        except Exception:
                            pass
            except Exception:
                pass

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
                        cap_current=cap_current,
                        pos_x=getattr(gamestate, "pos_x", None),
                        pos_y=getattr(gamestate, "pos_y", None),
                        pos_z=getattr(gamestate, "pos_z", None),
                        coords_provider=str(getattr(gamestate, "coords_provider", "") or ""),
                        coords_status=coords_status,
                        coords_jump=coords_jump,
                        minimap_mode_used=str(getattr(gamestate, "minimap_mode_used", "") or ""),
                        minimap_response=getattr(gamestate, "minimap_response", None),
                        minimap_delta_dx=getattr(gamestate, "minimap_delta_dx", None),
                        minimap_delta_dy=getattr(gamestate, "minimap_delta_dy", None),
                        minimap_acc_dx=getattr(gamestate, "minimap_acc_dx", None),
                        minimap_acc_dy=getattr(gamestate, "minimap_acc_dy", None),
                        minimap_marker_dpx_dx=getattr(gamestate, "minimap_marker_dpx_dx", None),
                        minimap_marker_dpx_dy=getattr(gamestate, "minimap_marker_dpx_dy", None),
                        ring_equipped=ring_equipped,
                        amulet_equipped=amulet_equipped,
                        low_hp=sig.low_hp,
                        low_mp=sig.low_mp,
                        low_cap=low_cap,
                        potions_remaining=potions_remaining,
                        potions_min=int(potions_min_threshold) if potions_stop_enabled else None,
                        low_potions=low_potions,
                        paralyzed=sig.paralyzed,
                        haste_active=sig.haste_active,
                        utamo_active=sig.utamo_active,
                        hungry=sig.hungry,
                        target=target_str,
                        recommendation=recommendation,
                        cavebot_next=cavebot_next,
                        cavebot_waypoint=cavebot_waypoint,
                        cavebot_action=cavebot_action,
                        cavebot_step_idx=cavebot_step_idx,
                        cavebot_step_next_idx=cavebot_step_next_idx,
                        cavebot_step_total=cavebot_step_total,
                        cavebot_blocked=cavebot_blocked,
                        cavebot_block_reason=cavebot_block_reason,
                        cavebot_finished=cavebot_finished,
                        cavebot_finish_reason=cavebot_finish_reason,
                        nav_mode=nav_mode,
                        nav_blockers=nav_blockers,
                        nav_astar_found=nav_astar_found,
                        nav_astar_path_len=nav_astar_path_len,
                        nav_astar_visited=nav_astar_visited,
                        action_request=action_req_str,
                        action_committed=action_committed,
                        input_plan=input_plan_str,
                        note=note_out if note_out else None,
                        stuck_reason=stuck_reason_tick,
                        stuck_idle_s=stuck_idle_s_tick,
                        stuck_blockers=stuck_blockers_tick,
                        stuck_extra=stuck_extra_tick,
                    )
                except Exception:
                    pass

            # Export JSONL de eventos (opt-in): cambios relevantes.
            if sig is not None:
                try:
                    if runtime_config is not None:
                        log_cfg = runtime_config.logging_snapshot()
                        log_enabled = bool(log_cfg.enabled)
                        log_interval_ms = int(log_cfg.interval_ms)
                        log_out_file = str(log_cfg.out_file)
                    else:
                        log_enabled = bool(log_enabled_env)
                        log_interval_ms = int(log_interval_ms_env)
                        log_out_file = str(log_out_file_env)

                    if log_enabled:
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
                                _put_drop_oldest(jsonl_write_q, ("jsonl", (log_out_file, {"kind": kind, **data})))
                            except Exception:
                                pass

                        if target_str != last_event_target:
                            emit("event.target", {"target": target_str})
                            last_event_target = target_str

                        if recommendation != last_event_reco:
                            emit("event.recommendation", {"recommendation": recommendation})
                            last_event_reco = recommendation

                        if (
                            action_req_str != last_event_action_req
                            or last_event_action_committed is None
                            or action_committed != last_event_action_committed
                        ):
                            ats = None
                            try:
                                _ar, _ac, _ats, _ip = _a_snapshot()
                                if _ats:
                                    ats = float(_ats)
                            except Exception:
                                ats = None
                            emit(
                                "event.action_request",
                                {
                                    "action_request": action_req_str,
                                    "action_committed": bool(action_committed),
                                    "action_ts": ats,
                                    "input_plan": input_plan_str,
                                },
                            )
                            last_event_action_req = action_req_str
                            last_event_action_committed = bool(action_committed)

                        if cavebot_waypoint != last_event_wp or cavebot_action != last_event_action:
                            emit(
                                "event.cavebot",
                                {
                                    "cavebot_waypoint": cavebot_waypoint,
                                    "cavebot_action": cavebot_action,
                                    "cavebot_next": cavebot_next,
                                },
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

                        # Note (stuck/stale diagnostics)
                        try:
                            if note_out and note_out != last_event_note:
                                emit("event.note", {"note": note_out})
                                last_event_note = note_out
                        except Exception:
                            pass
                except Exception:
                    pass

            # Export JSONL de telemetría (opt-in).
            try:
                if runtime_config is not None:
                    log_cfg = runtime_config.logging_snapshot()
                    log_enabled = bool(log_cfg.enabled)
                    log_interval_ms = int(log_cfg.interval_ms)
                    log_out_file = str(log_cfg.out_file)
                    tel = runtime_config.telemetry_snapshot()
                    tel_event = {
                        "kind": "telemetry",
                        "hp_current": tel.hp_current,
                        "hp_max": tel.hp_max,
                        "hp_pct": tel.hp_pct,
                        "mp_current": tel.mp_current,
                        "mp_max": tel.mp_max,
                        "mp_pct": tel.mp_pct,
                        "cap_current": getattr(tel, "cap_current", None),
                        "pos_x": getattr(tel, "pos_x", None),
                        "pos_y": getattr(tel, "pos_y", None),
                        "pos_z": getattr(tel, "pos_z", None),
                        "coords_provider": getattr(tel, "coords_provider", ""),
                        "coords_status": getattr(tel, "coords_status", ""),
                        "coords_jump": getattr(tel, "coords_jump", None),
                        "minimap_mode_used": getattr(tel, "minimap_mode_used", ""),
                        "minimap_response": getattr(tel, "minimap_response", None),
                        "minimap_delta_dx": getattr(tel, "minimap_delta_dx", None),
                        "minimap_delta_dy": getattr(tel, "minimap_delta_dy", None),
                        "minimap_acc_dx": getattr(tel, "minimap_acc_dx", None),
                        "minimap_acc_dy": getattr(tel, "minimap_acc_dy", None),
                        "minimap_marker_dpx_dx": getattr(tel, "minimap_marker_dpx_dx", None),
                        "minimap_marker_dpx_dy": getattr(tel, "minimap_marker_dpx_dy", None),
                        "ring_equipped": getattr(tel, "ring_equipped", None),
                        "amulet_equipped": getattr(tel, "amulet_equipped", None),
                        "low_hp": tel.low_hp,
                        "low_mp": tel.low_mp,
                        "low_cap": getattr(tel, "low_cap", None),
                        "paralyzed": tel.paralyzed,
                        "haste_active": tel.haste_active,
                        "utamo_active": tel.utamo_active,
                        "hungry": tel.hungry,
                        "target": tel.target,
                        "recommendation": tel.recommendation,
                        "cavebot_next": tel.cavebot_next,
                        "cavebot_waypoint": tel.cavebot_waypoint,
                        "cavebot_action": tel.cavebot_action,
                        "cavebot_step_idx": getattr(tel, "cavebot_step_idx", None),
                        "cavebot_step_next_idx": getattr(tel, "cavebot_step_next_idx", None),
                        "cavebot_step_total": getattr(tel, "cavebot_step_total", None),
                        "action_request": getattr(tel, "action_request", ""),
                        "action_committed": bool(getattr(tel, "action_committed", False)),
                        "action_ts": None,
                        "note": tel.note,
                        "stuck_reason": getattr(tel, "stuck_reason", ""),
                        "stuck_idle_s": getattr(tel, "stuck_idle_s", None),
                        "stuck_blockers": getattr(tel, "stuck_blockers", None),
                        "stuck_extra": getattr(tel, "stuck_extra", ""),
                    }
                    try:
                        ar, _ac, ats, ip = _a_snapshot()
                        if ar and ats:
                            tel_event["action_ts"] = float(ats)
                        if ip:
                            tel_event["input_plan"] = str(ip)
                    except Exception:
                        pass
                else:
                    log_enabled = bool(log_enabled_env)
                    log_interval_ms = int(log_interval_ms_env)
                    log_out_file = str(log_out_file_env)
                    tel_event = {
                        "kind": "telemetry",
                        "hp_current": getattr(gamestate, "hp_current", None),
                        "hp_max": getattr(gamestate, "hp_max", None),
                        "hp_pct": getattr(gamestate, "hp_pct", None),
                        "mp_current": getattr(gamestate, "mp_current", None),
                        "mp_max": getattr(gamestate, "mp_max", None),
                        "mp_pct": getattr(gamestate, "mp_pct", None),
                        "cap_current": getattr(gamestate, "cap_current", None),
                        "pos_x": getattr(gamestate, "pos_x", None),
                        "pos_y": getattr(gamestate, "pos_y", None),
                        "pos_z": getattr(gamestate, "pos_z", None),
                        "coords_provider": getattr(gamestate, "coords_provider", ""),
                        "coords_status": coords_status,
                        "coords_jump": coords_jump,
                        "minimap_mode_used": getattr(gamestate, "minimap_mode_used", ""),
                        "minimap_response": getattr(gamestate, "minimap_response", None),
                        "minimap_delta_dx": getattr(gamestate, "minimap_delta_dx", None),
                        "minimap_delta_dy": getattr(gamestate, "minimap_delta_dy", None),
                        "minimap_acc_dx": getattr(gamestate, "minimap_acc_dx", None),
                        "minimap_acc_dy": getattr(gamestate, "minimap_acc_dy", None),
                        "minimap_marker_dpx_dx": getattr(gamestate, "minimap_marker_dpx_dx", None),
                        "minimap_marker_dpx_dy": getattr(gamestate, "minimap_marker_dpx_dy", None),
                        "ring_equipped": ring_equipped,
                        "amulet_equipped": amulet_equipped,
                        "low_hp": bool(getattr(sig, "low_hp", False)) if sig is not None else None,
                        "low_mp": bool(getattr(sig, "low_mp", False)) if sig is not None else None,
                        "low_cap": low_cap,
                        "paralyzed": bool(getattr(sig, "paralyzed", False)) if sig is not None else None,
                        "haste_active": bool(getattr(sig, "haste_active", False)) if sig is not None else None,
                        "utamo_active": bool(getattr(sig, "utamo_active", False)) if sig is not None else None,
                        "hungry": bool(getattr(sig, "hungry", False)) if sig is not None else None,
                        "target": target_str,
                        "recommendation": recommendation,
                        "cavebot_next": cavebot_next,
                        "cavebot_waypoint": cavebot_waypoint,
                        "cavebot_action": cavebot_action,
                        "cavebot_step_idx": cavebot_step_idx,
                        "cavebot_step_next_idx": cavebot_step_next_idx,
                        "cavebot_step_total": cavebot_step_total,
                        "cavebot_blocked": bool(cavebot_blocked),
                        "cavebot_block_reason": str(cavebot_block_reason or ""),
                        "action_request": action_req_str,
                        "action_committed": bool(action_committed),
                        "action_ts": None,
                        "note": "",
                        "stuck_reason": stuck_reason_tick,
                        "stuck_idle_s": stuck_idle_s_tick,
                        "stuck_blockers": stuck_blockers_tick,
                        "stuck_extra": stuck_extra_tick,
                        "nav_mode": str(nav_mode or ""),
                        "nav_blockers": nav_blockers,
                        "nav_astar_found": nav_astar_found,
                        "nav_astar_path_len": nav_astar_path_len,
                        "nav_astar_visited": nav_astar_visited,
                    }
                    try:
                        ar, _ac, ats, ip = _a_snapshot()
                        if ar and ats:
                            tel_event["action_ts"] = float(ats)
                        if ip:
                            tel_event["input_plan"] = str(ip)
                    except Exception:
                        pass

                if log_enabled and jsonl.should_log(enabled=log_enabled, interval_ms=log_interval_ms):
                    _put_drop_oldest(jsonl_write_q, ("jsonl", (log_out_file, tel_event)))
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
                _h_set("decision_ms_last", (time.time() - loop_t0) * 1000.0)
                _h_inc("decision_ok")
                now = time.time()
                if now - last_prof >= prof_every_s and n_dec:
                    avg = dec_ms_sum / max(1, n_dec)
                    print(f"⏱️  decision avg {avg:.1f}ms ({n_dec} loops/{prof_every_s:.0f}s)")
                    last_prof = now
                    n_dec = 0
                    dec_ms_sum = 0.0

        # Persist recorded route on shutdown.
        try:
            if route_recorder is not None and record_route_path:
                route_recorder.save_route(record_route_path, name_prefix="wp")
                print(f"🧾 Route saved: {record_route_path} ({len(route_recorder.samples)} points)")
                if record_samples_path:
                    try:
                        route_recorder.save_samples_jsonl(record_samples_path)
                        print(f"🧾 Samples saved: {record_samples_path} ({len(route_recorder.samples)} points)")
                    except Exception:
                        pass
        except Exception:
            pass

        # If we exit the loop (stop_event set or crash), record it.
        _h_inc("decision_ex")

    # Watchdog (stability): detect dead threads / stale pipeline; optionally emit health JSONL.
    thread_map: dict[str, threading.Thread] = {}

    def watchdog_thread() -> None:
        check_s = 1.0
        try:
            check_s = max(0.2, float(os.getenv("WATCHDOG_INTERVAL_S", "1").strip() or "1"))
        except Exception:
            check_s = 1.0
        try:
            stale_gs_s = float(os.getenv("WATCHDOG_STALE_GS_S", "5").strip() or "5")
        except Exception:
            stale_gs_s = 5.0
        stop_on_dead = os.getenv("WATCHDOG_STOP_ON_THREAD_DEAD", "1").strip().lower() not in {"0", "false", "no"}
        stop_on_stale = os.getenv("WATCHDOG_STOP_ON_STALE", "0").strip().lower() in {"1", "true", "yes"}

        last_warn_ts = 0.0
        last_health_emit_ts = 0.0
        health_emit_s = 2.0
        try:
            health_emit_s = max(0.5, float(os.getenv("WATCHDOG_HEALTH_EMIT_S", "2").strip() or "2"))
        except Exception:
            health_emit_s = 2.0

        while not stop_event.is_set():
            now = time.time()

            # Thread liveness
            dead: list[str] = []
            try:
                for name, t in list(thread_map.items()):
                    if name == "watchdog":
                        continue
                    if not t.is_alive():
                        dead.append(name)
            except Exception:
                dead = []

            # Staleness
            snap = _h_snapshot()
            last_gs = float(snap.get("last_gs_ts", 0.0) or 0.0)
            # last_gs_ts==0 means we haven't produced any GameState yet.
            # Avoid emitting noisy 'stale' warnings during startup.
            gs_age = (now - last_gs) if last_gs > 0 else None

            # Publish health snapshot to UI (separate from TelemetrySnapshot.ts).
            try:
                if runtime_config is not None:
                    last_frame = float(snap.get("last_frame_ts", 0.0) or 0.0)
                    frame_age = (now - last_frame) if last_frame > 0 else 1e9

                    # ROI auto-alignment (AnchorTracker) lives in the shared `rois` dict.
                    roi_dx = None
                    roi_dy = None
                    roi_score = None
                    try:
                        if isinstance(rois, dict):
                            off = rois.get("_roi_offset_px")
                            if isinstance(off, (list, tuple)) and len(off) >= 2:
                                roi_dx = float(off[0])
                                roi_dy = float(off[1])
                            roi_score = rois.get("_roi_offset_score")
                            if roi_score is not None:
                                roi_score = float(roi_score)
                    except Exception:
                        roi_dx = None
                        roi_dy = None
                        roi_score = None

                    runtime_config.update_health(
                        ts=now,
                        uptime_s=max(0.0, now - float(snap.get("start_ts", now))),
                        frame_age_s=frame_age if frame_age < 1e8 else None,
                        gs_age_s=(gs_age if (gs_age is not None and gs_age < 1e8) else None),
                        dead_threads=",".join(dead) if dead else "",
                        capture_ok=int(snap.get("capture_ok", 0.0)),
                        capture_none=int(snap.get("capture_none", 0.0)),
                        vision_ok=int(snap.get("vision_ok", 0.0)),
                        vision_ex=int(snap.get("vision_ex", 0.0)),
                        decision_ok=int(snap.get("decision_ok", 0.0)),
                        decision_ex=int(snap.get("decision_ex", 0.0)),
                        drop_frame_queue=int(snap.get("drop_frame_queue", 0.0)),
                        drop_gs_queue=int(snap.get("drop_gs_queue", 0.0)),
                        drop_replay_queue=int(snap.get("drop_replay_queue", 0.0)),
                        drop_jsonl_queue=int(snap.get("drop_jsonl_queue", 0.0)),
                        q_frame=int(getattr(frame_queue, "qsize", lambda: 0)()),
                        q_gs=int(getattr(gs_queue, "qsize", lambda: 0)()),
                        capture_ms_last=float(snap.get("capture_ms_last", 0.0)),
                        vision_ms_last=float(snap.get("vision_ms_last", 0.0)),
                        decision_ms_last=float(snap.get("decision_ms_last", 0.0)),
                        capture_latency_ms=float(getattr(capture, "latency_ms", 0.0) or 0.0),
                        roi_offset_dx_px=roi_dx,
                        roi_offset_dy_px=roi_dy,
                        roi_offset_score=roi_score,
                        warn=(
                            f"dead={dead}"
                            if dead
                            else (
                                f"stale_gs={gs_age:.1f}s" if (gs_age is not None and gs_age >= stale_gs_s) else ""
                            )
                        ),
                    )
            except Exception:
                pass

            if dead or (gs_age is not None and gs_age >= stale_gs_s):
                if now - last_warn_ts >= 2.0:
                    last_warn_ts = now
                    msg = ""
                    if dead:
                        msg = f"⚠️  Watchdog: dead threads={dead}"
                    elif gs_age is not None and gs_age >= stale_gs_s:
                        msg = f"⚠️  Watchdog: stale GameState ({gs_age:.1f}s)"
                    try:
                        print(msg)
                    except Exception:
                        pass
                    if runtime_config is not None:
                        try:
                            runtime_config.update_telemetry(note=msg)
                        except Exception:
                            pass

                if dead and stop_on_dead:
                    stop_event.set()
                elif (gs_age is not None and gs_age >= stale_gs_s) and stop_on_stale:
                    stop_event.set()

            # Optional health JSONL
            try:
                if now - last_health_emit_ts >= health_emit_s:
                    last_health_emit_ts = now

                    # Determine logging config source.
                    if runtime_config is not None:
                        log_cfg = runtime_config.logging_snapshot()
                        log_enabled = bool(log_cfg.enabled)
                        log_out_file = str(log_cfg.out_file)
                    else:
                        log_enabled_env_raw = os.getenv("LOG_JSONL_ENABLED", "").strip().lower() or os.getenv("LOG_ENABLED", "").strip().lower()
                        log_enabled = log_enabled_env_raw in {"1", "true", "yes"}
                        log_out_file = os.getenv("LOG_JSONL_OUT_FILE", "logs/telemetry.jsonl").strip() or "logs/telemetry.jsonl"

                    if log_enabled:
                        snap = _h_snapshot()
                        event = {
                            "kind": "health",
                            "ts": now,
                            "uptime_s": max(0.0, now - float(snap.get("start_ts", now))),
                            "frame_age_s": max(0.0, now - float(snap.get("last_frame_ts", 0.0))) if snap.get("last_frame_ts", 0.0) else None,
                            "gs_age_s": max(0.0, now - float(snap.get("last_gs_ts", 0.0))) if snap.get("last_gs_ts", 0.0) else None,
                            "capture_ok": int(snap.get("capture_ok", 0.0)),
                            "capture_none": int(snap.get("capture_none", 0.0)),
                            "vision_ok": int(snap.get("vision_ok", 0.0)),
                            "vision_ex": int(snap.get("vision_ex", 0.0)),
                            "decision_ok": int(snap.get("decision_ok", 0.0)),
                            "decision_ex": int(snap.get("decision_ex", 0.0)),
                            "drop_frame_queue": int(snap.get("drop_frame_queue", 0.0)),
                            "drop_gs_queue": int(snap.get("drop_gs_queue", 0.0)),
                            "drop_replay_queue": int(snap.get("drop_replay_queue", 0.0)),
                            "drop_jsonl_queue": int(snap.get("drop_jsonl_queue", 0.0)),
                            "q_frame": int(getattr(frame_queue, "qsize", lambda: 0)()),
                            "q_gs": int(getattr(gs_queue, "qsize", lambda: 0)()),
                            "capture_ms_last": float(snap.get("capture_ms_last", 0.0)),
                            "vision_ms_last": float(snap.get("vision_ms_last", 0.0)),
                            "decision_ms_last": float(snap.get("decision_ms_last", 0.0)),
                            "capture_latency_ms": float(getattr(capture, "latency_ms", 0.0) or 0.0),
                        }
                        _put_drop_oldest(jsonl_write_q, ("jsonl", (log_out_file, event)), drop_key="drop_jsonl_queue")
            except Exception:
                pass

            try:
                time.sleep(check_s)
            except Exception:
                pass

    # Iniciar threads
    threads = [
        threading.Thread(target=writer_thread, daemon=True, name="writer"),
        threading.Thread(target=capture_thread, daemon=True, name="capture"),
        threading.Thread(target=vision_thread, daemon=True, name="vision"),
        threading.Thread(target=decision_thread, daemon=True, name="decision"),
        threading.Thread(target=watchdog_thread, daemon=True, name="watchdog"),
    ]

    # Expose threads to watchdog.
    try:
        thread_map = {t.name: t for t in threads if getattr(t, "name", "")}
    except Exception:
        thread_map = {}

    for t in threads:
        t.start()

    print("✅ Bot ejecutándose. Presiona Ctrl+C para detener.")
    # Optional auto-exit (useful for soak/debug runs without Ctrl+C).
    try:
        exit_after_s = float(os.getenv("BOT_EXIT_AFTER_S", "0").strip() or "0")
    except Exception:
        exit_after_s = 0.0
    start_ts = time.time()
    try:
        while not stop_event.is_set():
            time.sleep(1)
            if exit_after_s > 0.0 and (time.time() - start_ts) >= exit_after_s:
                print(f"⏱️  Auto-stop after {exit_after_s:.1f}s")
                stop_event.set()
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
