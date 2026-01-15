from __future__ import annotations

import json
import os
import sys
import threading
import time
import subprocess
from pathlib import Path
from typing import Any, Literal, cast


def _add_src_to_syspath() -> None:
    repo_root = Path(__file__).resolve().parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


class BotUI:
    def __init__(self) -> None:
        _add_src_to_syspath()

        self._repo_root = Path(__file__).resolve().parent
        self._last_roi_sanity_dir: str | None = None
        self._last_ocr_sanity_dir: str | None = None
        self._last_anchor_sanity_dir: str | None = None
        self._last_coords_sanity_dir: str | None = None

        try:
            import tkinter as tk
            from tkinter import filedialog, messagebox, simpledialog
            from tkinter import ttk
        except Exception as e:
            raise SystemExit(f"Tkinter no esta disponible en este entorno: {e}")

        # Optional system tray deps (pystray + Pillow).
        # Keep these as Any so we can assign a module or None.
        self._pystray: Any = None
        self._TrayImage: Any = None
        self._TrayImageDraw: Any = None

        try:
            import pystray  # type: ignore
            from PIL import Image, ImageDraw
            self._pystray = pystray
            self._TrayImage = Image
            self._TrayImageDraw = ImageDraw
        except Exception:
            self._pystray = None
            self._TrayImage = None
            self._TrayImageDraw = None

        self._tk = tk
        self._messagebox = messagebox
        self._filedialog = filedialog
        self._simpledialog = simpledialog
        self._ttk = ttk

        from main import run_bot  # import dentro para respetar sys.path
        from runtime_config import RuntimeConfig
        from route_editor.models import SetupConfig, WaypointStep
        from route_editor.setup_loader import load_setup, save_setup
        from route_editor.waypoints import (
            expand_move_macros,
            parse_waypoints,
            serialize_waypoints,
            validate_waypoints,
        )

        self._run_bot = run_bot
        self._config = RuntimeConfig()
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None

        # keep helpers reachable in instance
        self._WaypointStep = WaypointStep
        self._SetupConfig = SetupConfig
        self._load_setup = load_setup
        self._save_setup = save_setup
        self._parse_waypoints = parse_waypoints
        self._serialize_waypoints = serialize_waypoints
        self._validate_waypoints = validate_waypoints
        self._expand_move_macros = expand_move_macros

        # Soak session tracking (UI-only, helps opening the latest artifacts).
        self._last_soak_run_id: str | None = None
        self.soak_run_id_var = None

        self.root = tk.Tk()
        self.root.title("Tibia Bot Framework")
        self.root.resizable(False, False)

        # Soak run id (requires a root window)
        self.soak_run_id_var = tk.StringVar(master=self.root, value="-")

        # Estado general
        self.status_var = tk.StringVar(value="Detenido")
        self.stale_var = tk.StringVar(value="-")
        self.health_var = tk.StringVar(value="-")
        self.health_status_var = tk.StringVar(value="-")
        self.anchor_var = tk.StringVar(value="-")
        self.anchor_status_var = tk.StringVar(value="OFF")
        self.idle_var = tk.StringVar(value="-")
        self.idle_status_var = tk.StringVar(value="OFF")
        self.stuck_var = tk.StringVar(value="-")
        self.stuck_status_var = tk.StringVar(value="OFF")

        # UI-side idle tracking (derived from telemetry coords)
        self._idle_last_pos_key: tuple[int, int, int | None] | None = None
        self._idle_last_pos_change_ts: float = 0.0

        # UI operator panel (event stream)
        self._event_lines: list[str] = []
        self._event_max_lines = 30
        self._last_event_target: str = ""
        self._last_event_reco: str = ""
        self._last_event_action_req: str = ""
        self._last_event_action_committed: bool | None = None
        self._last_event_action_source: str = ""
        self._last_event_input_plan: str = ""
        self._latest_input_plan: str = ""
        self._latest_action_requests_display: str = ""
        self._last_event_wp: str = ""
        self._last_event_wp_action: str = ""
        self._last_event_health_status: str = ""
        self._last_event_idle_status: str = ""
        self._last_event_note: str = ""
        self._last_event_coords_status: str = ""
        self._last_event_stuck_reason: str = ""
        self._last_event_step_idx: int | None = None
        self._last_event_step_total: int | None = None

        # Route checklist state (UI-only)
        self._route_items: list[dict] = []
        self._route_loaded_from: str = ""
        self._route_current_index: int | None = None
        self._cavebot_follow_ui: bool = False

        # Ultimos resultados de sanity-check (persisten en UI)
        self.last_roi_summary_var = tk.StringVar(value="-")
        self.last_roi_dir_var = tk.StringVar(value="-")
        self.last_ocr_summary_var = tk.StringVar(value="-")
        self.last_ocr_dir_var = tk.StringVar(value="-")
        self.last_coords_summary_var = tk.StringVar(value="-")
        self.last_coords_dir_var = tk.StringVar(value="-")
        self.last_anchor_summary_var = tk.StringVar(value="-")
        self.last_anchor_dir_var = tk.StringVar(value="-")

        # Configuracion (en memoria por ahora)
        self.healing_enabled = tk.BooleanVar(value=False)
        self.heal_hp_below_pct = tk.IntVar(value=70)
        self.heal_hp_recover_pct = tk.IntVar(value=80)
        self.heal_mp_below_pct = tk.IntVar(value=30)
        self.heal_mp_recover_pct = tk.IntVar(value=50)
        self.heal_action = tk.StringVar(value="")
        self.heal_hp_action = tk.StringVar(value="")
        self.heal_mp_action = tk.StringVar(value="")
        self.heal_cooldown_s = tk.DoubleVar(value=1.0)

        self.cavebot_enabled = tk.BooleanVar(value=False)
        # Prefer scripts-master sample route when present.
        try:
            cand = self._repo_root / "scripts-master" / "wasp_ab" / "waypoints.in"
            if cand.is_file():
                self.cavebot_route_path = tk.StringVar(value=str(cand))
            else:
                self.cavebot_route_path = tk.StringVar(value="configs/route.json")
        except Exception:
            self.cavebot_route_path = tk.StringVar(value="configs/route.json")
        # Cavebot execution mode (applied via env vars at bot start)
        try:
            _cb_mode_default = (os.getenv("CAVEBOT_MODE", "") or "").strip().lower()
        except Exception:
            _cb_mode_default = ""
        if _cb_mode_default not in {"steps", "pos"}:
            # Default to steps: works without visible coords.
            _cb_mode_default = "steps"
        self.cavebot_mode = tk.StringVar(value=_cb_mode_default)

        # Loop route in step mode (applied via env var CAVEBOT_LOOP).
        try:
            _cb_loop_default = (os.getenv("CAVEBOT_LOOP", "1") or "1").strip().lower() in {"1", "true", "yes"}
        except Exception:
            _cb_loop_default = True
        self.cavebot_loop = tk.BooleanVar(value=bool(_cb_loop_default))

        # Coords provider (applied via env vars at bot start). "auto" = don't touch env.
        try:
            _cp_default = (os.getenv("COORDS_PROVIDER", "") or "").strip().lower()
        except Exception:
            _cp_default = ""
        if not _cp_default:
            _cp_default = "disabled" if _cb_mode_default == "steps" else "ocr"
        if _cp_default not in {"auto", "ocr", "minimap", "disabled"}:
            _cp_default = "ocr"
        self.coords_provider = tk.StringVar(value=_cp_default)

        # Minimap-motion provider calibration (persisted in UI config; applied via env vars).
        self.minimap_seed_x = tk.StringVar(value=(os.getenv("COORDS_SEED_X", "") or "").strip())
        self.minimap_seed_y = tk.StringVar(value=(os.getenv("COORDS_SEED_Y", "") or "").strip())
        self.minimap_seed_z = tk.StringVar(value=(os.getenv("COORDS_SEED_Z", "") or "").strip())

        mm_fb_mode = (os.getenv("MINIMAP_FALLBACK_MODE", "steps") or "steps").strip().lower()
        if mm_fb_mode not in {"steps", "ocr"}:
            mm_fb_mode = "steps"
        self.minimap_fallback_mode = tk.StringVar(value=mm_fb_mode)
        try:
            mm_thr = float((os.getenv("MINIMAP_FALLBACK_CONF_THRESHOLD", "0.40") or "0.40").strip() or "0.40")
        except Exception:
            mm_thr = 0.40
        self.minimap_fallback_conf_threshold = tk.DoubleVar(value=float(max(0.0, min(1.0, mm_thr))))
        try:
            mm_n = int(float((os.getenv("MINIMAP_FALLBACK_N_TICKS", "4") or "4").strip() or "4"))
        except Exception:
            mm_n = 4
        self.minimap_fallback_n_ticks = tk.IntVar(value=max(1, int(mm_n)))

        # UI-only: live provider status (text + semáforo color).
        self.coords_provider_state_text = tk.StringVar(value="-")
        self._coords_provider_state_label = None

        # Cavebot stop conditions (assistant-only): low cap / low potions.
        try:
            _cap_leave_enabled = os.getenv("CAP_LEAVE_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
        except Exception:
            _cap_leave_enabled = True
        try:
            _cap_leave_threshold = int(float(os.getenv("CAP_LEAVE_THRESHOLD", "50").strip() or "50"))
        except Exception:
            _cap_leave_threshold = 50
        self.cavebot_stop_on_low_cap = tk.BooleanVar(value=bool(_cap_leave_enabled))
        self.cavebot_cap_threshold = tk.IntVar(value=max(0, int(_cap_leave_threshold)))

        try:
            _pot_stop_enabled = os.getenv("POTIONS_STOP_ENABLED", "0").strip().lower() in {"1", "true", "yes"}
        except Exception:
            _pot_stop_enabled = False
        try:
            _pot_min = int(float(os.getenv("POTIONS_MIN", "0").strip() or "0"))
        except Exception:
            _pot_min = 0
        try:
            _pot_rem_raw = (os.getenv("POTIONS_REMAINING", "") or "").strip()
            _pot_rem = int(float(_pot_rem_raw)) if _pot_rem_raw else 0
        except Exception:
            _pot_rem = 0
        self.cavebot_stop_on_low_potions = tk.BooleanVar(value=bool(_pot_stop_enabled))
        self.cavebot_potions_remaining = tk.IntVar(value=max(0, int(_pot_rem)))
        self.cavebot_potions_min = tk.IntVar(value=max(0, int(_pot_min)))

        # Cavebot recovery (assistant-only): propose sidesteps when stuck.
        try:
            _rec_enabled = os.getenv("ASSIST_RECOVERY_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
        except Exception:
            _rec_enabled = True
        try:
            _rec_idle = float((os.getenv("ASSIST_RECOVERY_IDLE_S", "") or "").strip() or "0")
        except Exception:
            _rec_idle = 0.0
        try:
            _rec_max = int(float(os.getenv("ASSIST_RECOVERY_MAX_ATTEMPTS", "4").strip() or "4"))
        except Exception:
            _rec_max = 4
        try:
            _rec_stop = os.getenv("ASSIST_RECOVERY_STOP_ON_FAIL", "0").strip().lower() in {"1", "true", "yes"}
        except Exception:
            _rec_stop = False
        self.cavebot_recovery_enabled = tk.BooleanVar(value=bool(_rec_enabled))
        # 0 => auto (core will use ASSIST_IDLE_ALERT_S or fallback)
        self.cavebot_recovery_idle_s = tk.DoubleVar(value=max(0.0, float(_rec_idle)))
        self.cavebot_recovery_max_attempts = tk.IntVar(value=max(1, int(_rec_max)))
        self.cavebot_recovery_stop_on_fail = tk.BooleanVar(value=bool(_rec_stop))

        self.cavebot_finish_text = tk.StringVar(value="-")
        self.cavebot_block_text = tk.StringVar(value="-")

        self.cavebot_step_text = tk.StringVar(value="-")

        # Injection status (assistant-only observability; reflects why actions won't execute).
        self.injection_step_var = tk.StringVar(value="-")
        self.injection_label_var = tk.StringVar(value="-")
        self.injection_next_var = tk.StringVar(value="-")
        self.injection_state_var = tk.StringVar(value="-")
        self.injection_reason_var = tk.StringVar(value="-")
        self.injection_mode_driver_var = tk.StringVar(value="-")
        self.injection_foreground_var = tk.StringVar(value="-")
        self.injection_block_reason_var = tk.StringVar(value="-")

        # Simulacion/overrides de senales (para cuando aun no hay deteccion real)
        self.sim_enabled = tk.BooleanVar(value=True)
        self.sim_paralyzed = tk.BooleanVar(value=False)
        self.sim_haste_active = tk.BooleanVar(value=False)
        self.sim_utamo_active = tk.BooleanVar(value=False)
        self.sim_hungry = tk.BooleanVar(value=False)

        # Modo asistente (sin inputs) + confirmacion humana
        self.asst_enabled = tk.BooleanVar(value=True)
        self.asst_confirm = tk.BooleanVar(value=True)
        self.asst_sound = tk.BooleanVar(value=True)

        try:
            _action_driver = os.getenv("ACTION_DRIVER", "log").strip().lower() or "log"
        except Exception:
            _action_driver = "log"
        self.asst_input_mode = tk.StringVar(value=_action_driver if _action_driver in {"log", "mock", "keyboard", "wininput"} else "log")
        self.asst_target_hotkey = tk.StringVar(value=os.getenv("TARGET_HOTKEY", "").strip())
        self.asst_minimap_hotkey = tk.StringVar(value=os.getenv("MINIMAP_CLICK_HOTKEY", "").strip())

        # Live input mode (OS injection) must be explicitly armed.
        _asst_titles = ["TibiaClone", "MyClient"]
        _asst_armed = False
        try:
            _asst_cfg = self._config.assistant_snapshot()
            try:
                _asst_titles = list(getattr(_asst_cfg, "allowed_window_titles", None) or _asst_titles)
            except Exception:
                _asst_titles = _asst_titles
            try:
                _asst_armed = bool(getattr(_asst_cfg, "live_input_armed", False))
            except Exception:
                _asst_armed = False
        except Exception:
            pass

        self.asst_live_input_armed = tk.BooleanVar(value=bool(_asst_armed))
        self.asst_allowed_window_titles = tk.StringVar(value=",".join([str(x).strip() for x in _asst_titles if str(x).strip()]))

        # Idle alert (anti-stuck, sin inputs). Se aplica al iniciar el bot via env vars.
        try:
            _idle_alert_default = float(os.getenv("ASSIST_IDLE_ALERT_S", "0").strip() or "0")
        except Exception:
            _idle_alert_default = 0.0
        try:
            _idle_repeat_default = float(os.getenv("ASSIST_IDLE_REPEAT_S", "10").strip() or "10")
        except Exception:
            _idle_repeat_default = 10.0
        try:
            _ui_idle_fail_default = float(os.getenv("UI_IDLE_FAIL_S", "0").strip() or "0")
        except Exception:
            _ui_idle_fail_default = 0.0

        self.idle_alert_s = tk.DoubleVar(value=max(0.0, float(_idle_alert_default)))
        self.idle_repeat_s = tk.DoubleVar(value=max(1.0, float(_idle_repeat_default)))
        # 0 => auto (UI calcula fail como 2x warn o warn+30)
        self.ui_idle_fail_s = tk.DoubleVar(value=max(0.0, float(_ui_idle_fail_default)))

        # Replay + export JSONL
        self.replay_enabled = tk.BooleanVar(value=False)
        self.replay_interval_ms = tk.IntVar(value=2000)
        self.replay_out_dir = tk.StringVar(value=self._config.replay_snapshot().out_dir)
        self.log_enabled = tk.BooleanVar(value=False)
        self.log_interval_ms = tk.IntVar(value=250)
        self.log_out_file = tk.StringVar(value=self._config.logging_snapshot().out_file)

        # Overlay exporter (env-based; applied at bot start)
        self.overlay_enabled = tk.BooleanVar(
            value=os.getenv("OVERLAY_ENABLED", "").strip().lower() in {"1", "true", "yes"}
        )
        try:
            _overlay_interval_default = float(os.getenv("OVERLAY_INTERVAL_S", "1.0").strip() or "1.0")
        except Exception:
            _overlay_interval_default = 1.0
        self.overlay_interval_s = tk.DoubleVar(value=max(0.1, float(_overlay_interval_default)))
        self.overlay_out_dir = tk.StringVar(
            value=os.getenv("OVERLAY_OUT_DIR", "logs/debug_overlay").strip() or "logs/debug_overlay"
        )
        self.overlay_tile_grid = tk.BooleanVar(
            value=os.getenv("OVERLAY_TILE_GRID", "1").strip().lower() not in {"0", "false", "no"}
        )
        try:
            _tile_raw = os.getenv("OVERLAY_TILE_PX", "").strip() or os.getenv("TIBIA_TILE_PX", "32").strip() or "32"
            _tile_default = int(float(_tile_raw))
        except Exception:
            _tile_default = 32
        self.overlay_tile_px = tk.IntVar(value=max(4, min(128, int(_tile_default))))
        self.overlay_rois = tk.StringVar(
            value=os.getenv(
                "OVERLAY_ROIS",
                "coords_ocr,minimap_content,hp_top_ocr,mp_top_ocr,hp_low_bar,mp_low_bar,states_icons,equipment_slots,battlelist_rows",
            ).strip()
        )
        self.overlay_preset = tk.StringVar(value="Custom")
        self.telemetry_preset = tk.StringVar(value="Custom")

        # ROI config override (applied at bot start via env var)
        self.rois_config_override = tk.StringVar(value=os.getenv("ROIS_CONFIG", "").strip())

        # UI settings persistence (best-effort): load last overlay settings.
        try:
            self._load_ui_settings()
        except Exception:
            pass

        # Telemetria (solo lectura, viene del loop)
        self.hp_text = tk.StringVar(value="?")
        self.mp_text = tk.StringVar(value="?")
        self.cap_text = tk.StringVar(value="?")
        self.signals_text = tk.StringVar(value="-")
        self.target_text = tk.StringVar(value="-")
        self.reco_text = tk.StringVar(value="-")
        self.cavebot_next_text = tk.StringVar(value="-")
        self.cavebot_wp_text = tk.StringVar(value="-")
        self.cavebot_action_text = tk.StringVar(value="-")
        self.input_plan_text = tk.StringVar(value="-")

        # Route/Cavebot editor state
        self.route_steps: list = []  # list[WaypointStep]
        self.route_path_var = tk.StringVar(value="")
        self.route_setup_path_var = tk.StringVar(value="")
        self.route_status_var = tk.StringVar(value="-")
        self.route_setup_cfg: Any = None
        self.hc_mana_name = tk.StringVar(value="")
        self.hc_take_mana = tk.IntVar(value=0)
        self.hc_mana_leave = tk.IntVar(value=0)
        self.hc_cap_leave = tk.IntVar(value=0)
        self.item_name_var = tk.StringVar(value="")
        self.item_hotkey_var = tk.StringVar(value="")
        self.item_use_var = tk.StringVar(value="self")

        # System tray (pystray)
        self._tray_icon = None
        self._tray_thread: threading.Thread | None = None
        self._tray_image = None
        self._allow_close = False

        container = tk.Frame(self.root, padx=14, pady=14)
        container.pack(fill="both", expand=True)

        notebook = ttk.Notebook(container)
        notebook.pack(fill="both", expand=True)

        tab_control = tk.Frame(notebook)
        tab_healing = tk.Frame(notebook)
        tab_cavebot = tk.Frame(notebook)
        tab_tools = tk.Frame(notebook)
        tab_config = tk.Frame(notebook)
        tab_routes = tk.Frame(notebook)

        notebook.add(tab_control, text="Control")
        notebook.add(tab_healing, text="Healing")
        notebook.add(tab_cavebot, text="Cavebot")
        notebook.add(tab_tools, text="Herramientas")
        notebook.add(tab_config, text="Configuracion")
        notebook.add(tab_routes, text="Rutas / Cavebot")

        # --- TAB: Control ---
        tk.Label(tab_control, text="Estado:").grid(row=0, column=0, sticky="w")
        tk.Label(tab_control, textvariable=self.status_var, width=22, anchor="w").grid(row=0, column=1, sticky="w")

        self.start_btn = tk.Button(tab_control, text="Iniciar", width=12, command=self.start)
        self.stop_btn = tk.Button(tab_control, text="Parar", width=12, command=self.stop, state="disabled")
        self.tray_btn = tk.Button(tab_control, text="Minimizar", width=12, command=self._minimize_to_tray)

        self.start_btn.grid(row=1, column=0, pady=(10, 0), sticky="w")
        self.stop_btn.grid(row=1, column=1, pady=(10, 0), sticky="e")
        self.tray_btn.grid(row=1, column=2, pady=(10, 0), sticky="e")

        ctrl_info = tk.Frame(tab_control)
        ctrl_info.grid(row=2, column=0, columnspan=4, sticky="w", pady=(10, 0))
        ctrl_info_left = tk.Frame(ctrl_info)
        ctrl_info_left.grid(row=0, column=0, sticky="nw", padx=(0, 12))
        ctrl_info_right = tk.Frame(ctrl_info)
        ctrl_info_right.grid(row=0, column=1, sticky="nw")

        tk.Label(ctrl_info_left, text="HP:").grid(row=0, column=0, sticky="w")
        tk.Label(ctrl_info_left, textvariable=self.hp_text, width=22, anchor="w").grid(row=0, column=1, sticky="w")

        tk.Label(ctrl_info_left, text="MP:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        tk.Label(ctrl_info_left, textvariable=self.mp_text, width=22, anchor="w").grid(row=1, column=1, sticky="w", pady=(6, 0))

        tk.Label(ctrl_info_left, text="Cap:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        tk.Label(ctrl_info_left, textvariable=self.cap_text, width=22, anchor="w").grid(row=2, column=1, sticky="w", pady=(6, 0))

        tk.Label(ctrl_info_left, text="Senales:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        tk.Label(ctrl_info_left, textvariable=self.signals_text, width=32, anchor="w").grid(row=3, column=1, sticky="w", pady=(6, 0))

        tk.Label(ctrl_info_right, text="Target:").grid(row=0, column=0, sticky="w")
        tk.Label(ctrl_info_right, textvariable=self.target_text, width=36, anchor="w").grid(row=0, column=1, sticky="w")

        tk.Label(ctrl_info_right, text="Recomendacion:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        tk.Label(ctrl_info_right, textvariable=self.reco_text, width=36, anchor="w").grid(row=1, column=1, sticky="w", pady=(6, 0))

        tk.Label(ctrl_info_right, text="Waypoint:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        tk.Label(ctrl_info_right, textvariable=self.cavebot_wp_text, width=36, anchor="w").grid(row=2, column=1, sticky="w", pady=(6, 0))

        tk.Label(ctrl_info_right, text="Estado stream:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        tk.Label(ctrl_info_right, textvariable=self.stale_var, width=36, anchor="w").grid(row=3, column=1, sticky="w", pady=(6, 0))

        ctrl_diag = tk.Frame(tab_control)
        ctrl_diag.grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        diag_left = tk.Frame(ctrl_diag)
        diag_left.grid(row=0, column=0, sticky="nw", padx=(0, 12))
        diag_right = tk.Frame(ctrl_diag)
        diag_right.grid(row=0, column=1, sticky="nw")

        tk.Label(diag_left, text="Health:").grid(row=0, column=0, sticky="w")
        self._health_status_label = tk.Label(diag_left, textvariable=self.health_status_var, width=8, anchor="w")
        self._health_status_label.grid(row=0, column=1, sticky="w")
        self._health_detail_label = tk.Label(diag_left, textvariable=self.health_var, width=40, anchor="w")
        self._health_detail_label.grid(row=0, column=2, sticky="w")

        tk.Label(diag_left, text="Anchor:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._anchor_status_label = tk.Label(diag_left, textvariable=self.anchor_status_var, width=8, anchor="w")
        self._anchor_status_label.grid(row=1, column=1, sticky="w", pady=(6, 0))
        self._anchor_detail_label = tk.Label(diag_left, textvariable=self.anchor_var, width=40, anchor="w")
        self._anchor_detail_label.grid(row=1, column=2, sticky="w", pady=(6, 0))

        tk.Label(diag_right, text="Idle:").grid(row=0, column=0, sticky="w")
        self._idle_status_label = tk.Label(diag_right, textvariable=self.idle_status_var, width=8, anchor="w")
        self._idle_status_label.grid(row=0, column=1, sticky="w")
        self._idle_detail_label = tk.Label(diag_right, textvariable=self.idle_var, width=40, anchor="w")
        self._idle_detail_label.grid(row=0, column=2, sticky="w")

        tk.Label(diag_right, text="Stuck:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._stuck_status_label = tk.Label(diag_right, textvariable=self.stuck_status_var, width=8, anchor="w")
        self._stuck_status_label.grid(row=1, column=1, sticky="w", pady=(6, 0))
        self._stuck_detail_label = tk.Label(diag_right, textvariable=self.stuck_var, width=40, anchor="w")
        self._stuck_detail_label.grid(row=1, column=2, sticky="w", pady=(6, 0))

        # Panel operador: ultimos eventos
        tk.Label(tab_control, text="Eventos (ultimos):").grid(row=4, column=0, sticky="w", pady=(6, 0))

        def _copy_inputs_to_clipboard() -> None:
            # Prefer planned inputs; fall back to action_request.
            txt = str(getattr(self, "_latest_input_plan", "") or "").strip()
            if not txt:
                txt = str(getattr(self, "_latest_action_requests_display", "") or "").strip()
            if not txt:
                txt = str(getattr(self, "_last_event_action_req", "") or "").strip()
            if not txt:
                try:
                    self._messagebox.showinfo("Copiar", "No hay inputs planeados para copiar.")
                except Exception:
                    pass
                return
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(txt)
                self.root.update()
                self._messagebox.showinfo("Copiar", "Copiado al portapapeles.")
            except Exception:
                try:
                    self._messagebox.showinfo("Copiar", "No se pudo copiar al portapapeles.")
                except Exception:
                    pass

        tk.Button(tab_control, text="Copiar inputs", width=14, command=_copy_inputs_to_clipboard).grid(
            row=4, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(tab_control, text="Inputs / Actions:").grid(row=4, column=2, sticky="w", pady=(6, 0))
        tk.Label(
            tab_control,
            textvariable=self.input_plan_text,
            width=52,
            anchor="nw",
            justify="left",
            font=("Consolas", 9),
        ).grid(
            row=4, column=3, sticky="w", pady=(6, 2)
        )

        self._events_text = tk.Text(tab_control, height=8, width=80, wrap="none", font=("Consolas", 9))
        try:
            self._events_text.configure(state="disabled")
        except Exception:
            pass
        self._events_text.grid(row=5, column=0, columnspan=5, sticky="w", pady=(4, 0))

        def _monitor_default() -> int:
            raw = os.getenv("FORCE_MONITOR", "2").strip() or "2"
            try:
                return int(raw)
            except Exception:
                return 2

        def _run_tool_async(
            cmd: list[str],
            *,
            title: str,
            open_dir: str | None = None,
            on_complete=None,
        ) -> None:
            if not cmd:
                return

            def _extract_out_dir(output: str) -> str | None:
                try:
                    for line in (output or "").splitlines():
                        if line.strip().startswith("OUT_DIR:"):
                            return line.split(":", 1)[1].strip()
                except Exception:
                    return None
                return None

            def _format_report_summary(out_dir_path: str) -> str | None:
                try:
                    p = Path(out_dir_path) / "report.json"
                    if not p.exists():
                        return None
                    import json

                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and "summary" in data and "checks" in data:
                        # ROI sanity report
                        s = data.get("summary") or {}
                        ok = s.get("ok")
                        warn = s.get("warn")
                        fail = s.get("fail")
                        lines = [f"Summary: OK={ok} WARN={warn} FAIL={fail}"]
                        # show a few warnings/fails
                        shown = 0
                        for c in (data.get("checks") or []):
                            try:
                                status = str(c.get("status") or "")
                                if status not in {"WARN", "FAIL"}:
                                    continue
                                name = str(c.get("name") or "")
                                reason = str(c.get("reason") or "")
                                lines.append(f"{status}: {name}: {reason}")
                                shown += 1
                                if shown >= 6:
                                    break
                            except Exception:
                                continue
                        return "\n".join(lines)

                    if isinstance(data, dict) and "ocr" in data:
                        # OCR sanity report
                        ocr = data.get("ocr") or {}
                        pres = data.get("presence") or {}
                        hp = f"{ocr.get('hp_current')}/{ocr.get('hp_max')}"
                        mp = f"{ocr.get('mp_current')}/{ocr.get('mp_max')}"
                        capv = ocr.get("cap_current")
                        coords = ocr.get("coords")
                        lines = [f"HP: {hp}", f"MP: {mp}", f"Cap: {capv}"]
                        if coords is not None:
                            lines.append(f"Coords: {coords}")
                        lines.append(
                            f"Ring: {pres.get('ring_equipped')} | Amulet: {pres.get('amulet_equipped')} | Hungry: {pres.get('hungry')}"
                        )
                        return "\n".join(lines)

                    if isinstance(data, dict) and "coords_sanity" in data:
                        cs = data.get("coords_sanity") or {}
                        ok = cs.get("ok")
                        samples = cs.get("samples")
                        ok_rate = cs.get("ok_rate")
                        max_jump = cs.get("max_jump")
                        warn_jumps = cs.get("warn_jumps")
                        fail_jumps = cs.get("fail_jumps")
                        lines = [
                            f"Coords: ok={ok}/{samples} ({0 if ok_rate is None else float(ok_rate):.0%})",
                            f"Jumps: max={max_jump} warn={warn_jumps} fail={fail_jumps}",
                        ]
                        last = cs.get("last_coords")
                        if last is not None:
                            lines.append(f"Last: {last}")
                        return "\n".join(lines)

                    if isinstance(data, dict) and "anchor" in data:
                        # Anchor sanity report
                        a = data.get("anchor") or {}
                        name = str(a.get("roi_name") or "")
                        dx = a.get("dx_src_px")
                        dy = a.get("dy_src_px")
                        sc = a.get("score")
                        lines = [f"Anchor ROI: {name}", f"dx: {dx} | dy: {dy} | score: {sc}"]
                        return "\n".join(lines)
                except Exception:
                    return None
                return None

            def _format_report_short(out_dir_path: str) -> str | None:
                try:
                    s = _format_report_summary(out_dir_path)
                    if not s:
                        return None
                    first = s.splitlines()[0].strip()
                    return first or None
                except Exception:
                    return None

            def worker() -> None:
                try:
                    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(self._repo_root))
                    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
                    ok = proc.returncode == 0
                except Exception as e:
                    out = str(e)
                    ok = False

                tool_out_dir = _extract_out_dir(out)
                summary = _format_report_summary(tool_out_dir) if tool_out_dir else None
                summary_short = _format_report_short(tool_out_dir) if tool_out_dir else None

                def done() -> None:
                    try:
                        # Let callers persist the exact OUT_DIR for later buttons.
                        try:
                            if on_complete is not None:
                                on_complete(tool_out_dir, ok, out, summary_short)
                        except Exception:
                            pass

                        # Prefer opening the exact run folder.
                        chosen_open = tool_out_dir or open_dir
                        if chosen_open:
                            try:
                                os.makedirs(chosen_open, exist_ok=True)
                                os.startfile(os.path.abspath(chosen_open))
                            except Exception:
                                pass
                        msg = (summary or out).strip() or ("OK" if ok else "FAIL")
                        if ok:
                            self._messagebox.showinfo(title, msg)
                        else:
                            self._messagebox.showerror(title, msg)
                    except Exception:
                        pass

                try:
                    self.root.after(0, done)
                except Exception:
                    pass

            threading.Thread(target=worker, daemon=True).start()

        def run_roi_sanity_ui() -> None:
            rois_path = str(self.rois_config_override.get()).strip()
            out_dir = str(self._repo_root / "logs" / "roi_sanity_ui")
            cmd = [
                sys.executable,
                str(self._repo_root / "tools" / "roi_sanity_check.py"),
                "--monitor",
                str(_monitor_default()),
                "--out-dir",
                out_dir,
                "--save-overlay",
            ]
            if rois_path:
                cmd += ["--rois", rois_path]

            def _on_complete(tool_out_dir: str | None, ok: bool, out: str, summary_short: str | None) -> None:
                if tool_out_dir:
                    self._last_roi_sanity_dir = tool_out_dir
                    try:
                        self.last_roi_dir_var.set(str(tool_out_dir))
                        self.last_roi_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
                    except Exception:
                        pass
                    return
                # Fallback: best-effort newest folder.
                try:
                    base = Path(out_dir)
                    if not base.exists():
                        return
                    dirs = [p for p in base.iterdir() if p.is_dir()]
                    if not dirs:
                        return
                    newest = max(dirs, key=lambda p: p.stat().st_mtime)
                    self._last_roi_sanity_dir = str(newest)
                    try:
                        self.last_roi_dir_var.set(str(newest))
                        self.last_roi_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
                    except Exception:
                        pass
                except Exception:
                    pass

            _run_tool_async(cmd, title="ROI Sanity Check", open_dir=out_dir, on_complete=_on_complete)

        def run_ocr_sanity_ui() -> None:
            rois_path = str(self.rois_config_override.get()).strip()
            out_dir = str(self._repo_root / "logs" / "ocr_sanity_ui")
            cmd = [
                sys.executable,
                str(self._repo_root / "tools" / "ocr_sanity_check.py"),
                "--monitor",
                str(_monitor_default()),
                "--out-dir",
                out_dir,
                "--save-overlay",
                "--save-crops",
            ]
            if rois_path:
                cmd += ["--rois", rois_path]

            def _on_complete(tool_out_dir: str | None, ok: bool, out: str, summary_short: str | None) -> None:
                if tool_out_dir:
                    self._last_ocr_sanity_dir = tool_out_dir
                    try:
                        self.last_ocr_dir_var.set(str(tool_out_dir))
                        self.last_ocr_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
                    except Exception:
                        pass
                    return
                try:
                    base = Path(out_dir)
                    if not base.exists():
                        return
                    dirs = [p for p in base.iterdir() if p.is_dir()]
                    if not dirs:
                        return
                    newest = max(dirs, key=lambda p: p.stat().st_mtime)
                    self._last_ocr_sanity_dir = str(newest)
                    try:
                        self.last_ocr_dir_var.set(str(newest))
                        self.last_ocr_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
                    except Exception:
                        pass
                except Exception:
                    pass

            _run_tool_async(cmd, title="OCR Sanity Check", open_dir=out_dir, on_complete=_on_complete)

        def run_coords_sanity_ui() -> None:
            rois_path = str(self.rois_config_override.get()).strip()
            out_dir = str(self._repo_root / "logs" / "coords_sanity_ui")
            cmd = [
                sys.executable,
                str(self._repo_root / "tools" / "coords_sanity_check.py"),
                "--monitor",
                str(_monitor_default()),
                "--out-dir",
                out_dir,
                "--save-overlay",
                "--save-crops",
            ]
            if rois_path:
                cmd += ["--rois", rois_path]

            def _on_complete(tool_out_dir: str | None, ok: bool, out: str, summary_short: str | None) -> None:
                if tool_out_dir:
                    self._last_coords_sanity_dir = tool_out_dir
                    try:
                        self.last_coords_dir_var.set(str(tool_out_dir))
                        self.last_coords_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
                    except Exception:
                        pass
                    return
                try:
                    base = Path(out_dir)
                    if not base.exists():
                        return
                    dirs = [p for p in base.iterdir() if p.is_dir()]
                    if not dirs:
                        return
                    newest = max(dirs, key=lambda p: p.stat().st_mtime)
                    self._last_coords_sanity_dir = str(newest)
                    try:
                        self.last_coords_dir_var.set(str(newest))
                        self.last_coords_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
                    except Exception:
                        pass
                except Exception:
                    pass

            _run_tool_async(cmd, title="Coords Sanity Check", open_dir=out_dir, on_complete=_on_complete)

        def run_anchor_sanity_ui() -> None:
            rois_path = str(self.rois_config_override.get()).strip()
            out_dir = str(self._repo_root / "logs" / "anchor_sanity_ui")
            cmd = [
                sys.executable,
                str(self._repo_root / "tools" / "anchor_sanity_check.py"),
                "--monitor",
                str(_monitor_default()),
                "--out-dir",
                out_dir,
                "--save-overlay",
            ]
            if rois_path:
                cmd += ["--rois", rois_path]

            def _on_complete(tool_out_dir: str | None, ok: bool, out: str, summary_short: str | None) -> None:
                if tool_out_dir:
                    self._last_anchor_sanity_dir = tool_out_dir
                    try:
                        self.last_anchor_dir_var.set(str(tool_out_dir))
                        self.last_anchor_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
                    except Exception:
                        pass
                    return
                # Fallback: newest folder.
                try:
                    base = Path(out_dir)
                    if not base.exists():
                        return
                    dirs = [p for p in base.iterdir() if p.is_dir()]
                    if not dirs:
                        return
                    newest = max(dirs, key=lambda p: p.stat().st_mtime)
                    self._last_anchor_sanity_dir = str(newest)
                    try:
                        self.last_anchor_dir_var.set(str(newest))
                        self.last_anchor_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
                    except Exception:
                        pass
                except Exception:
                    pass

            _run_tool_async(cmd, title="Anchor Sanity Check", open_dir=out_dir, on_complete=_on_complete)

        def run_anchor_setup_ui() -> None:
            # Interactive: lets you select a stable on-screen anchor and writes it into the ROIs config.
            rois_path = str(self.rois_config_override.get()).strip()
            if not rois_path:
                # If no override is selected, create_anchor_template will pick by resolution.
                rois_path = ""

            # Default template location (repo-relative)
            out_template = str(Path("data") / "anchors" / "hud_anchor.png")

            cmd = [
                sys.executable,
                str(self._repo_root / "tools" / "create_anchor_template.py"),
                "--monitor",
                str(_monitor_default()),
                "--base",
                "frame",
                "--out",
                out_template,
                "--write",
            ]

            # If user selected a specific ROIs file, we want to write into that exact file.
            # The tool currently chooses by resolution, so we pass via env ROIS_CONFIG.
            try:
                if rois_path:
                    os.environ["ROIS_CONFIG"] = rois_path
            except Exception:
                pass

            _run_tool_async(cmd, title="Anchor Setup", open_dir=str(self._repo_root / "data" / "anchors"))

        def _open_last_artifact(which: str, kind: str) -> None:
            try:
                if kind == "roi":
                    last_dir = self._last_roi_sanity_dir
                elif kind == "ocr":
                    last_dir = self._last_ocr_sanity_dir
                elif kind == "coords":
                    last_dir = self._last_coords_sanity_dir
                else:
                    last_dir = self._last_anchor_sanity_dir
                if not last_dir:
                    self._messagebox.showinfo("Info", "Aun no hay un run reciente.")
                    return
                p = Path(last_dir) / ("overlay.png" if which == "overlay" else "report.json")
                if not p.exists():
                    self._messagebox.showinfo("Info", f"No existe: {p}")
                    return
                os.startfile(os.path.abspath(str(p)))
            except Exception:
                pass

        # --- TAB: Herramientas ---
        tk.Label(tab_tools, text="Sanity checks").grid(row=0, column=0, sticky="w", pady=(4, 0))
        tk.Button(tab_tools, text="ROI sanity", width=12, command=run_roi_sanity_ui).grid(
            row=1, column=1, sticky="w", pady=(4, 0)
        )
        tk.Button(tab_tools, text="OCR test", width=12, command=run_ocr_sanity_ui).grid(
            row=1, column=2, sticky="w", padx=(8, 0), pady=(4, 0)
        )
        tk.Button(tab_tools, text="Coords OCR test", width=14, command=run_coords_sanity_ui).grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        tk.Button(tab_tools, text="Anchor test", width=12, command=run_anchor_sanity_ui).grid(
            row=1, column=3, sticky="w", padx=(8, 0), pady=(4, 0)
        )
        tk.Button(tab_tools, text="Anchor setup", width=12, command=run_anchor_setup_ui).grid(
            row=1, column=4, sticky="w", padx=(8, 0), pady=(4, 0)
        )

        tk.Label(tab_tools, text="Abrir artefactos").grid(row=2, column=0, sticky="w", pady=(10, 0))
        tk.Button(tab_tools, text="ROI overlay", width=12, command=lambda: _open_last_artifact("overlay", "roi")).grid(
            row=3, column=1, sticky="w", pady=(4, 0)
        )
        tk.Button(tab_tools, text="ROI report", width=12, command=lambda: _open_last_artifact("report", "roi")).grid(
            row=3, column=2, sticky="w", padx=(8, 0), pady=(4, 0)
        )
        tk.Button(tab_tools, text="Coords overlay", width=14, command=lambda: _open_last_artifact("overlay", "coords")).grid(
            row=3, column=0, sticky="w", pady=(4, 0)
        )
        tk.Button(tab_tools, text="Coords report", width=14, command=lambda: _open_last_artifact("report", "coords")).grid(
            row=4, column=0, sticky="w", pady=(4, 0)
        )
        tk.Button(tab_tools, text="Anchor overlay", width=12, command=lambda: _open_last_artifact("overlay", "anchor")).grid(
            row=3, column=3, sticky="w", padx=(8, 0), pady=(4, 0)
        )
        tk.Button(tab_tools, text="Anchor report", width=12, command=lambda: _open_last_artifact("report", "anchor")).grid(
            row=4, column=3, sticky="w", padx=(8, 0), pady=(4, 0)
        )
        tk.Button(tab_tools, text="OCR overlay", width=12, command=lambda: _open_last_artifact("overlay", "ocr")).grid(
            row=4, column=1, sticky="w", pady=(4, 0)
        )
        tk.Button(tab_tools, text="OCR report", width=12, command=lambda: _open_last_artifact("report", "ocr")).grid(
            row=4, column=2, sticky="w", padx=(8, 0), pady=(4, 0)
        )

        tk.Label(tab_tools, text="Ultimos resultados").grid(row=5, column=0, sticky="w", pady=(10, 0))
        tk.Label(tab_tools, text="Ultimo ROI:").grid(row=6, column=0, sticky="w", pady=(4, 0))
        tk.Label(tab_tools, textvariable=self.last_roi_summary_var, width=22, anchor="w").grid(
            row=6, column=1, sticky="w", pady=(4, 0)
        )
        tk.Label(tab_tools, textvariable=self.last_roi_dir_var, width=52, anchor="w").grid(
            row=7, column=1, columnspan=3, sticky="w"
        )

        tk.Label(tab_tools, text="Ultimo OCR:").grid(row=8, column=0, sticky="w", pady=(4, 0))
        tk.Label(tab_tools, textvariable=self.last_ocr_summary_var, width=22, anchor="w").grid(
            row=8, column=1, sticky="w", pady=(4, 0)
        )
        tk.Label(tab_tools, textvariable=self.last_ocr_dir_var, width=52, anchor="w").grid(
            row=9, column=1, columnspan=3, sticky="w"
        )

        tk.Label(tab_tools, text="Ultimo Coords:").grid(row=10, column=0, sticky="w", pady=(4, 0))
        tk.Label(tab_tools, textvariable=self.last_coords_summary_var, width=22, anchor="w").grid(
            row=10, column=1, sticky="w", pady=(4, 0)
        )
        tk.Label(tab_tools, textvariable=self.last_coords_dir_var, width=52, anchor="w").grid(
            row=11, column=1, columnspan=3, sticky="w"
        )

        tk.Label(tab_tools, text="Ultimo Anchor:").grid(row=12, column=0, sticky="w", pady=(4, 0))
        tk.Label(tab_tools, textvariable=self.last_anchor_summary_var, width=22, anchor="w").grid(
            row=12, column=1, sticky="w", pady=(4, 0)
        )
        tk.Label(tab_tools, textvariable=self.last_anchor_dir_var, width=52, anchor="w").grid(
            row=13, column=1, columnspan=3, sticky="w"
        )

        # --- TAB: Healing ---
        tk.Checkbutton(tab_healing, text="Habilitar healing", variable=self.healing_enabled).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )

        heal_frame = tk.Frame(tab_healing)
        heal_frame.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        heal_left = tk.Frame(heal_frame)
        heal_left.grid(row=0, column=0, sticky="nw", padx=(0, 12))
        heal_right = tk.Frame(heal_frame)
        heal_right.grid(row=0, column=1, sticky="nw")

        tk.Label(heal_left, text="Curar si HP < (%)").grid(row=0, column=0, sticky="w")
        tk.Spinbox(heal_left, from_=1, to=100, textvariable=self.heal_hp_below_pct, width=6).grid(
            row=0, column=1, sticky="w"
        )

        tk.Label(heal_left, text="Recuperar HP a (%)").grid(row=1, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(heal_left, from_=1, to=100, textvariable=self.heal_hp_recover_pct, width=6).grid(
            row=1, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(heal_left, text="Accion HP (F1-F12)").grid(row=2, column=0, sticky="w", pady=(6, 0))
        f_keys = [f"F{i}" for i in range(1, 13)]
        ttk.Combobox(heal_left, textvariable=self.heal_hp_action, values=f_keys, width=8, state="normal").grid(
            row=2, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(heal_left, text="Cooldown (s)").grid(row=3, column=0, sticky="w", pady=(6, 0))
        tk.Entry(heal_left, textvariable=self.heal_cooldown_s, width=8).grid(row=3, column=1, sticky="w", pady=(6, 0))

        tk.Label(heal_right, text="Curar si MP < (%)").grid(row=0, column=0, sticky="w")
        tk.Spinbox(heal_right, from_=0, to=100, textvariable=self.heal_mp_below_pct, width=6).grid(
            row=0, column=1, sticky="w"
        )

        tk.Label(heal_right, text="Recuperar MP a (%)").grid(row=1, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(heal_right, from_=0, to=100, textvariable=self.heal_mp_recover_pct, width=6).grid(
            row=1, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(heal_right, text="Accion MP (F1-F12)").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(heal_right, textvariable=self.heal_mp_action, values=f_keys, width=8, state="normal").grid(
            row=2, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(heal_right, text="Accion generica (fallback)").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(heal_right, textvariable=self.heal_action, values=f_keys, width=8, state="normal").grid(
            row=3, column=1, sticky="w", pady=(6, 0)
        )

        # --- TAB: Cavebot ---
        cb_top = tk.Frame(tab_cavebot)
        cb_top.grid(row=0, column=0, columnspan=4, sticky="w")
        cb_left = tk.Frame(cb_top)
        cb_left.grid(row=0, column=0, sticky="nw", padx=(0, 12))
        cb_right = tk.Frame(cb_top)
        cb_right.grid(row=0, column=1, sticky="nw")

        tk.Checkbutton(cb_left, text="Habilitar cavebot", variable=self.cavebot_enabled).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )

        tk.Label(cb_left, text="Ruta (JSON)").grid(row=1, column=0, sticky="w", pady=(10, 0))
        tk.Entry(cb_left, textvariable=self.cavebot_route_path, width=32).grid(
            row=1, column=1, sticky="w", pady=(10, 0)
        )

        def browse_route() -> None:
            try:
                from tkinter import filedialog

                base = Path(__file__).resolve().parent / "scripts-master"
                if not base.is_dir():
                    base = Path(__file__).resolve().parent / "configs"
                path = filedialog.askopenfilename(
                    title="Selecciona ruta/script JSON",
                    initialdir=str(base),
                    filetypes=[("Routes", "*.in *.json"), ("waypoints", "waypoints.in"), ("JSON", "*.json"), ("All files", "*")],
                )
                if path:
                    self.cavebot_route_path.set(path)
            except Exception:
                pass

        tk.Button(cb_left, text="Browse", width=8, command=browse_route).grid(
            row=1, column=2, sticky="w", padx=(8, 0), pady=(10, 0)
        )

        tk.Label(cb_left, text="Modo:").grid(row=2, column=0, sticky="w", pady=(8, 0))
        tk.Radiobutton(cb_left, text="Steps (sin coords)", variable=self.cavebot_mode, value="steps").grid(
            row=2, column=1, sticky="w", pady=(8, 0)
        )
        tk.Radiobutton(cb_left, text="Pos (con coords)", variable=self.cavebot_mode, value="pos").grid(
            row=2, column=2, sticky="w", pady=(8, 0)
        )

        tk.Checkbutton(cb_left, text="Loop ruta", variable=self.cavebot_loop).grid(
            row=2, column=3, sticky="w", pady=(8, 0), padx=(8, 0)
        )

        tk.Label(cb_left, text="Coords provider:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        tk.Radiobutton(cb_left, text="disabled", variable=self.coords_provider, value="disabled").grid(
            row=3, column=1, sticky="w", pady=(6, 0)
        )
        tk.Radiobutton(cb_left, text="ocr", variable=self.coords_provider, value="ocr").grid(
            row=3, column=2, sticky="w", pady=(6, 0)
        )
        tk.Radiobutton(cb_left, text="minimap_motion", variable=self.coords_provider, value="minimap").grid(
            row=3, column=3, sticky="w", pady=(6, 0)
        )

        # Live provider status (text/semáforo)
        tk.Label(cb_left, text="Provider status:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        _lbl = tk.Label(cb_left, textvariable=self.coords_provider_state_text, width=34, anchor="w")
        _lbl.grid(row=4, column=1, columnspan=3, sticky="w", pady=(6, 0))
        try:
            self._coords_provider_state_label = _lbl
        except Exception:
            pass

        # Minimap calibration (seed) + fallback policy (persisted in UI config)
        tk.Label(cb_left, text="Minimap seed X/Y/Z:").grid(row=5, column=0, sticky="w", pady=(6, 0))
        tk.Entry(cb_left, textvariable=self.minimap_seed_x, width=6).grid(row=5, column=1, sticky="w", pady=(6, 0))
        tk.Entry(cb_left, textvariable=self.minimap_seed_y, width=6).grid(row=5, column=2, sticky="w", pady=(6, 0))
        tk.Entry(cb_left, textvariable=self.minimap_seed_z, width=6).grid(row=5, column=3, sticky="w", pady=(6, 0))

        tk.Label(cb_left, text="Minimap fallback:").grid(row=6, column=0, sticky="w", pady=(4, 0))
        ttk.Combobox(
            cb_left,
            textvariable=self.minimap_fallback_mode,
            values=["steps", "ocr"],
            width=8,
            state="readonly",
        ).grid(row=6, column=1, sticky="w", pady=(4, 0))
        tk.Label(cb_left, text="conf<").grid(row=6, column=2, sticky="e", pady=(4, 0))
        tk.Entry(cb_left, textvariable=self.minimap_fallback_conf_threshold, width=6).grid(
            row=6, column=3, sticky="w", pady=(4, 0)
        )
        tk.Label(cb_left, text="N ticks").grid(row=7, column=2, sticky="e", pady=(2, 0))
        tk.Entry(cb_left, textvariable=self.minimap_fallback_n_ticks, width=6).grid(row=7, column=3, sticky="w", pady=(2, 0))

        def _start_cavebot_ui() -> None:
            try:
                self._cavebot_follow_ui = True
            except Exception:
                pass
            try:
                self.cavebot_enabled.set(True)
            except Exception:
                pass
            # Reset selection to start following from the beginning.
            try:
                self._route_current_index = None
                self._route_listbox.selection_clear(0, "end")
                if self._route_items:
                    self._route_listbox.selection_set(0)
                    self._route_listbox.see(0)
            except Exception:
                pass

        def _stop_cavebot_ui() -> None:
            try:
                self._cavebot_follow_ui = False
            except Exception:
                pass
            try:
                self.cavebot_enabled.set(False)
            except Exception:
                pass
            try:
                self._route_current_index = None
                self._route_listbox.selection_clear(0, "end")
                self._route_next_var.set("-")
            except Exception:
                pass

        tk.Button(cb_left, text="Iniciar cavebot", width=14, command=_start_cavebot_ui).grid(
            row=8, column=0, sticky="w", pady=(10, 0)
        )
        tk.Button(cb_left, text="Detener cavebot", width=14, command=_stop_cavebot_ui).grid(
            row=8, column=1, sticky="w", padx=(8, 0), pady=(10, 0)
        )

        tk.Label(cb_right, text="Paso:").grid(row=0, column=0, sticky="w")
        tk.Label(cb_right, textvariable=self.cavebot_step_text, width=36, anchor="w").grid(
            row=0, column=1, sticky="w"
        )

        tk.Label(cb_right, text="Proxima accion:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        tk.Label(cb_right, textvariable=self.cavebot_next_text, width=36, anchor="w").grid(
            row=1, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(cb_right, text="Waypoint:").grid(row=2, column=0, sticky="w", pady=(6, 0))
        tk.Label(cb_right, textvariable=self.cavebot_wp_text, width=36, anchor="w").grid(
            row=2, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(cb_right, text="Action:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        tk.Label(cb_right, textvariable=self.cavebot_action_text, width=36, anchor="w").grid(
            row=3, column=1, sticky="w", pady=(6, 0)
        )

        def request_advance() -> None:
            try:
                self._config.request_advance()
            except Exception:
                pass

        self._cavebot_mark_btn = tk.Button(cb_right, text="Marcar como ejecutado", width=18, command=request_advance)
        self._cavebot_mark_btn.grid(row=4, column=0, sticky="w", pady=(10, 0))
        self._cavebot_next_btn = tk.Button(cb_right, text="Siguiente accion", width=14, command=request_advance)
        self._cavebot_next_btn.grid(row=4, column=1, sticky="w", pady=(10, 0))

        inj = tk.LabelFrame(cb_right, text="Injection Status", padx=8, pady=6)
        inj.grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))
        tk.Label(inj, text="STEP").grid(row=0, column=0, sticky="w")
        tk.Label(inj, textvariable=self.injection_step_var, width=34, anchor="w").grid(row=0, column=1, sticky="w")
        tk.Label(inj, text="LABEL").grid(row=1, column=0, sticky="w", pady=(2, 0))
        tk.Label(inj, textvariable=self.injection_label_var, width=34, anchor="w").grid(row=1, column=1, sticky="w", pady=(2, 0))
        tk.Label(inj, text="NEXT").grid(row=2, column=0, sticky="w", pady=(2, 0))
        tk.Label(inj, textvariable=self.injection_next_var, width=34, anchor="w").grid(row=2, column=1, sticky="w", pady=(2, 0))
        tk.Label(inj, text="STATE").grid(row=3, column=0, sticky="w", pady=(2, 0))
        tk.Label(inj, textvariable=self.injection_state_var, width=34, anchor="w").grid(row=3, column=1, sticky="w", pady=(2, 0))
        tk.Label(inj, text="REASON").grid(row=4, column=0, sticky="w", pady=(2, 0))
        tk.Label(inj, textvariable=self.injection_reason_var, width=34, anchor="w").grid(row=4, column=1, sticky="w", pady=(2, 0))
        tk.Label(inj, text="MODE/DRIVER").grid(row=5, column=0, sticky="w", pady=(2, 0))
        tk.Label(inj, textvariable=self.injection_mode_driver_var, width=34, anchor="w").grid(row=5, column=1, sticky="w", pady=(2, 0))

        tk.Label(inj, text="FOREGROUND").grid(row=6, column=0, sticky="w", pady=(2, 0))
        tk.Label(inj, textvariable=self.injection_foreground_var, width=34, anchor="w").grid(row=6, column=1, sticky="w", pady=(2, 0))
        tk.Label(inj, text="BLOCK").grid(row=7, column=0, sticky="w", pady=(2, 0))
        tk.Label(inj, textvariable=self.injection_block_reason_var, width=34, anchor="w").grid(row=7, column=1, sticky="w", pady=(2, 0))

        cb_block = tk.Frame(tab_cavebot)
        cb_block.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))

        tk.Label(
            cb_block,
            text="(Default: solo logs. Live input solo si esta ARMED + ventana allowlisted + accion COMMITTED)",
        ).grid(row=0, column=0, columnspan=8, sticky="w")

        # Stop conditions
        tk.Checkbutton(cb_block, text="Stop por cap baja", variable=self.cavebot_stop_on_low_cap).grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        tk.Label(cb_block, text="Cap <=").grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(4, 0))
        tk.Spinbox(cb_block, from_=0, to=9999, textvariable=self.cavebot_cap_threshold, width=6).grid(
            row=1, column=2, sticky="w", pady=(4, 0)
        )

        tk.Checkbutton(cb_block, text="Stop por pociones", variable=self.cavebot_stop_on_low_potions).grid(
            row=1, column=3, sticky="w", padx=(14, 0), pady=(4, 0)
        )
        tk.Label(cb_block, text="Rem=").grid(row=1, column=4, sticky="w", padx=(8, 0), pady=(4, 0))
        tk.Spinbox(cb_block, from_=0, to=9999, textvariable=self.cavebot_potions_remaining, width=6).grid(
            row=1, column=5, sticky="w", pady=(4, 0)
        )
        tk.Label(cb_block, text="Min=").grid(row=1, column=6, sticky="w", padx=(8, 0), pady=(4, 0))
        tk.Spinbox(cb_block, from_=0, to=9999, textvariable=self.cavebot_potions_min, width=6).grid(
            row=1, column=7, sticky="w", pady=(4, 0)
        )

        # Recovery (assistant-only)
        tk.Checkbutton(cb_block, text="Recovery (anti-stuck)", variable=self.cavebot_recovery_enabled).grid(
            row=2, column=0, sticky="w", pady=(6, 0)
        )
        tk.Label(cb_block, text="Idle s:").grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        tk.Spinbox(cb_block, from_=0.0, to=999.0, increment=0.5, textvariable=self.cavebot_recovery_idle_s, width=6).grid(
            row=2, column=2, sticky="w", pady=(6, 0)
        )
        tk.Label(cb_block, text="Max:").grid(row=2, column=3, sticky="w", padx=(14, 0), pady=(6, 0))
        tk.Spinbox(cb_block, from_=1, to=99, textvariable=self.cavebot_recovery_max_attempts, width=6).grid(
            row=2, column=4, sticky="w", pady=(6, 0)
        )
        tk.Checkbutton(cb_block, text="Stop si falla", variable=self.cavebot_recovery_stop_on_fail).grid(
            row=2, column=5, columnspan=3, sticky="w", padx=(14, 0), pady=(6, 0)
        )

        tk.Label(cb_block, text="Fin:").grid(row=3, column=0, sticky="w", pady=(4, 0))
        tk.Label(cb_block, textvariable=self.cavebot_finish_text, width=60, anchor="w").grid(
            row=3, column=1, columnspan=7, sticky="w", pady=(4, 0)
        )

        tk.Label(cb_block, text="Bloqueado:").grid(row=4, column=0, sticky="w", pady=(2, 0))
        tk.Label(cb_block, textvariable=self.cavebot_block_text, width=60, anchor="w").grid(
            row=4, column=1, columnspan=7, sticky="w", pady=(2, 0)
        )

        # Checklist de ruta
        tk.Label(tab_cavebot, text="").grid(row=2, column=0)
        tk.Label(tab_cavebot, text="Checklist de ruta:").grid(row=3, column=0, sticky="w", pady=(10, 0))

        self._route_status_var = tk.StringVar(value="(ruta no cargada)")
        tk.Label(tab_cavebot, textvariable=self._route_status_var, width=60, anchor="w").grid(
            row=3, column=1, columnspan=3, sticky="w", pady=(10, 0)
        )

        self._route_listbox = tk.Listbox(tab_cavebot, height=10, width=60)
        self._route_listbox.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

        def request_step_jump_selected() -> None:
            try:
                sel = self._route_listbox.curselection()
                if not sel:
                    return
                idx = int(sel[0])
                self._config.request_step_jump(idx)
            except Exception:
                pass

        def request_step_reset() -> None:
            try:
                self._config.request_step_jump(0)
            except Exception:
                pass

        # UX: doble click en la checklist para saltar el puntero del cavebot a ese step.
        try:
            self._route_listbox.bind("<Double-Button-1>", lambda _e: request_step_jump_selected())
        except Exception:
            pass

        self._route_next_var = tk.StringVar(value="-")
        tk.Label(tab_cavebot, text="Proximos:").grid(row=5, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_cavebot, textvariable=self._route_next_var, width=60, anchor="w").grid(
            row=5, column=1, columnspan=2, sticky="w", pady=(6, 0)
        )

        def _route_format_item(idx: int, wp: object) -> str:
            try:
                name = getattr(wp, "name", None) or ""
                x = getattr(wp, "x", None)
                y = getattr(wp, "y", None)
                z = getattr(wp, "z", None)
                act = getattr(wp, "action", None) or ""
                coord = ""
                if x is not None and y is not None:
                    coord = f"({x},{y}{'' if z is None else ','+str(z)})"
                label = name or coord or "(wp)"
                if act:
                    return f"{idx:03d}  {label}  action={act}"
                return f"{idx:03d}  {label}"
            except Exception:
                return f"{idx:03d}  (wp)"

        def _load_route_for_ui() -> None:
            path = str(self.cavebot_route_path.get()).strip() or "configs/route.json"
            if path == self._route_loaded_from and self._route_items:
                return
            self._route_loaded_from = path
            self._route_items = []
            self._route_current_index = None
            try:
                from navigation.route import load_route

                route = load_route(path)
                self._route_items = [{"wp": wp} for wp in route]
            except Exception:
                self._route_items = []

            try:
                self._route_listbox.delete(0, "end")
                for i, item in enumerate(self._route_items):
                    self._route_listbox.insert("end", _route_format_item(i, item.get("wp")))
            except Exception:
                pass

            try:
                if self._route_items:
                    self._route_status_var.set(f"Cargada: {path} ({len(self._route_items)} waypoints)")
                else:
                    self._route_status_var.set(f"No pude cargar ruta: {path}")
            except Exception:
                pass

        def reload_route_ui() -> None:
            self._route_loaded_from = ""
            _load_route_for_ui()

        tk.Button(tab_cavebot, text="Recargar ruta", width=14, command=reload_route_ui).grid(
            row=4, column=3, sticky="w", padx=(8, 0)
        )

        tk.Button(tab_cavebot, text="Saltar a paso", width=14, command=request_step_jump_selected).grid(
            row=5, column=2, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Button(tab_cavebot, text="Reset (inicio)", width=14, command=request_step_reset).grid(
            row=5, column=3, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        # Mantener la ruta cargada para checklist cuando cambie el path.
        try:
            self.cavebot_route_path.trace_add("write", lambda *_args: reload_route_ui())
        except Exception:
            pass
        _load_route_for_ui()

        # --- TAB: Configuracion --- (dos columnas)
        cfg_grid = tk.Frame(tab_config)
        cfg_grid.grid(row=0, column=0, columnspan=4, sticky="nw")
        cfg_left = tk.Frame(cfg_grid)
        cfg_left.grid(row=0, column=0, sticky="nw", padx=(0, 16))
        cfg_right = tk.Frame(cfg_grid)
        cfg_right.grid(row=0, column=1, sticky="nw")

        # Simulacion y ROIs (izquierda)
        tk.Checkbutton(cfg_left, text="Habilitar simulacion de senales", variable=self.sim_enabled).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        tk.Checkbutton(cfg_left, text="Paralyzed", variable=self.sim_paralyzed).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        tk.Checkbutton(cfg_left, text="Haste activo", variable=self.sim_haste_active).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        tk.Checkbutton(cfg_left, text="Utamo activo", variable=self.sim_utamo_active).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        tk.Checkbutton(cfg_left, text="Hungry", variable=self.sim_hungry).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )

        tk.Label(cfg_left, text="ROIs config (override)").grid(row=5, column=0, sticky="w", pady=(10, 0))
        tk.Entry(cfg_left, textvariable=self.rois_config_override, width=30).grid(
            row=5, column=1, sticky="w", pady=(10, 0)
        )

        def browse_rois() -> None:
            try:
                from tkinter import filedialog

                path = filedialog.askopenfilename(
                    title="Selecciona ROIs profile JSON",
                    initialdir=str((Path(__file__).resolve().parent / "configs")),
                    filetypes=[("JSON", "*.json"), ("All files", "*")],
                )
                if path:
                    self.rois_config_override.set(path)
            except Exception:
                pass

        tk.Button(cfg_left, text="Browse", width=8, command=browse_rois).grid(
            row=5, column=2, sticky="w", padx=(8, 0), pady=(10, 0)
        )

        tk.Label(
            cfg_left,
            text="(Se aplica al iniciar el bot; requiere reinicio)",
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(4, 0))

        tk.Label(
            cfg_left,
            text=(
                "Tip estable (sin coords visibles): usa CAVEBOT_MODE=steps. "
                "Para minimap_motion: define seed X/Y (y opcional Z) y una policy de fallback."
            ),
            wraplength=320,
            justify="left",
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # Asistente + Replay/Log (derecha)
        tk.Checkbutton(cfg_right, text="Modo asistente (sin inputs)", variable=self.asst_enabled).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        tk.Checkbutton(cfg_right, text="Confirmacion humana (cavebot)", variable=self.asst_confirm).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        tk.Checkbutton(cfg_right, text="Alertas sonoras", variable=self.asst_sound).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )

        tk.Label(cfg_right, text="Modo de inputs").grid(row=3, column=0, sticky="w", pady=(6, 0))
        tk.OptionMenu(cfg_right, self.asst_input_mode, "log", "mock", "keyboard", "wininput").grid(
            row=3, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(cfg_right, text="TARGET hotkey").grid(row=4, column=0, sticky="w", pady=(4, 0))
        tk.Entry(cfg_right, textvariable=self.asst_target_hotkey, width=10).grid(
            row=4, column=1, sticky="w", pady=(4, 0)
        )

        tk.Label(cfg_right, text="Minimap click hotkey").grid(row=5, column=0, sticky="w", pady=(4, 0))
        tk.Entry(cfg_right, textvariable=self.asst_minimap_hotkey, width=10).grid(
            row=5, column=1, sticky="w", pady=(4, 0)
        )

        # Live input guardrails (must not shift the rest of the layout).
        live_frame = tk.LabelFrame(cfg_right, text="Live input safety", padx=8, pady=6)
        live_frame.grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))

        def _confirm_arm_live() -> None:
            try:
                if not bool(self.asst_live_input_armed.get()):
                    return

                msg = (
                    "Vas a ARMAR live input (inyeccion real de teclas en Windows).\n\n"
                    "Reglas de seguridad:\n"
                    "- Solo ejecuta acciones COMMITTED (no preview).\n"
                    "- Requiere un pulso humano (boton 'Siguiente accion').\n"
                    "- Solo envia teclas si la ventana activa coincide con los titulos permitidos.\n\n"
                    "Continuar?"
                )
                ok = bool(self._messagebox.askyesno("Arm live input", msg))
                if not ok:
                    self.asst_live_input_armed.set(False)
                    return

                # Ensure we are in keyboard mode when arming.
                try:
                    m = str(self.asst_input_mode.get() or "log").strip().lower()
                except Exception:
                    m = "log"
                if m not in {"keyboard", "wininput"}:
                    self.asst_input_mode.set("keyboard")
            except Exception:
                try:
                    self.asst_live_input_armed.set(False)
                except Exception:
                    pass

        tk.Checkbutton(
            live_frame,
            text="ARM live input (I understand)",
            variable=self.asst_live_input_armed,
            command=_confirm_arm_live,
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        tk.Label(live_frame, text="Allowed window titles (comma)").grid(row=1, column=0, sticky="w", pady=(6, 0))
        tk.Entry(live_frame, textvariable=self.asst_allowed_window_titles, width=34).grid(
            row=1, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(
            live_frame,
            text="Tip: usa 'TibiaClone Harness' para pruebas controladas",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        def open_live_input_harness() -> None:
            try:
                # Convenience: ensure the harness title is allowlisted.
                # This does NOT arm live input; it only updates the allowlist.
                try:
                    harness_title = "TibiaClone Harness"
                    raw = str(self.asst_allowed_window_titles.get() or "")
                    titles = [s.strip() for s in raw.split(",") if s.strip()]
                    if harness_title not in titles:
                        titles.append(harness_title)
                        self.asst_allowed_window_titles.set(", ".join(titles))
                except Exception:
                    pass

                script = self._repo_root / "tools" / "live_input_harness.py"
                if not script.is_file():
                    self._messagebox.showerror("Harness", f"No existe: {script}")
                    return
                # Use the current interpreter (Poetry/venv) so Tk deps match.
                subprocess.Popen(
                    [sys.executable, str(script), "--title", "TibiaClone Harness"],
                    cwd=str(self._repo_root),
                )
            except Exception as e:
                try:
                    self._messagebox.showerror("Harness", f"No pude abrir el harness: {e}")
                except Exception:
                    pass

        def add_foreground_title_to_allowlist() -> None:
            """Append the current foreground window title to the allowlist.

            This does NOT arm live input; it only helps populate the allowlist.
            """

            try:
                from input_guard import get_foreground_window_title

                title = str(get_foreground_window_title() or "").strip()
            except Exception:
                title = ""

            if not title:
                try:
                    self._messagebox.showwarning(
                        "Allowlist",
                        "No pude leer el titulo de la ventana activa.\n\n"
                        "Tip: ejecuta como Windows, o agrega el titulo manualmente.",
                    )
                except Exception:
                    pass
                return

            try:
                raw = str(self.asst_allowed_window_titles.get() or "")
                titles = [s.strip() for s in raw.split(",") if s.strip()]
                if title not in titles:
                    titles.append(title)
                    self.asst_allowed_window_titles.set(", ".join(titles))
            except Exception:
                pass

        def reset_allowlist_defaults() -> None:
            """Reset allowlist to safe defaults.

            This does NOT arm live input.
            """

            try:
                defaults = ["TibiaClone", "MyClient", "TibiaClone Harness"]
                self.asst_allowed_window_titles.set(",".join(defaults))
            except Exception:
                pass

        def clear_allowlist() -> None:
            """Clear the allowlist.

            This does NOT arm live input.
            """

            try:
                self.asst_allowed_window_titles.set("")
            except Exception:
                pass

        tk.Button(live_frame, text="Abrir harness", width=14, command=open_live_input_harness).grid(
            row=3, column=0, sticky="w", pady=(6, 0)
        )

        tk.Button(
            live_frame,
            text="Add foreground",
            width=14,
            command=add_foreground_title_to_allowlist,
        ).grid(row=3, column=1, sticky="w", pady=(6, 0))

        tk.Button(
            live_frame,
            text="Reset allowlist",
            width=14,
            command=reset_allowlist_defaults,
        ).grid(row=4, column=0, sticky="w", pady=(6, 0))

        tk.Button(
            live_frame,
            text="Clear allowlist",
            width=14,
            command=clear_allowlist,
        ).grid(row=4, column=1, sticky="w", pady=(6, 0))

        tk.Checkbutton(cfg_right, text="Guardar replays (ROI+JSON)", variable=self.replay_enabled).grid(
            row=7, column=0, columnspan=2, sticky="w"
        )
        tk.Label(cfg_right, text="Replay interval (ms)").grid(row=8, column=0, sticky="w", pady=(4, 0))
        tk.Spinbox(cfg_right, from_=100, to=60000, increment=100, textvariable=self.replay_interval_ms, width=8).grid(
            row=8, column=1, sticky="w", pady=(4, 0)
        )

        tk.Label(cfg_right, text="Replay out_dir").grid(row=9, column=0, sticky="w", pady=(4, 0))
        tk.Entry(cfg_right, textvariable=self.replay_out_dir, width=28).grid(
            row=9, column=1, sticky="w", pady=(4, 0)
        )

        def open_replay_dir() -> None:
            try:
                p = str(self.replay_out_dir.get()).strip() or "logs/replay"
                os.makedirs(p, exist_ok=True)
                os.startfile(os.path.abspath(p))
            except Exception:
                pass

        def force_replay_snapshot() -> None:
            try:
                self._config.request_replay_snapshot()
            except Exception:
                pass

        tk.Button(cfg_right, text="Snapshot ahora", width=12, command=force_replay_snapshot).grid(
            row=8, column=2, sticky="w", padx=(8, 0)
        )
        tk.Button(cfg_right, text="Abrir carpeta", width=12, command=open_replay_dir).grid(
            row=9, column=2, sticky="w", padx=(8, 0)
        )

        tk.Label(cfg_right, text="Preset (replay/log)").grid(row=10, column=0, sticky="w", pady=(6, 0))
        tel_presets = ["Custom", "Off", "Debug", "Soak", "Soak Full"]
        ttk.Combobox(
            cfg_right,
            textvariable=self.telemetry_preset,
            values=tel_presets,
            width=16,
            state="readonly",
        ).grid(row=10, column=1, sticky="w", pady=(6, 0))
        tk.Button(
            cfg_right,
            text="Aplicar",
            width=12,
            command=lambda: self._apply_telemetry_preset(str(self.telemetry_preset.get())),
        ).grid(row=10, column=2, sticky="w", padx=(8, 0), pady=(6, 0))

        tk.Checkbutton(cfg_right, text="Exportar telemetria JSONL", variable=self.log_enabled).grid(
            row=11, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        tk.Label(cfg_right, text="Log interval (ms)").grid(row=12, column=0, sticky="w", pady=(4, 0))
        tk.Spinbox(cfg_right, from_=100, to=60000, increment=50, textvariable=self.log_interval_ms, width=8).grid(
            row=12, column=1, sticky="w", pady=(4, 0)
        )

        tk.Label(cfg_right, text="Log out_file").grid(row=13, column=0, sticky="w", pady=(4, 0))
        tk.Entry(cfg_right, textvariable=self.log_out_file, width=28).grid(
            row=13, column=1, sticky="w", pady=(4, 0)
        )

        def open_log_parent() -> None:
            try:
                p = str(self.log_out_file.get()).strip() or "logs/telemetry.jsonl"
                parent = os.path.dirname(p) or "."
                os.makedirs(parent, exist_ok=True)
                os.startfile(os.path.abspath(parent))
            except Exception:
                pass

        def open_log_file() -> None:
            try:
                p = str(self.log_out_file.get()).strip() or "logs/telemetry.jsonl"
                parent = os.path.dirname(p) or "."
                os.makedirs(parent, exist_ok=True)
                if not os.path.exists(p):
                    with open(p, "a", encoding="utf-8"):
                        pass
                os.startfile(os.path.abspath(p))
            except Exception:
                pass

        tk.Button(cfg_right, text="Abrir carpeta", width=12, command=open_log_parent).grid(
            row=13, column=2, sticky="w", padx=(8, 0)
        )
        tk.Button(cfg_right, text="Abrir archivo", width=12, command=open_log_file).grid(
            row=14, column=2, sticky="w", padx=(8, 0)
        )

        # --- TAB: Rutas / Cavebot ---
        self._build_routes_tab(tab_routes)

        # Idle alert + Overlay (derecha, debajo)
        tk.Label(cfg_right, text="Idle (alerta / anti-stuck, sin inputs)").grid(
            row=11, column=0, columnspan=3, sticky="w", pady=(10, 0)
        )
        tk.Label(cfg_right, text="WARN si idle >= (s)").grid(row=12, column=0, sticky="w", pady=(4, 0))
        tk.Spinbox(cfg_right, from_=0, to=3600, increment=5, textvariable=self.idle_alert_s, width=8).grid(
            row=12, column=1, sticky="w", pady=(4, 0)
        )
        tk.Label(cfg_right, text="FAIL si idle >= (s)").grid(row=13, column=0, sticky="w", pady=(4, 0))
        tk.Spinbox(cfg_right, from_=0, to=7200, increment=10, textvariable=self.ui_idle_fail_s, width=8).grid(
            row=13, column=1, sticky="w", pady=(4, 0)
        )
        tk.Label(cfg_right, text="Repetir alerta cada (s)").grid(row=14, column=0, sticky="w", pady=(4, 0))
        tk.Spinbox(cfg_right, from_=1, to=600, increment=1, textvariable=self.idle_repeat_s, width=8).grid(
            row=14, column=1, sticky="w", pady=(4, 0)
        )
        tk.Label(
            cfg_right,
            text="(0 desactiva. Se aplica al iniciar el bot; requiere reinicio)",
        ).grid(row=15, column=0, columnspan=3, sticky="w", pady=(4, 0))

        tk.Label(cfg_right, text="Overlay (frames anotados)").grid(
            row=16, column=0, columnspan=3, sticky="w", pady=(10, 0)
        )
        tk.Label(cfg_right, text="Preset").grid(row=17, column=0, sticky="w", pady=(4, 0))
        overlay_presets = ["Custom", "Minimal", "Debug HUD", "Full HUD"]
        ttk.Combobox(
            cfg_right,
            textvariable=self.overlay_preset,
            values=overlay_presets,
            width=16,
            state="readonly",
        ).grid(row=17, column=1, sticky="w", pady=(4, 0))
        tk.Button(
            cfg_right,
            text="Aplicar",
            width=12,
            command=lambda: self._apply_overlay_preset(str(self.overlay_preset.get())),
        ).grid(row=17, column=2, sticky="w", padx=(8, 0), pady=(4, 0))

        tk.Checkbutton(cfg_right, text="Habilitar overlay", variable=self.overlay_enabled).grid(
            row=18, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        tk.Button(cfg_right, text="Cargar UI", width=12, command=self._load_ui_settings).grid(
            row=18, column=2, sticky="w", pady=(4, 0)
        )
        tk.Button(cfg_right, text="Guardar UI", width=12, command=self._save_ui_settings).grid(
            row=19, column=2, sticky="w", padx=(8, 0), pady=(4, 0)
        )

        tk.Label(cfg_right, text="Overlay out_dir").grid(row=19, column=0, sticky="w", pady=(4, 0))
        tk.Entry(cfg_right, textvariable=self.overlay_out_dir, width=28).grid(
            row=19, column=1, sticky="w", pady=(4, 0)
        )

        def open_overlay_dir() -> None:
            try:
                p = str(self.overlay_out_dir.get()).strip() or "logs/debug_overlay"
                os.makedirs(p, exist_ok=True)
                os.startfile(os.path.abspath(p))
            except Exception:
                pass

        tk.Button(cfg_right, text="Abrir carpeta", width=12, command=open_overlay_dir).grid(
            row=20, column=2, sticky="w", padx=(8, 0)
        )

        tk.Label(cfg_right, text="Interval (s)").grid(row=20, column=0, sticky="w", pady=(4, 0))
        tk.Spinbox(
            cfg_right,
            from_=0.1,
            to=60.0,
            increment=0.1,
            textvariable=self.overlay_interval_s,
            width=8,
        ).grid(row=20, column=1, sticky="w", pady=(4, 0))

        tk.Checkbutton(cfg_right, text="Tile grid", variable=self.overlay_tile_grid).grid(
            row=21, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        tk.Label(cfg_right, text="Tile px").grid(row=21, column=2, sticky="w", pady=(4, 0))
        tk.Spinbox(cfg_right, from_=4, to=128, increment=1, textvariable=self.overlay_tile_px, width=8).grid(
            row=21, column=3, sticky="w", pady=(4, 0)
        )

        tk.Label(cfg_right, text="OVERLAY_ROIS (CSV)").grid(row=22, column=0, sticky="w", pady=(4, 0))
        tk.Entry(cfg_right, textvariable=self.overlay_rois, width=28).grid(
            row=22, column=1, sticky="w", pady=(4, 0)
        )
        tk.Label(cfg_right, text="(Se aplica al iniciar el bot; requiere reinicio)").grid(
            row=23, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )

        # Ajustes UI y soak (abajo)
        def open_ui_settings_file() -> None:
            try:
                p = self._ui_settings_path()
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
                if not p.exists():
                    try:
                        self._save_ui_settings()
                    except Exception:
                        pass
                os.startfile(os.path.abspath(str(p)))
            except Exception:
                pass

        tk.Button(cfg_right, text="Reset defaults", width=18, command=self._reset_ui_defaults).grid(
            row=24, column=0, sticky="w", pady=(6, 0)
        )
        tk.Button(cfg_right, text="Abrir ui_settings.json", width=18, command=open_ui_settings_file).grid(
            row=24, column=1, sticky="w", pady=(6, 0)
        )

        def open_latest_soak() -> None:
            try:
                rid = self._last_soak_run_id
                replay_base = "logs/replay_soak"
                overlay_base = "logs/debug_overlay_soak"
                replay_dir = None
                overlay_dir = None
                if rid:
                    replay_dir = Path(replay_base) / rid
                    overlay_dir = Path(overlay_base) / rid
                if replay_dir is None or not replay_dir.exists():
                    replay_dir = self._find_latest_soak_dir(replay_base)
                if overlay_dir is None or not overlay_dir.exists():
                    overlay_dir = self._find_latest_soak_dir(overlay_base)

                if replay_dir is not None and replay_dir.exists():
                    os.startfile(os.path.abspath(str(replay_dir)))
                if overlay_dir is not None and overlay_dir.exists():
                    os.startfile(os.path.abspath(str(overlay_dir)))
            except Exception:
                pass

        def open_latest_soak_jsonl() -> None:
            try:
                p = None
                rid = self._last_soak_run_id
                if rid:
                    cand = Path("logs") / f"telemetry_soak_{rid}.jsonl"
                    if cand.exists():
                        p = cand
                if p is None:
                    p = self._find_latest_soak_jsonl()
                if p is None:
                    return
                os.startfile(os.path.abspath(str(p)))
            except Exception:
                pass

        def open_latest_soak_all() -> None:
            try:
                open_latest_soak()
                open_latest_soak_jsonl()
            except Exception:
                pass

        tk.Button(cfg_right, text="Abrir ultimo soak", width=18, command=open_latest_soak).grid(
            row=25, column=0, sticky="w", pady=(6, 0)
        )
        tk.Button(cfg_right, text="Abrir JSONL soak", width=18, command=open_latest_soak_jsonl).grid(
            row=25, column=1, sticky="w", pady=(6, 0)
        )
        tk.Button(cfg_right, text="Abrir TODO soak", width=18, command=open_latest_soak_all).grid(
            row=25, column=2, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Label(cfg_right, text="Soak run_id:").grid(row=26, column=0, sticky="w", pady=(6, 0))
        tk.Label(cfg_right, textvariable=self.soak_run_id_var, width=22, anchor="w").grid(
            row=26, column=1, sticky="w", pady=(6, 0)
        )

        def _resolve_latest_soak_inputs() -> tuple[Path | None, Path | None, Path | None, str | None]:
            """Best-effort resolution of latest soak artifacts.

            Prefers the current UI session run_id, otherwise the newest on disk.
            """

            rid = self._last_soak_run_id

            replay_dir = None
            overlay_dir = None
            jsonl_path = None

            try:
                if rid:
                    cand = Path("logs") / "replay_soak" / str(rid)
                    if cand.exists():
                        replay_dir = cand
            except Exception:
                replay_dir = None

            try:
                if rid:
                    cand = Path("logs") / "debug_overlay_soak" / str(rid)
                    if cand.exists():
                        overlay_dir = cand
            except Exception:
                overlay_dir = None

            try:
                if rid:
                    cand = Path("logs") / f"telemetry_soak_{rid}.jsonl"
                    if cand.exists():
                        jsonl_path = cand
            except Exception:
                jsonl_path = None

            if replay_dir is None:
                replay_dir = self._find_latest_soak_dir("logs/replay_soak")
            if overlay_dir is None:
                overlay_dir = self._find_latest_soak_dir("logs/debug_overlay_soak")
            if jsonl_path is None:
                jsonl_path = self._find_latest_soak_jsonl()

            # Fallbacks to non-soak locations.
            if replay_dir is None:
                replay_dir = Path("logs") / "replay"
            if overlay_dir is None:
                overlay_dir = Path("logs") / "debug_overlay"
            if jsonl_path is None:
                jsonl_path = Path("logs") / "telemetry.jsonl"

            return replay_dir, overlay_dir, jsonl_path, rid

        def _timeline_default_out(rid: str | None) -> Path:
            try:
                if rid:
                    return Path("logs") / f"soak_timeline_{rid}.html"
            except Exception:
                pass
            return Path("logs") / "soak_timeline.html"

        def open_soak_timeline_html() -> None:
            try:
                replay_dir, overlay_dir, jsonl_path, rid = _resolve_latest_soak_inputs()
                out_html = _timeline_default_out(rid)
                if not out_html.exists():
                    return
                os.startfile(os.path.abspath(str(out_html)))
            except Exception:
                pass

        def generate_soak_timeline_html() -> None:
            """Generate timeline HTML for the latest soak and open it."""
            try:
                replay_dir, overlay_dir, jsonl_path, rid = _resolve_latest_soak_inputs()
                out_html = _timeline_default_out(rid)

                # Ensure output dir exists.
                try:
                    out_html.parent.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass

                cmd = [
                    sys.executable,
                    str(self._repo_root / "tools" / "soak_timeline_report.py"),
                    "--jsonl",
                    str(jsonl_path),
                    "--replay-dir",
                    str(replay_dir),
                    "--overlay-dir",
                    str(overlay_dir),
                    "--out",
                    str(out_html),
                ]

                p = subprocess.run(
                    cmd,
                    cwd=str(self._repo_root),
                    capture_output=True,
                    text=True,
                )

                if p.returncode != 0:
                    msg = (p.stderr or p.stdout or "(sin output)").strip()
                    if len(msg) > 1500:
                        msg = msg[:1500] + "..."
                    try:
                        self._messagebox.showerror(
                            "Timeline HTML",
                            f"Fallo generacion (code={p.returncode}).\n\n{msg}",
                        )
                    except Exception:
                        pass
                    return

                # Open result.
                try:
                    os.startfile(os.path.abspath(str(out_html)))
                except Exception:
                    pass
            except Exception as e:
                try:
                    self._messagebox.showerror("Timeline HTML", f"Error: {e}")
                except Exception:
                    pass

        tk.Button(tab_config, text="Soak timeline (HTML)", width=18, command=generate_soak_timeline_html).grid(
            row=40, column=0, sticky="w", pady=(6, 0)
        )
        tk.Button(tab_config, text="Abrir timeline", width=18, command=open_soak_timeline_html).grid(
            row=40, column=1, sticky="w", pady=(6, 0)
        )

        # Aplicacion en tiempo real: cada cambio de UI actualiza el RuntimeConfig.
        def sync_healing(*_args):
            self._config.update_healing(
                enabled=bool(self.healing_enabled.get()),
                hp_below_pct=int(self.heal_hp_below_pct.get()),
                hp_recover_pct=int(self.heal_hp_recover_pct.get()),
                mp_below_pct=int(self.heal_mp_below_pct.get()),
                mp_recover_pct=int(self.heal_mp_recover_pct.get()),
                action=str(self.heal_action.get()),
                hp_action=str(self.heal_hp_action.get()),
                mp_action=str(self.heal_mp_action.get()),
                cooldown_s=float(self.heal_cooldown_s.get()),
            )

        def sync_cavebot(*_args):
            # Apply Cavebot config live (RuntimeConfig + env-driven knobs).
            try:
                mode = str(self.cavebot_mode.get() or "").strip().lower() or "steps"
            except Exception:
                mode = "steps"
            if mode not in {"pos", "steps"}:
                mode = "steps"

            self._config.update_cavebot(
                enabled=bool(self.cavebot_enabled.get()),
                route_path=str(self.cavebot_route_path.get()),
                mode=mode,
                # Keep explicit force_steps as an advanced knob (not exposed in UI for now).
                force_steps=False,
            )

            # Keep env vars in sync so core pieces that read os.getenv() each tick
            # can react without restarting.
            try:
                os.environ["CAVEBOT_MODE"] = mode
            except Exception:
                pass
            try:
                os.environ["CAVEBOT_LOOP"] = "1" if bool(self.cavebot_loop.get()) else "0"
            except Exception:
                pass

            # Coords provider (used by GameStateBuilder each update).
            try:
                cp = str(self.coords_provider.get() or "").strip().lower()
                if not cp or cp == "auto":
                    os.environ.pop("COORDS_PROVIDER", None)
                else:
                    os.environ["COORDS_PROVIDER"] = cp
            except Exception:
                pass

            # Minimap seed + fallback (only meaningful if provider is minimap).
            try:
                if str(self.coords_provider.get() or "").strip().lower() == "minimap":
                    sx = str(self.minimap_seed_x.get() or "").strip()
                    sy = str(self.minimap_seed_y.get() or "").strip()
                    sz = str(self.minimap_seed_z.get() or "").strip()
                    if sx:
                        os.environ["COORDS_SEED_X"] = sx
                    else:
                        os.environ.pop("COORDS_SEED_X", None)
                    if sy:
                        os.environ["COORDS_SEED_Y"] = sy
                    else:
                        os.environ.pop("COORDS_SEED_Y", None)
                    if sz:
                        os.environ["COORDS_SEED_Z"] = sz
                    else:
                        os.environ.pop("COORDS_SEED_Z", None)

                    fb_mode = str(self.minimap_fallback_mode.get() or "steps").strip().lower()
                    if fb_mode not in {"steps", "ocr"}:
                        fb_mode = "steps"
                    os.environ["MINIMAP_FALLBACK_MODE"] = fb_mode
                    os.environ["MINIMAP_FALLBACK_CONF_THRESHOLD"] = str(
                        float(self.minimap_fallback_conf_threshold.get())
                    )
                    os.environ["MINIMAP_FALLBACK_N_TICKS"] = str(
                        max(1, int(self.minimap_fallback_n_ticks.get()))
                    )
                else:
                    for k in [
                        "COORDS_SEED_X",
                        "COORDS_SEED_Y",
                        "COORDS_SEED_Z",
                        "MINIMAP_FALLBACK_MODE",
                        "MINIMAP_FALLBACK_CONF_THRESHOLD",
                        "MINIMAP_FALLBACK_N_TICKS",
                    ]:
                        os.environ.pop(k, None)
            except Exception:
                pass

            # Cavebot stop conditions (assistant-only).
            try:
                os.environ["CAP_LEAVE_ENABLED"] = "1" if bool(self.cavebot_stop_on_low_cap.get()) else "0"
                os.environ["CAP_LEAVE_THRESHOLD"] = str(max(0, int(self.cavebot_cap_threshold.get())))
            except Exception:
                pass
            try:
                os.environ["POTIONS_STOP_ENABLED"] = "1" if bool(self.cavebot_stop_on_low_potions.get()) else "0"
                os.environ["POTIONS_MIN"] = str(max(0, int(self.cavebot_potions_min.get())))
                os.environ["POTIONS_REMAINING"] = str(max(0, int(self.cavebot_potions_remaining.get())))
            except Exception:
                pass

            # Recovery policy (assistant-only).
            try:
                os.environ["ASSIST_RECOVERY_ENABLED"] = "1" if bool(self.cavebot_recovery_enabled.get()) else "0"
            except Exception:
                pass
            try:
                idle_s = float(self.cavebot_recovery_idle_s.get())
            except Exception:
                idle_s = 0.0
            try:
                if idle_s > 0.0:
                    os.environ["ASSIST_RECOVERY_IDLE_S"] = str(float(idle_s))
                else:
                    os.environ.pop("ASSIST_RECOVERY_IDLE_S", None)
            except Exception:
                pass
            try:
                os.environ["ASSIST_RECOVERY_MAX_ATTEMPTS"] = str(
                    max(1, int(self.cavebot_recovery_max_attempts.get()))
                )
            except Exception:
                pass
            try:
                os.environ["ASSIST_RECOVERY_STOP_ON_FAIL"] = "1" if bool(self.cavebot_recovery_stop_on_fail.get()) else "0"
            except Exception:
                pass

        def sync_simulation(*_args):
            self._config.update_simulation(
                enabled=bool(self.sim_enabled.get()),
                paralyzed=bool(self.sim_paralyzed.get()),
                haste_active=bool(self.sim_haste_active.get()),
                utamo_active=bool(self.sim_utamo_active.get()),
                hungry=bool(self.sim_hungry.get()),
            )

        def sync_assistant(*_args):
            try:
                mode = str(self.asst_input_mode.get() or "log").strip().lower()
            except Exception:
                mode = "log"

            # Only allow arming when keyboard mode is selected.
            live_armed = bool(self.asst_live_input_armed.get())
            if mode not in {"keyboard", "wininput"}:
                live_armed = False

            titles_raw = str(self.asst_allowed_window_titles.get() or "")
            allowed_titles = [s.strip() for s in titles_raw.split(",") if s.strip()]

            self._config.update_assistant(
                enabled=bool(self.asst_enabled.get()),
                confirm_actions=bool(self.asst_confirm.get()),
                sound_alerts=bool(self.asst_sound.get()),
                input_mode=str(self.asst_input_mode.get()),
                target_hotkey=str(self.asst_target_hotkey.get()),
                minimap_hotkey=str(self.asst_minimap_hotkey.get()),
                live_input_armed=bool(live_armed),
                allowed_window_titles=list(allowed_titles),
            )

        def sync_replay_and_logging(*_args):
            self._config.update_replay(
                enabled=bool(self.replay_enabled.get()),
                interval_ms=int(self.replay_interval_ms.get()),
                out_dir=str(self.replay_out_dir.get()),
            )
            self._config.update_logging(
                enabled=bool(self.log_enabled.get()),
                interval_ms=int(self.log_interval_ms.get()),
                out_file=str(self.log_out_file.get()),
            )

        for v in [
            self.healing_enabled,
            self.heal_hp_below_pct,
            self.heal_hp_recover_pct,
            self.heal_mp_below_pct,
            self.heal_mp_recover_pct,
            self.heal_action,
            self.heal_hp_action,
            self.heal_mp_action,
            self.heal_cooldown_s,
        ]:
            v.trace_add("write", sync_healing)
        for v in [
            self.cavebot_enabled,
            self.cavebot_route_path,
            self.cavebot_mode,
            self.cavebot_loop,
            self.coords_provider,
            self.minimap_seed_x,
            self.minimap_seed_y,
            self.minimap_seed_z,
            self.minimap_fallback_mode,
            self.minimap_fallback_conf_threshold,
            self.minimap_fallback_n_ticks,
            self.cavebot_stop_on_low_cap,
            self.cavebot_cap_threshold,
            self.cavebot_stop_on_low_potions,
            self.cavebot_potions_remaining,
            self.cavebot_potions_min,
            self.cavebot_recovery_enabled,
            self.cavebot_recovery_idle_s,
            self.cavebot_recovery_max_attempts,
            self.cavebot_recovery_stop_on_fail,
        ]:
            v.trace_add("write", sync_cavebot)
        for v in [self.sim_enabled, self.sim_paralyzed, self.sim_haste_active, self.sim_utamo_active, self.sim_hungry]:
            v.trace_add("write", sync_simulation)
        for v in [
            self.asst_enabled,
            self.asst_confirm,
            self.asst_sound,
            self.asst_input_mode,
            self.asst_target_hotkey,
            self.asst_minimap_hotkey,
            self.asst_live_input_armed,
            self.asst_allowed_window_titles,
        ]:
            v.trace_add("write", sync_assistant)
        for v in [
            self.replay_enabled,
            self.replay_interval_ms,
            self.replay_out_dir,
            self.log_enabled,
            self.log_interval_ms,
            self.log_out_file,
        ]:
            v.trace_add("write", sync_replay_and_logging)

        # Sync inicial
        sync_healing()
        sync_cavebot()
        sync_simulation()
        sync_assistant()
        sync_replay_and_logging()

        def poll_telemetry() -> None:  # pyright: ignore[reportGeneralTypeIssues]
            try:
                tel = self._config.telemetry_snapshot()
                health = self._config.health_snapshot()

                # Injection/steps status (assistant-only). Fail-safe if core doesn't provide it.
                try:
                    st = getattr(self._config, "assistant_status_snapshot", None)
                    if callable(st):
                        ss = st()
                        i = getattr(ss, "step_index", None)
                        n = getattr(ss, "total_steps", None)
                        if i is not None and n is not None and int(n) > 0:
                            # `step_index` is 0-based in core; display 1-based.
                            self.injection_step_var.set(f"{int(i) + 1}/{int(n)}")
                        else:
                            self.injection_step_var.set("-")
                        self.injection_label_var.set(str(getattr(ss, "current_label", "") or "-") or "-")
                        self.injection_next_var.set(str(getattr(ss, "next_step_text", "") or "-") or "-")
                        self.injection_state_var.set(str(getattr(ss, "injection_state", "") or "-") or "-")
                        self.injection_reason_var.set(str(getattr(ss, "injection_reason", "") or "-") or "-")
                        mode = str(getattr(ss, "input_mode", "") or "-") or "-"
                        drv = str(getattr(ss, "driver_name", "") or "-") or "-"
                        self.injection_mode_driver_var.set(f"{mode} | {drv}")
                        fg = str(getattr(ss, "foreground_title", "") or "-") or "-"
                        br = str(getattr(ss, "input_block_reason", "") or "-") or "-"
                        if len(fg) > 60:
                            fg = fg[:57] + "..."
                        if len(br) > 60:
                            br = br[:57] + "..."
                        self.injection_foreground_var.set(fg)
                        self.injection_block_reason_var.set(br)
                except Exception:
                    pass
                hp_str = "?"
                mp_str = "?"
                cap_str = "?"
                if tel.hp_current is not None and tel.hp_max is not None:
                    if tel.hp_pct is not None:
                        hp_str = f"{tel.hp_current}/{tel.hp_max} ({tel.hp_pct:.1f}%)"
                    else:
                        hp_str = f"{tel.hp_current}/{tel.hp_max}"
                if tel.mp_current is not None and tel.mp_max is not None:
                    if tel.mp_pct is not None:
                        mp_str = f"{tel.mp_current}/{tel.mp_max} ({tel.mp_pct:.1f}%)"
                    else:
                        mp_str = f"{tel.mp_current}/{tel.mp_max}"

                if tel.cap_current is not None:
                    cap_str = f"{tel.cap_current}"

                self.hp_text.set(hp_str)
                self.mp_text.set(mp_str)
                self.cap_text.set(cap_str)

                # Coords provider status (selection + readiness)
                try:
                    prov = str(getattr(tel, "coords_provider", "") or "")
                    prov_info = getattr(tel, "coords_provider_state", None)
                    if isinstance(prov_info, dict) and prov_info:
                        enabled = bool(prov_info.get("enabled", True))
                        seed_ok = bool(prov_info.get("seed_ok", False))
                        reason = str(prov_info.get("reason", "") or "")
                        conf = prov_info.get("confidence", None)
                        conf_s = "-"
                        color = "gray"
                        try:
                            if conf is not None:
                                conf_f = float(conf)
                                conf_s = f"{conf_f:.2f}"
                                if conf_f >= 0.7:
                                    color = "green"
                                elif conf_f >= 0.4:
                                    color = "orange"
                                else:
                                    color = "red"
                        except Exception:
                            conf_s = "-"
                            color = "gray"

                        seed_s = "OK" if seed_ok else "NO"
                        en_s = "ON" if enabled else "OFF"
                        msg = f"{prov or '-'} {en_s} seed={seed_s} conf={conf_s} {reason}".strip()
                        self.coords_provider_state_text.set(msg)
                        try:
                            if self._coords_provider_state_label is not None:
                                self._coords_provider_state_label.config(fg=color)
                        except Exception:
                            pass
                    else:
                        # Fallback to legacy flat fields
                        status = str(getattr(tel, "coords_provider_status", "") or "")
                        conf = getattr(tel, "coords_confidence", None)
                        conf_s = ""
                        try:
                            if conf is not None:
                                conf_s = f" conf={float(conf):.2f}"
                        except Exception:
                            conf_s = ""
                        msg = f"{prov or '-'} {status}{conf_s}".strip() or "-"
                        self.coords_provider_state_text.set(msg)
                        try:
                            if self._coords_provider_state_label is not None:
                                self._coords_provider_state_label.config(fg="gray")
                        except Exception:
                            pass
                except Exception:
                    pass

                parts = []
                # Coords confidence (estructurado desde el core)
                coords_status = str(getattr(tel, "coords_status", "") or "")
                coords_jump = getattr(tel, "coords_jump", None)
                if coords_status == "NO_COORDS" or getattr(tel, "pos_x", None) is None or getattr(tel, "pos_y", None) is None:
                    parts.append("no_coords")
                elif coords_status == "UNSTABLE":
                    parts.append("coords_unstable")
                elif coords_status == "BAD_JUMP":
                    parts.append("coords_bad")
                if tel.low_hp:
                    parts.append("low_hp")
                if tel.low_mp:
                    parts.append("low_mp")
                if tel.low_cap:
                    parts.append("low_cap")
                try:
                    if getattr(tel, "low_potions", None):
                        parts.append("low_potions")
                except Exception:
                    pass
                if getattr(tel, "ring_equipped", None) is True:
                    parts.append("ring")
                if getattr(tel, "amulet_equipped", None) is True:
                    parts.append("amulet")
                if tel.paralyzed:
                    parts.append("paralyzed")
                if tel.haste_active:
                    parts.append("haste")
                if tel.utamo_active:
                    parts.append("utamo")
                if tel.hungry:
                    parts.append("hungry")

                self.signals_text.set(", ".join(parts) if parts else "-")

                self.target_text.set(tel.target or "-")
                self.reco_text.set(tel.recommendation or "-")
                self.cavebot_next_text.set(tel.cavebot_next or "-")
                self.cavebot_wp_text.set(tel.cavebot_waypoint or "-")
                self.cavebot_action_text.set(tel.cavebot_action or "-")

                # Cavebot finish state (end-of-route / low cap / low potions)
                try:
                    finished = bool(getattr(tel, "cavebot_finished", False))
                except Exception:
                    finished = False
                try:
                    reason = str(getattr(tel, "cavebot_finish_reason", "") or "").strip()
                except Exception:
                    reason = ""
                try:
                    self.cavebot_finish_text.set(reason if reason else "-")
                except Exception:
                    pass

                # Cavebot blocked state (require-gates)
                try:
                    blocked = bool(getattr(tel, "cavebot_blocked", False))
                except Exception:
                    blocked = False
                try:
                    why = str(getattr(tel, "cavebot_block_reason", "") or "").strip()
                except Exception:
                    why = ""
                try:
                    self.cavebot_block_text.set(why if (blocked and why) else ("(blocked)" if blocked else "-"))
                except Exception:
                    pass

                # Disable/enable advance buttons based on finish state.
                try:
                    running = bool(self._is_running())
                    btn_tk_state = cast(
                        Literal["disabled", "normal"],
                        ("disabled" if (not running or finished) else "normal"),
                    )
                    if hasattr(self, "_cavebot_mark_btn") and self._cavebot_mark_btn is not None:
                        self._cavebot_mark_btn.config(state=btn_tk_state)
                    if hasattr(self, "_cavebot_next_btn") and self._cavebot_next_btn is not None:
                        self._cavebot_next_btn.config(state=btn_tk_state)
                except Exception:
                    pass
                try:
                    st = int(getattr(tel, "cavebot_step_total", 0) or 0)
                    si = int(getattr(tel, "cavebot_step_idx", 0) or 0)
                    if st > 0:
                        self.cavebot_step_text.set(f"{si + 1}/{st}")
                    else:
                        self.cavebot_step_text.set("-")
                except Exception:
                    self.cavebot_step_text.set("-")

                now = time.time()
                if tel.ts and tel.ts > 0:
                    age = max(0.0, now - float(tel.ts))
                    if age >= 2.0:
                        self.stale_var.set(f"stale {age:.1f}s")
                    else:
                        self.stale_var.set("OK")
                else:
                    self.stale_var.set("-")

                # Health line (watchdog snapshot)
                try:
                    # Thresholds (env-tunable)
                    try:
                        warn_gs_age_s = float(os.getenv("UI_HEALTH_WARN_GS_AGE_S", "3").strip() or "3")
                    except Exception:
                        warn_gs_age_s = 3.0
                    try:
                        fail_gs_age_s = float(os.getenv("UI_HEALTH_FAIL_GS_AGE_S", "8").strip() or "8")
                    except Exception:
                        fail_gs_age_s = 8.0
                    try:
                        warn_frame_age_s = float(os.getenv("UI_HEALTH_WARN_FRAME_AGE_S", "3").strip() or "3")
                    except Exception:
                        warn_frame_age_s = 3.0
                    try:
                        fail_frame_age_s = float(os.getenv("UI_HEALTH_FAIL_FRAME_AGE_S", "8").strip() or "8")
                    except Exception:
                        fail_frame_age_s = 8.0
                    warn_on_drops = os.getenv("UI_HEALTH_WARN_ON_DROPS", "1").strip().lower() not in {"0", "false", "no"}

                    parts_h = []
                    if health.frame_age_s is not None:
                        parts_h.append(f"frame_age {float(health.frame_age_s):.1f}s")
                    if health.gs_age_s is not None:
                        parts_h.append(f"gs_age {float(health.gs_age_s):.1f}s")
                    if health.q_frame is not None or health.q_gs is not None:
                        parts_h.append(f"q f={health.q_frame} gs={health.q_gs}")
                    if health.drop_frame_queue is not None or health.drop_gs_queue is not None:
                        parts_h.append(f"drops f={health.drop_frame_queue} gs={health.drop_gs_queue}")
                    if health.capture_ms_last is not None or health.vision_ms_last is not None or health.decision_ms_last is not None:
                        parts_h.append(
                            f"ms cap={0 if health.capture_ms_last is None else float(health.capture_ms_last):.0f}"
                            f" vis={0 if health.vision_ms_last is None else float(health.vision_ms_last):.0f}"
                            f" dec={0 if health.decision_ms_last is None else float(health.decision_ms_last):.0f}"
                        )
                    try:
                        if (
                            getattr(health, "roi_offset_dx_px", None) is not None
                            or getattr(health, "roi_offset_dy_px", None) is not None
                            or getattr(health, "roi_offset_score", None) is not None
                        ):
                            dx = getattr(health, "roi_offset_dx_px", None)
                            dy = getattr(health, "roi_offset_dy_px", None)
                            sc = getattr(health, "roi_offset_score", None)
                            parts_h.append(
                                "roi_off "
                                f"dx={0 if dx is None else float(dx):.0f} "
                                f"dy={0 if dy is None else float(dy):.0f} "
                                f"score={0 if sc is None else float(sc):.2f}"
                            )
                    except Exception:
                        pass
                    if health.dead_threads:
                        parts_h.append(f"dead {health.dead_threads}")
                    if health.warn:
                        parts_h.append(str(health.warn))
                    self.health_var.set(" | ".join(parts_h) if parts_h else "-")

                    # Semaphore status
                    status = "OK"
                    if health.dead_threads:
                        status = "FAIL"
                    else:
                        try:
                            fa = float(health.frame_age_s) if health.frame_age_s is not None else None
                            ga = float(health.gs_age_s) if health.gs_age_s is not None else None
                        except Exception:
                            fa, ga = None, None

                        if (ga is not None and ga >= fail_gs_age_s) or (fa is not None and fa >= fail_frame_age_s):
                            status = "FAIL"
                        elif (ga is not None and ga >= warn_gs_age_s) or (fa is not None and fa >= warn_frame_age_s):
                            status = "WARN"
                        elif warn_on_drops:
                            try:
                                if int(health.drop_frame_queue or 0) > 0 or int(health.drop_gs_queue or 0) > 0:
                                    status = "WARN"
                            except Exception:
                                pass
                        elif health.warn:
                            status = "WARN"

                    self.health_status_var.set(status)
                    try:
                        if status == "OK":
                            self._health_status_label.config(fg="#1b7f3a")
                        elif status == "WARN":
                            self._health_status_label.config(fg="#b26a00")
                        else:
                            self._health_status_label.config(fg="#b00020")
                    except Exception:
                        pass

                    # Anchor status (ROI auto-alignment)
                    try:
                        try:
                            warn_score = float(os.getenv("UI_ANCHOR_WARN_SCORE", "0.60").strip() or "0.60")
                        except Exception:
                            warn_score = 0.60
                        try:
                            fail_score = float(os.getenv("UI_ANCHOR_FAIL_SCORE", "0.48").strip() or "0.48")
                        except Exception:
                            fail_score = 0.48

                        dx = getattr(health, "roi_offset_dx_px", None)
                        dy = getattr(health, "roi_offset_dy_px", None)
                        sc = getattr(health, "roi_offset_score", None)

                        if dx is None and dy is None and sc is None:
                            self.anchor_status_var.set("OFF")
                            self.anchor_var.set("-")
                            try:
                                self._anchor_status_label.config(fg="#666666")
                            except Exception:
                                pass
                        else:
                            # Show numeric values when available.
                            try:
                                dx_s = "?" if dx is None else f"{float(dx):.0f}"
                            except Exception:
                                dx_s = "?"
                            try:
                                dy_s = "?" if dy is None else f"{float(dy):.0f}"
                            except Exception:
                                dy_s = "?"
                            try:
                                sc_s = "?" if sc is None else f"{float(sc):.2f}"
                            except Exception:
                                sc_s = "?"
                            self.anchor_var.set(f"dx={dx_s} dy={dy_s} score={sc_s}")

                            a_status = "OK"
                            try:
                                sc_f = float(sc) if sc is not None else None
                            except Exception:
                                sc_f = None
                            if sc_f is None:
                                a_status = "ON"
                            elif sc_f < fail_score:
                                a_status = "FAIL"
                            elif sc_f < warn_score:
                                a_status = "WARN"

                            self.anchor_status_var.set(a_status)
                            try:
                                if a_status == "OK":
                                    self._anchor_status_label.config(fg="#1b7f3a")
                                elif a_status == "WARN":
                                    self._anchor_status_label.config(fg="#b26a00")
                                elif a_status == "FAIL":
                                    self._anchor_status_label.config(fg="#b00020")
                                else:
                                    self._anchor_status_label.config(fg="#1f6feb")
                            except Exception:
                                pass
                    except Exception:
                        self.anchor_status_var.set("-")
                        self.anchor_var.set("-")

                    # Idle status (UI-side, derived from telemetry coords)
                    try:
                        try:
                            idle_warn_s = float(
                                (os.getenv("UI_IDLE_WARN_S", "").strip() or os.getenv("ASSIST_IDLE_ALERT_S", "0")).strip()
                                or "0"
                            )
                        except Exception:
                            idle_warn_s = 0.0
                        idle_warn_s = max(0.0, float(idle_warn_s))

                        try:
                            idle_fail_raw = os.getenv("UI_IDLE_FAIL_S", "").strip()
                            idle_fail_s = (
                                float(idle_fail_raw)
                                if idle_fail_raw
                                else (
                                    max(idle_warn_s * 2.0, idle_warn_s + 30.0) if idle_warn_s > 0 else 0.0
                                )
                            )
                        except Exception:
                            idle_fail_s = max(idle_warn_s * 2.0, idle_warn_s + 30.0) if idle_warn_s > 0 else 0.0
                        idle_fail_s = max(0.0, float(idle_fail_s))

                        if idle_warn_s <= 0.0:
                            self.idle_status_var.set("OFF")
                            self.idle_var.set("-")
                            try:
                                self._idle_status_label.config(fg="#666666")
                            except Exception:
                                pass
                        else:
                            gx = getattr(tel, "pos_x", None)
                            gy = getattr(tel, "pos_y", None)
                            gz = getattr(tel, "pos_z", None)

                            # If coords are not trusted/available, don't compute idle (it would be garbage).
                            if coords_status in {"NO_COORDS", "BAD_JUMP", "UNSTABLE", "DISABLED"}:
                                self.idle_status_var.set("-")
                                jump_s = "" if coords_jump is None else f" (jump={coords_jump})"
                                self.idle_var.set(f"coords {coords_status or 'unknown'}{jump_s}")
                                try:
                                    self._idle_status_label.config(fg="#666666")
                                except Exception:
                                    pass

                            elif gx is None or gy is None:
                                self.idle_status_var.set("-")
                                self.idle_var.set("no coords")
                                try:
                                    self._idle_status_label.config(fg="#666666")
                                except Exception:
                                    pass
                            else:
                                try:
                                    key = (int(gx), int(gy), int(gz) if gz is not None else None)
                                except Exception:
                                    key = None

                                if key is None:
                                    self.idle_status_var.set("-")
                                    self.idle_var.set("no coords")
                                    try:
                                        self._idle_status_label.config(fg="#666666")
                                    except Exception:
                                        pass
                                else:
                                    if self._idle_last_pos_key is None:
                                        self._idle_last_pos_key = key
                                        self._idle_last_pos_change_ts = now
                                    elif key != self._idle_last_pos_key:
                                        self._idle_last_pos_key = key
                                        self._idle_last_pos_change_ts = now
                                    idle_for = max(0.0, now - float(self._idle_last_pos_change_ts or now))

                                    z_s = "" if key[2] is None else f",{key[2]}"
                                    self.idle_var.set(f"pos={key[0]},{key[1]}{z_s} | idle {idle_for:.0f}s")

                                    i_status = "OK" if idle_for < idle_warn_s else "WARN"
                                    if idle_fail_s > 0.0 and idle_for >= idle_fail_s:
                                        i_status = "FAIL"
                                    self.idle_status_var.set(i_status)
                                    try:
                                        if i_status == "OK":
                                            self._idle_status_label.config(fg="#1b7f3a")
                                        elif i_status == "WARN":
                                            self._idle_status_label.config(fg="#b26a00")
                                        else:
                                            self._idle_status_label.config(fg="#b00020")
                                    except Exception:
                                        pass
                    except Exception:
                        try:
                            self.idle_status_var.set("-")
                            self.idle_var.set("-")
                            try:
                                self._idle_status_label.config(fg="#666666")
                            except Exception:
                                pass
                        except Exception:
                            pass

                    # Stuck status (diagnostico del bot en tel.note)
                    try:
                        note = str(getattr(tel, "note", "") or "").strip()
                        s_reason = str(getattr(tel, "stuck_reason", "") or "").strip()
                        s_idle = getattr(tel, "stuck_idle_s", None)
                        s_block = getattr(tel, "stuck_blockers", None)
                        s_extra = str(getattr(tel, "stuck_extra", "") or "").strip()

                        if not s_reason and not note:
                            self.stuck_status_var.set("OFF")
                            self.stuck_var.set("-")
                            try:
                                self._stuck_status_label.config(fg="#666666")
                            except Exception:
                                pass
                        elif s_reason:
                            status = "FAIL" if s_reason == "STALE_GS" else "WARN"
                            self.stuck_status_var.set(status)
                            if note.startswith("Ôøö Stuck:"):
                                self.stuck_var.set(note)
                            else:
                                try:
                                    idle_s = "?" if s_idle is None else f"{float(s_idle):.0f}s"
                                except Exception:
                                    idle_s = "?"
                                try:
                                    blk_s = "?" if s_block is None else str(int(s_block))
                                except Exception:
                                    blk_s = "?"
                                msg = f"Ôøö Stuck: {s_reason} | idle {idle_s} | blockers {blk_s}"
                                if s_extra:
                                    msg = f"{msg} | {s_extra}"
                                self.stuck_var.set(msg)
                            try:
                                if status == "FAIL":
                                    self._stuck_status_label.config(fg="#b00020")
                                else:
                                    self._stuck_status_label.config(fg="#b26a00")
                            except Exception:
                                pass
                        else:
                            # Any other note (informational)
                            self.stuck_status_var.set("ON")
                            self.stuck_var.set(note)
                            try:
                                self._stuck_status_label.config(fg="#1f6feb")
                            except Exception:
                                pass
                    except Exception:
                        self.stuck_status_var.set("-")
                        self.stuck_var.set("-")

                    # Panel de eventos + checklist: detectar cambios relevantes.
                    try:
                        def _ts() -> str:
                            try:
                                return time.strftime("%H:%M:%S")
                            except Exception:
                                return "--:--:--"

                        def _push(line: str) -> None:
                            try:
                                self._event_lines.append(line)
                                if len(self._event_lines) > int(self._event_max_lines):
                                    self._event_lines = self._event_lines[-int(self._event_max_lines) :]
                                # render
                                try:
                                    self._events_text.configure(state="normal")
                                except Exception:
                                    pass
                                try:
                                    self._events_text.delete("1.0", "end")
                                    self._events_text.insert("end", "\n".join(self._event_lines))
                                    self._events_text.see("end")
                                except Exception:
                                    pass
                                try:
                                    self._events_text.configure(state="disabled")
                                except Exception:
                                    pass
                            except Exception:
                                pass

                        tgt = str(getattr(tel, "target", "") or "")
                        reco = str(getattr(tel, "recommendation", "") or "")
                        action_req = str(getattr(tel, "action_request", "") or "")
                        action_requests = getattr(tel, "action_requests", None)
                        action_source = str(getattr(tel, "action_source", "") or "")
                        action_committed = getattr(tel, "action_committed", None)
                        input_plan = str(getattr(tel, "input_plan", "") or "")
                        self._latest_input_plan = input_plan

                        def _clip1(s: object, n: int = 80) -> str:
                            try:
                                t = str(s or "").replace("\n", " ").replace("\r", " ").strip()
                            except Exception:
                                return ""
                            if len(t) > int(n):
                                return t[: max(0, int(n) - 3)] + "..."
                            return t

                        def _fmt_actions_lines_ui(reqs) -> list[str]:
                            try:
                                if not isinstance(reqs, list) or not reqs:
                                    return []
                                out: list[str] = []
                                for r in reqs:
                                    if not isinstance(r, dict):
                                        continue
                                    kind = _clip1(r.get("kind", ""), 18)
                                    value = _clip1(r.get("value", ""), 42)
                                    committed = bool(r.get("committed", False))
                                    note = str(r.get("note", "") or "").strip().lower()
                                    star = "*" if committed or note == "committed" else ""
                                    if kind or value:
                                        base = f"{kind}:{value}" if kind else f"{value}"
                                        out.append(f"{base}{star}")
                                return out
                            except Exception:
                                return []

                        # Build multi-line operator display: inputs + actions.
                        try:
                            disp_lines: list[str] = []

                            ip = (input_plan or "").strip()
                            if ip:
                                disp_lines.append(_clip1(ip, 110))

                            acts_lines = _fmt_actions_lines_ui(action_requests)
                            if acts_lines:
                                disp_lines.append("actions:")
                                for ln in acts_lines[:5]:
                                    disp_lines.append(f"  {ln}")

                                # Clipboard-friendly multi-line list.
                                try:
                                    self._latest_action_requests_display = "\n".join(acts_lines)
                                except Exception:
                                    self._latest_action_requests_display = "".join(acts_lines)

                            if not disp_lines:
                                self.input_plan_text.set("-")
                            else:
                                self.input_plan_text.set("\n".join(disp_lines))
                        except Exception:
                            self.input_plan_text.set("-")
                        wp = str(getattr(tel, "cavebot_waypoint", "") or "")
                        wp_action = str(getattr(tel, "cavebot_action", "") or "")

                        def _fmt_actions_ui(reqs) -> str:
                            try:
                                if not isinstance(reqs, list) or not reqs:
                                    return ""
                                parts = []
                                for r in reqs:
                                    if not isinstance(r, dict):
                                        continue
                                    kind = str(r.get("kind", "") or "").strip()
                                    value = str(r.get("value", "") or "").strip()
                                    committed = bool(r.get("committed", False))
                                    note = str(r.get("note", "") or "").strip().lower()
                                    star = "*" if committed or note == "committed" else ""
                                    if kind or value:
                                        parts.append(f"{kind}:{value}{star}" if kind else f"{value}{star}")
                                return ";".join(parts)
                            except Exception:
                                return ""

                        action_req_struct = _fmt_actions_ui(action_requests)
                        action_req_display = action_req_struct or action_req
                        # Keep a compact single-line representation for event stream;
                        # clipboard uses the multi-line value when available.
                        if not str(getattr(self, "_latest_action_requests_display", "") or "").strip():
                            self._latest_action_requests_display = action_req_struct

                        if action_source != self._last_event_action_source:
                            try:
                                ssrc = (action_source or "").strip()
                                if ssrc:
                                    if len(ssrc) > 100:
                                        ssrc = ssrc[:97] + "..."
                                    _push(f"{_ts()} planner: {ssrc}")
                            except Exception:
                                pass
                            self._last_event_action_source = action_source

                        # Coords confidence transitions (structured telemetry)
                        coords_status = str(getattr(tel, "coords_status", "") or "")
                        coords_jump = getattr(tel, "coords_jump", None)

                        def _coords_trouble(s: str) -> bool:
                            return str(s or "") in {"NO_COORDS", "UNSTABLE", "BAD_JUMP", "DISABLED"}

                        if coords_status != self._last_event_coords_status:
                            if _coords_trouble(coords_status) or _coords_trouble(self._last_event_coords_status):
                                jump_s = "" if coords_jump is None else f" (jump={coords_jump})"
                                _push(f"{_ts()} coords: {coords_status or 'OK'}{jump_s}")
                            self._last_event_coords_status = coords_status

                        # Stuck reason transitions (structured telemetry)
                        s_reason = str(getattr(tel, "stuck_reason", "") or "").strip()
                        s_idle = getattr(tel, "stuck_idle_s", None)
                        s_block = getattr(tel, "stuck_blockers", None)
                        s_extra = str(getattr(tel, "stuck_extra", "") or "").strip()
                        if s_reason != self._last_event_stuck_reason:
                            if s_reason:
                                try:
                                    idle_s = "?" if s_idle is None else f"{float(s_idle):.0f}s"
                                except Exception:
                                    idle_s = "?"
                                try:
                                    blk_s = "?" if s_block is None else str(int(s_block))
                                except Exception:
                                    blk_s = "?"
                                extra_s = f" | {s_extra}" if s_extra else ""
                                _push(f"{_ts()} stuck: {s_reason} | idle {idle_s} | blockers {blk_s}{extra_s}")
                            elif self._last_event_stuck_reason:
                                _push(f"{_ts()} stuck: cleared")
                            self._last_event_stuck_reason = s_reason

                        # StepNavigator index transitions (structured telemetry)
                        try:
                            step_total = int(getattr(tel, "cavebot_step_total", 0) or 0)
                            step_idx = int(getattr(tel, "cavebot_step_idx", 0) or 0)
                        except Exception:
                            step_total = 0
                            step_idx = 0

                        if step_total > 0:
                            prev_total = int(self._last_event_step_total or 0)
                            prev_idx = self._last_event_step_idx
                            if prev_total <= 0:
                                # Step mode just became active.
                                _push(f"{_ts()} step: {step_idx + 1}/{step_total} ({wp})")
                            elif prev_idx is not None and step_idx != int(prev_idx):
                                _push(f"{_ts()} step: {step_idx + 1}/{step_total} ({wp})")
                            self._last_event_step_total = step_total
                            self._last_event_step_idx = step_idx
                        else:
                            # Reset when not in step mode.
                            self._last_event_step_total = 0
                            self._last_event_step_idx = None

                        if tgt != self._last_event_target:
                            _push(f"{_ts()} target: {tgt}")
                            self._last_event_target = tgt

                        if reco != self._last_event_reco:
                            _push(f"{_ts()} reco: {reco}")
                            self._last_event_reco = reco

                        if (
                            action_req_display != self._last_event_action_req
                            or self._last_event_action_committed is None
                            or (action_committed is not None and bool(action_committed) != bool(self._last_event_action_committed))
                        ):
                            star = "*" if bool(action_committed) else ""
                            _push(f"{_ts()} action{star}: {action_req_display}")
                            self._last_event_action_req = action_req_display
                            self._last_event_action_committed = bool(action_committed) if action_committed is not None else None

                        if input_plan != self._last_event_input_plan:
                            if input_plan.strip():
                                _push(f"{_ts()} inputs: {input_plan}")
                            self._last_event_input_plan = input_plan

                        if wp != self._last_event_wp or wp_action != self._last_event_wp_action:
                            if wp or wp_action:
                                _push(f"{_ts()} cavebot: {wp} action={wp_action}")
                            self._last_event_wp = wp
                            self._last_event_wp_action = wp_action

                        # Health / Idle status transitions
                        hs = str(self.health_status_var.get() or "")
                        if hs and hs != self._last_event_health_status and hs in {"WARN", "FAIL"}:
                            _push(f"{_ts()} health: {hs}")
                        self._last_event_health_status = hs

                        is_ = str(self.idle_status_var.get() or "")
                        if is_ and is_ != self._last_event_idle_status and is_ in {"WARN", "FAIL"}:
                            _push(f"{_ts()} idle: {is_} ({self.idle_var.get()})")
                        self._last_event_idle_status = is_

                        # Note / stuck diagnostics
                        try:
                            note = str(getattr(tel, "note", "") or "").strip()
                        except Exception:
                            note = ""
                        if note and note != self._last_event_note:
                            _push(f"{_ts()} note: {note}")
                            self._last_event_note = note

                        # Checklist: resaltar waypoint actual si podemos mapearlo.
                        try:
                            _load_route_for_ui()
                            if self._cavebot_follow_ui and self._route_items:
                                cur_idx = None
                                # Prefer StepNavigator structured index when available.
                                try:
                                    step_total = int(getattr(tel, "cavebot_step_total", 0) or 0)
                                    step_idx = int(getattr(tel, "cavebot_step_idx", 0) or 0)
                                except Exception:
                                    step_total = 0
                                    step_idx = 0

                                if step_total > 0:
                                    cur_idx = max(0, step_idx)
                                    # Bound by loaded route length.
                                    try:
                                        if cur_idx >= len(self._route_items):
                                            cur_idx = max(0, len(self._route_items) - 1)
                                    except Exception:
                                        pass

                                if cur_idx is None and wp:
                                    # match by name or by "(x,y" string
                                    for i, item in enumerate(self._route_items):
                                        wpi = item.get("wp")
                                        name_i = str(getattr(wpi, "name", "") or "")
                                        x_i = getattr(wpi, "x", None)
                                        y_i = getattr(wpi, "y", None)
                                        z_i = getattr(wpi, "z", None)
                                        if name_i and name_i == wp:
                                            cur_idx = i
                                            break
                                        if x_i is not None and y_i is not None:
                                            cand2 = f"({x_i},{y_i})"
                                            cand3 = f"({x_i},{y_i},{z_i})" if z_i is not None else ""
                                            if wp == cand2 or (cand3 and wp == cand3):
                                                cur_idx = i
                                                break
                                if cur_idx is not None and cur_idx != self._route_current_index:
                                    self._route_current_index = cur_idx
                                    try:
                                        self._route_listbox.selection_clear(0, "end")
                                        self._route_listbox.selection_set(cur_idx)
                                        self._route_listbox.see(cur_idx)
                                    except Exception:
                                        pass

                                # Proximos N
                                try:
                                    n = 3
                                    if self._route_current_index is not None:
                                        start = min(len(self._route_items) - 1, max(0, int(self._route_current_index)))
                                        nxt = []
                                        for j in range(start, min(len(self._route_items), start + n)):
                                            wpp = self._route_items[j].get("wp")
                                            label = getattr(wpp, "name", None) or f"({getattr(wpp, 'x', '?')},{getattr(wpp, 'y', '?')})"
                                            nxt.append(str(label))
                                        self._route_next_var.set(" -> ".join(nxt) if nxt else "-")
                                    else:
                                        self._route_next_var.set("-")
                                except Exception:
                                    pass
                            elif not self._cavebot_follow_ui:
                                try:
                                    self._route_current_index = None
                                    self._route_listbox.selection_clear(0, "end")
                                    self._route_next_var.set("-")
                                except Exception:
                                    pass
                        except Exception:
                            pass
                    except Exception:
                        pass
                except Exception:
                    self.health_var.set("-")
                    try:
                        self.health_status_var.set("-")
                    except Exception:
                        pass
            except Exception:
                pass
            self.root.after(250, poll_telemetry)

        poll_telemetry()

        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        try:
            self.root.bind("<Unmap>", self._on_window_state)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Tray helpers
    # ------------------------------------------------------------------
    def _on_window_state(self, event=None) -> None:
        try:
            if self.root.state() == "iconic":
                self._minimize_to_tray()
        except Exception:
            pass

    def _on_window_close(self) -> None:
        # UX: clicking the window close button (X) should close the UI.
        # Minimize-to-tray is available via the explicit "Minimizar" button.
        self._allow_close = True
        self.on_close()

    def _minimize_to_tray(self) -> bool:
        if self._pystray is None or self._TrayImage is None:
            try:
                self.root.iconify()
            except Exception:
                pass
            return True

        self._start_tray_icon()
        try:
            self.root.withdraw()
        except Exception:
            pass
        try:
            self.status_var.set("Minimizado a bandeja")
        except Exception:
            pass
        return True

    def _start_tray_icon(self) -> None:
        if self._tray_icon is not None:
            return
        if self._pystray is None or self._TrayImage is None:
            return
        if self._tray_image is None:
            self._tray_image = self._make_tray_image()
        if self._tray_image is None:
            return

        menu = None
        try:
            menu = self._pystray.Menu(
                self._pystray.MenuItem("Restaurar", lambda: self.root.after(0, self._restore_from_tray), default=True),
                self._pystray.MenuItem("Salir", lambda: self.root.after(0, self._tray_quit)),
            )
        except Exception:
            menu = None

        try:
            self._tray_icon = self._pystray.Icon("tibia_bot", self._tray_image, "Tibia Bot Framework", menu)
        except Exception:
            self._tray_icon = None
            return

        def _run_icon():
            try:
                icon = self._tray_icon
                if icon is not None:
                    icon.run()
            except Exception:
                pass

        try:
            self._tray_thread = threading.Thread(target=_run_icon, daemon=True)
            self._tray_thread.start()
        except Exception:
            self._tray_icon = None
            self._tray_thread = None

    def _stop_tray_icon(self) -> None:
        icon = self._tray_icon
        try:
            if icon is not None:
                icon.stop()
        except Exception:
            pass
        self._tray_icon = None
        self._tray_thread = None

    def _restore_from_tray(self) -> None:
        self._stop_tray_icon()
        try:
            self.root.deiconify()
            self.root.state("normal")
            self.root.focus_force()
        except Exception:
            pass
        try:
            self.status_var.set("Detenido" if not self._is_running() else "Ejecutandose")
        except Exception:
            pass

    def _tray_quit(self) -> None:
        self._allow_close = True
        try:
            self.root.after(0, self.on_close)
        except Exception:
            pass

    def _make_tray_image(self):
        try:
            if self._TrayImage is None or self._TrayImageDraw is None:
                return None
            img = self._TrayImage.new("RGBA", (64, 64), (34, 34, 34, 0))
            d = self._TrayImageDraw.Draw(img)
            d.rectangle((8, 8, 56, 56), fill=(30, 144, 255, 255))
            d.rectangle((14, 14, 50, 50), fill=(10, 10, 10, 255))
            d.text((20, 22), "TB", fill=(255, 255, 255, 255))
            return img
        except Exception:
            return None

    def _reset_idle_ui(self) -> None:
        try:
            self._idle_last_pos_key = None
            self._idle_last_pos_change_ts = 0.0
            self.idle_status_var.set("OFF")
            self.idle_var.set("-")
            try:
                self._idle_status_label.config(fg="#666666")
            except Exception:
                pass
        except Exception:
            pass

    def _reset_cavebot_ui(self) -> None:
        try:
            self.cavebot_step_text.set("-")
        except Exception:
            pass
        try:
            self.cavebot_finish_text.set("-")
            try:
                self.cavebot_block_text.set("-")
            except Exception:
                pass
        except Exception:
            pass
        try:
            if hasattr(self, "_cavebot_mark_btn") and self._cavebot_mark_btn is not None:
                self._cavebot_mark_btn.config(state="disabled")
            if hasattr(self, "_cavebot_next_btn") and self._cavebot_next_btn is not None:
                self._cavebot_next_btn.config(state="disabled")
        except Exception:
            pass
        try:
            self._route_current_index = None
        except Exception:
            pass
        try:
            self._cavebot_follow_ui = False
        except Exception:
            pass
        try:
            self._route_next_var.set("-")
        except Exception:
            pass
        try:
            self._route_listbox.selection_clear(0, "end")
        except Exception:
            pass

    def _is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _ui_settings_path(self) -> Path:
        """Return the JSON path used to persist UI settings.

        Controlled by env var UI_SETTINGS_FILE; defaults to configs/ui_settings.json.
        Relative paths are resolved from repo root.
        """

        raw = (os.getenv("UI_SETTINGS_FILE", "") or "").strip()
        if not raw:
            return self._repo_root / "configs" / "ui_settings.json"
        p = Path(raw)
        if not p.is_absolute():
            p = self._repo_root / p
        return p

    def _load_ui_settings(self) -> None:
        p = self._ui_settings_path()
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return

        ov = data.get("overlay")
        if isinstance(ov, dict):
            try:
                if "enabled" in ov:
                    self.overlay_enabled.set(bool(ov.get("enabled")))
                if "out_dir" in ov:
                    self.overlay_out_dir.set(str(ov.get("out_dir") or "logs/debug_overlay"))
                if "interval_s" in ov:
                    self.overlay_interval_s.set(max(0.1, float(ov.get("interval_s") or 1.0)))
                if "tile_grid" in ov:
                    self.overlay_tile_grid.set(bool(ov.get("tile_grid")))
                if "tile_px" in ov:
                    self.overlay_tile_px.set(max(4, min(128, int(float(ov.get("tile_px") or 32)))))
                if "rois" in ov:
                    self.overlay_rois.set(str(ov.get("rois") or ""))
            except Exception:
                pass

        # RuntimeConfig-backed settings
        try:
            h = data.get("healing")
            if isinstance(h, dict):
                if "enabled" in h:
                    self.healing_enabled.set(bool(h.get("enabled")))
                if "hp_below_pct" in h:
                    self.heal_hp_below_pct.set(int(float(h.get("hp_below_pct") or 0)))
                if "hp_recover_pct" in h:
                    self.heal_hp_recover_pct.set(int(float(h.get("hp_recover_pct") or 0)))
                if "mp_below_pct" in h:
                    self.heal_mp_below_pct.set(int(float(h.get("mp_below_pct") or 0)))
                if "mp_recover_pct" in h:
                    self.heal_mp_recover_pct.set(int(float(h.get("mp_recover_pct") or 0)))
                if "action" in h:
                    self.heal_action.set(str(h.get("action") or ""))
                if "hp_action" in h:
                    self.heal_hp_action.set(str(h.get("hp_action") or ""))
                if "mp_action" in h:
                    self.heal_mp_action.set(str(h.get("mp_action") or ""))
                if "cooldown_s" in h:
                    self.heal_cooldown_s.set(float(h.get("cooldown_s") or 0.0))
        except Exception:
            pass

        try:
            cb = data.get("cavebot")
            if isinstance(cb, dict):
                if "enabled" in cb:
                    self.cavebot_enabled.set(bool(cb.get("enabled")))
                if "route_path" in cb:
                    self.cavebot_route_path.set(str(cb.get("route_path") or "configs/route.json"))
                if "mode" in cb:
                    m = str(cb.get("mode") or "").strip().lower()
                    if m in {"steps", "pos"}:
                        self.cavebot_mode.set(m)
                if "coords_provider" in cb:
                    cp = str(cb.get("coords_provider") or "").strip().lower()
                    if cp in {"auto", "ocr", "minimap", "disabled"}:
                        self.coords_provider.set(cp)

                # Minimap calibration + fallback policy
                if "minimap_seed_x" in cb:
                    self.minimap_seed_x.set(str(cb.get("minimap_seed_x") or "").strip())
                if "minimap_seed_y" in cb:
                    self.minimap_seed_y.set(str(cb.get("minimap_seed_y") or "").strip())
                if "minimap_seed_z" in cb:
                    self.minimap_seed_z.set(str(cb.get("minimap_seed_z") or "").strip())
                if "minimap_fallback_mode" in cb:
                    m = str(cb.get("minimap_fallback_mode") or "").strip().lower()
                    if m in {"steps", "ocr"}:
                        self.minimap_fallback_mode.set(m)
                if "minimap_fallback_conf_threshold" in cb:
                    try:
                        v = float(cb.get("minimap_fallback_conf_threshold") or 0.0)
                        self.minimap_fallback_conf_threshold.set(max(0.0, min(1.0, v)))
                    except Exception:
                        pass
                if "minimap_fallback_n_ticks" in cb:
                    try:
                        v = int(float(cb.get("minimap_fallback_n_ticks") or 1))
                        self.minimap_fallback_n_ticks.set(max(1, v))
                    except Exception:
                        pass
                if "loop" in cb:
                    self.cavebot_loop.set(bool(cb.get("loop")))

                # Stop conditions (assistant-only)
                if "stop_on_low_cap" in cb:
                    self.cavebot_stop_on_low_cap.set(bool(cb.get("stop_on_low_cap")))
                if "cap_threshold" in cb:
                    self.cavebot_cap_threshold.set(max(0, int(float(cb.get("cap_threshold") or 0))))
                if "stop_on_low_potions" in cb:
                    self.cavebot_stop_on_low_potions.set(bool(cb.get("stop_on_low_potions")))
                if "potions_remaining" in cb:
                    self.cavebot_potions_remaining.set(max(0, int(float(cb.get("potions_remaining") or 0))))
                if "potions_min" in cb:
                    self.cavebot_potions_min.set(max(0, int(float(cb.get("potions_min") or 0))))

                # Recovery (assistant-only)
                if "recovery_enabled" in cb:
                    self.cavebot_recovery_enabled.set(bool(cb.get("recovery_enabled")))
                if "recovery_idle_s" in cb:
                    self.cavebot_recovery_idle_s.set(max(0.0, float(cb.get("recovery_idle_s") or 0.0)))
                if "recovery_max_attempts" in cb:
                    self.cavebot_recovery_max_attempts.set(max(1, int(float(cb.get("recovery_max_attempts") or 1))))
                if "recovery_stop_on_fail" in cb:
                    self.cavebot_recovery_stop_on_fail.set(bool(cb.get("recovery_stop_on_fail")))
        except Exception:
            pass

        try:
            s = data.get("simulation")
            if isinstance(s, dict):
                if "enabled" in s:
                    self.sim_enabled.set(bool(s.get("enabled")))
                if "paralyzed" in s:
                    self.sim_paralyzed.set(bool(s.get("paralyzed")))
                if "haste_active" in s:
                    self.sim_haste_active.set(bool(s.get("haste_active")))
                if "utamo_active" in s:
                    self.sim_utamo_active.set(bool(s.get("utamo_active")))
                if "hungry" in s:
                    self.sim_hungry.set(bool(s.get("hungry")))
        except Exception:
            pass

        try:
            a = data.get("assistant")
            if isinstance(a, dict):
                if "enabled" in a:
                    self.asst_enabled.set(bool(a.get("enabled")))
                if "confirm_actions" in a:
                    self.asst_confirm.set(bool(a.get("confirm_actions")))
                if "sound_alerts" in a:
                    self.asst_sound.set(bool(a.get("sound_alerts")))
                if "input_mode" in a:
                    mode = str(a.get("input_mode") or "log").strip().lower()
                    if mode not in {"log", "mock", "keyboard", "wininput"}:
                        mode = "log"
                    self.asst_input_mode.set(mode)
                if "target_hotkey" in a:
                    self.asst_target_hotkey.set(str(a.get("target_hotkey") or ""))
                if "minimap_hotkey" in a:
                    self.asst_minimap_hotkey.set(str(a.get("minimap_hotkey") or ""))
                if "live_input_armed" in a:
                    try:
                        self.asst_live_input_armed.set(bool(a.get("live_input_armed")))
                    except Exception:
                        self.asst_live_input_armed.set(False)
                if "allowed_window_titles" in a:
                    try:
                        raw = a.get("allowed_window_titles")
                        if isinstance(raw, list):
                            titles = [str(x).strip() for x in raw if str(x).strip()]
                        else:
                            titles = ["TibiaClone", "MyClient"]
                        self.asst_allowed_window_titles.set(",".join(titles))
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            r = data.get("replay")
            if isinstance(r, dict):
                if "enabled" in r:
                    self.replay_enabled.set(bool(r.get("enabled")))
                if "interval_ms" in r:
                    self.replay_interval_ms.set(int(float(r.get("interval_ms") or 0)))
                if "out_dir" in r:
                    self.replay_out_dir.set(str(r.get("out_dir") or self.replay_out_dir.get()))
        except Exception:
            pass

        try:
            lg = data.get("logging")
            if isinstance(lg, dict):
                if "enabled" in lg:
                    self.log_enabled.set(bool(lg.get("enabled")))
                if "interval_ms" in lg:
                    self.log_interval_ms.set(int(float(lg.get("interval_ms") or 0)))
                if "out_file" in lg:
                    self.log_out_file.set(str(lg.get("out_file") or self.log_out_file.get()))
        except Exception:
            pass

        # Env-backed per-run settings
        try:
            idle = data.get("idle")
            if isinstance(idle, dict):
                if "alert_s" in idle:
                    self.idle_alert_s.set(max(0.0, float(idle.get("alert_s") or 0.0)))
                if "repeat_s" in idle:
                    self.idle_repeat_s.set(max(1.0, float(idle.get("repeat_s") or 10.0)))
                if "ui_fail_s" in idle:
                    self.ui_idle_fail_s.set(max(0.0, float(idle.get("ui_fail_s") or 0.0)))
        except Exception:
            pass

        try:
            ro = data.get("rois")
            if isinstance(ro, dict):
                if "config_override" in ro:
                    self.rois_config_override.set(str(ro.get("config_override") or ""))
        except Exception:
            pass

        try:
            preset = data.get("overlay_preset")
            if isinstance(preset, str) and preset:
                self.overlay_preset.set(preset)
        except Exception:
            pass

        try:
            tp = data.get("telemetry_preset")
            if isinstance(tp, str) and tp:
                self.telemetry_preset.set(tp)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Rutas / Cavebot editor
    # ------------------------------------------------------------------
    def _build_routes_tab(self, tab) -> None:
        tk = self._tk
        ttk = self._ttk

        main = tk.Frame(tab, padx=10, pady=10)
        main.pack(fill="both", expand=True)
        main.grid_columnconfigure(0, weight=3)
        main.grid_columnconfigure(1, weight=2)

        path_row = tk.Frame(main)
        path_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        tk.Label(path_row, text="Ruta carpeta:").grid(row=0, column=0, sticky="w")
        tk.Entry(path_row, textvariable=self.route_path_var, width=48).grid(row=0, column=1, sticky="ew", padx=(6, 6))
        tk.Button(path_row, text="Nueva", width=8, command=self._route_new).grid(row=0, column=2, padx=(0, 4))
        tk.Button(path_row, text="Abrir", width=8, command=self._route_open).grid(row=0, column=3, padx=(0, 4))
        tk.Button(path_row, text="Guardar", width=8, command=self._route_save).grid(row=0, column=4, padx=(0, 4))
        tk.Button(path_row, text="Validar", width=8, command=self._route_validate).grid(row=0, column=5)

        # Steps editor (left)
        steps_frame = tk.Frame(main)
        steps_frame.grid(row=1, column=0, sticky="nsew")
        steps_frame.grid_rowconfigure(1, weight=1)
        steps_frame.grid_columnconfigure(0, weight=1)

        columns = ("#", "Tipo", "X", "Y", "Z", "Nombre/Accion", "Params", "Comment", "Enabled")
        self.route_tree = ttk.Treeview(steps_frame, columns=columns, show="headings", height=14)
        for col, w in zip(columns, [40, 90, 70, 70, 50, 140, 120, 120, 70]):
            self.route_tree.heading(col, text=col)
            self.route_tree.column(col, width=w, anchor="w")
        vsb = ttk.Scrollbar(steps_frame, orient="vertical", command=self.route_tree.yview)
        self.route_tree.configure(yscrollcommand=vsb.set)
        self.route_tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")
        self.route_tree.bind("<<TreeviewSelect>>", lambda _e: self._route_on_select())

        # Form
        form = tk.Frame(steps_frame)
        form.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        self.route_kind_var = tk.StringVar(value="node")
        self.route_x_var = tk.StringVar(value="")
        self.route_y_var = tk.StringVar(value="")
        self.route_z_var = tk.StringVar(value="")
        self.route_name_var = tk.StringVar(value="")
        self.route_params_var = tk.StringVar(value="")
        self.route_comment_var = tk.StringVar(value="")
        self.route_enabled_var = tk.BooleanVar(value=True)

        tk.Label(form, text="Tipo").grid(row=0, column=0, sticky="w")
        ttk.Combobox(form, values=["label", "action", "node", "stand", "rope", "ladder", "move", "custom"], textvariable=self.route_kind_var, width=10, state="readonly").grid(row=0, column=1, sticky="w")
        tk.Label(form, text="X").grid(row=0, column=2, sticky="w")
        tk.Entry(form, textvariable=self.route_x_var, width=8).grid(row=0, column=3, sticky="w")
        tk.Label(form, text="Y").grid(row=0, column=4, sticky="w")
        tk.Entry(form, textvariable=self.route_y_var, width=8).grid(row=0, column=5, sticky="w")
        tk.Label(form, text="Z").grid(row=0, column=6, sticky="w")
        tk.Entry(form, textvariable=self.route_z_var, width=5).grid(row=0, column=7, sticky="w")

        tk.Label(form, text="Nombre/Accion").grid(row=1, column=0, sticky="w")
        tk.Entry(form, textvariable=self.route_name_var, width=28).grid(row=1, column=1, columnspan=3, sticky="w")
        tk.Label(form, text="Params").grid(row=1, column=4, sticky="w")
        tk.Entry(form, textvariable=self.route_params_var, width=18).grid(row=1, column=5, columnspan=2, sticky="w")
        tk.Label(form, text="Comment").grid(row=2, column=0, sticky="w")
        tk.Entry(form, textvariable=self.route_comment_var, width=46).grid(row=2, column=1, columnspan=5, sticky="w")
        tk.Checkbutton(form, text="Enabled", variable=self.route_enabled_var).grid(row=2, column=6, sticky="w")

        btn_row = tk.Frame(steps_frame)
        btn_row.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        tk.Button(btn_row, text="Agregar", width=10, command=self._route_add_step).grid(row=0, column=0, padx=(0, 4))
        tk.Button(btn_row, text="Insertar", width=10, command=lambda: self._route_add_step(insert=True)).grid(row=0, column=1, padx=(0, 4))
        tk.Button(btn_row, text="Duplicar", width=10, command=self._route_duplicate_step).grid(row=0, column=2, padx=(0, 4))
        tk.Button(btn_row, text="Subir", width=8, command=lambda: self._route_move_step(-1)).grid(row=0, column=3, padx=(0, 4))
        tk.Button(btn_row, text="Bajar", width=8, command=lambda: self._route_move_step(1)).grid(row=0, column=4, padx=(0, 4))
        tk.Button(btn_row, text="Eliminar", width=10, command=self._route_delete_step).grid(row=0, column=5, padx=(0, 4))
        tk.Button(btn_row, text="Aplicar cambios", width=14, command=self._route_apply_form).grid(row=0, column=6, padx=(0, 4))

        tpl_row = tk.Frame(steps_frame)
        tpl_row.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        tk.Button(tpl_row, text="Desde posicion", width=14, command=self._route_add_from_position).grid(row=0, column=0, padx=(0, 4))
        tk.Button(tpl_row, text="Agregar MOVE", width=12, command=self._route_add_move).grid(row=0, column=1, padx=(0, 4))
        tk.Button(tpl_row, text="action=rope", width=12, command=lambda: self._route_template_action("rope")).grid(row=0, column=2, padx=(0, 4))
        tk.Button(tpl_row, text="action=abre_puerta", width=16, command=lambda: self._route_template_action("abre_puerta")).grid(row=0, column=3, padx=(0, 4))

        tk.Label(main, textvariable=self.route_status_var, fg="gray25").grid(row=5, column=0, sticky="w", pady=(6, 0))

        # Supplies editor (right)
        sup_frame = tk.LabelFrame(main, text="Supplies / setup_*.json", padx=8, pady=8)
        sup_frame.grid(row=1, column=1, rowspan=4, sticky="nsew", padx=(10, 0))

        tk.Label(sup_frame, text="Setup path").grid(row=0, column=0, sticky="w")
        tk.Entry(sup_frame, textvariable=self.route_setup_path_var, width=36).grid(row=0, column=1, sticky="w")
        tk.Button(sup_frame, text="Cargar", width=8, command=self._route_load_setup).grid(row=0, column=2, padx=(4, 0))
        tk.Button(sup_frame, text="Guardar", width=8, command=self._route_save_setup).grid(row=0, column=3, padx=(4, 0))

        tk.Label(sup_frame, text="mana_name").grid(row=1, column=0, sticky="w", pady=(6, 0))
        tk.Entry(sup_frame, textvariable=self.hc_mana_name, width=18).grid(row=1, column=1, sticky="w", pady=(6, 0))
        tk.Label(sup_frame, text="take_mana").grid(row=1, column=2, sticky="w", pady=(6, 0))
        tk.Entry(sup_frame, textvariable=self.hc_take_mana, width=8).grid(row=1, column=3, sticky="w", pady=(6, 0))

        tk.Label(sup_frame, text="mana_leave").grid(row=2, column=0, sticky="w", pady=(4, 0))
        tk.Entry(sup_frame, textvariable=self.hc_mana_leave, width=8).grid(row=2, column=1, sticky="w", pady=(4, 0))
        tk.Label(sup_frame, text="cap_leave").grid(row=2, column=2, sticky="w", pady=(4, 0))
        tk.Entry(sup_frame, textvariable=self.hc_cap_leave, width=8).grid(row=2, column=3, sticky="w", pady=(4, 0))

        tk.Label(sup_frame, text="Items (pociones/consumibles)").grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        self.route_items_list = tk.Listbox(sup_frame, height=8, width=28)
        self.route_items_list.grid(row=4, column=0, columnspan=2, sticky="nw")
        self.route_items_list.bind("<<ListboxSelect>>", lambda _e: self._route_on_item_select())
        btn_items = tk.Frame(sup_frame)
        btn_items.grid(row=4, column=2, columnspan=2, sticky="nw", padx=(6, 0))
        tk.Button(btn_items, text="Agregar/Actualizar", width=16, command=self._route_add_update_item).grid(row=0, column=0, pady=(0, 4))
        tk.Button(btn_items, text="Eliminar", width=10, command=self._route_delete_item).grid(row=1, column=0)

        tk.Label(sup_frame, text="item_name").grid(row=5, column=0, sticky="w", pady=(6, 0))
        tk.Entry(sup_frame, textvariable=self.item_name_var, width=20).grid(row=5, column=1, sticky="w", pady=(6, 0))
        tk.Label(sup_frame, text="hotkey").grid(row=5, column=2, sticky="w", pady=(6, 0))
        tk.Entry(sup_frame, textvariable=self.item_hotkey_var, width=8).grid(row=5, column=3, sticky="w", pady=(6, 0))

        tk.Label(sup_frame, text="use").grid(row=6, column=0, sticky="w", pady=(4, 0))
        ttk.Combobox(sup_frame, values=["self", "use", "target", "tile"], textvariable=self.item_use_var, width=10, state="readonly").grid(
            row=6, column=1, sticky="w"
        )

        self._route_refresh_tree()

    def _route_new(self) -> None:
        self.route_steps = []
        self.route_path_var.set("")
        self.route_status_var.set("Nueva ruta")
        self._route_refresh_tree()

    def _route_open(self) -> None:
        try:
            base = self._repo_root / "routes"
            path = self._filedialog.askopenfilename(title="Abrir waypoints.in", initialdir=str(base), filetypes=[("waypoints", "waypoints.in"), ("All", "*.*")])
        except Exception:
            path = ""
        if not path:
            return
        p = Path(path)
        self.route_path_var.set(str(p.parent))
        self._route_load_waypoints(p)

    def _route_save(self) -> None:
        route_dir = self.route_path_var.get().strip()
        if not route_dir:
            try:
                route_dir = self._filedialog.askdirectory(title="Selecciona carpeta de ruta", initialdir=str(self._repo_root / "routes"))
            except Exception:
                route_dir = ""
        if not route_dir:
            return
        self.route_path_var.set(route_dir)
        wp_path = Path(route_dir) / "waypoints.in"
        expanded, errs = self._expand_move_macros(self.route_steps)
        if errs:
            self.route_status_var.set(f"Errores MOVE: {errs[0]}")
            return
        try:
            wp_path.parent.mkdir(parents=True, exist_ok=True)
            wp_path.write_text(self._serialize_waypoints(expanded), encoding="utf-8")
            self.route_status_var.set(f"Guardado {wp_path}")
        except Exception as e:
            self.route_status_var.set(f"Error guardando: {e}")

    def _route_validate(self) -> None:
        expanded, errs = self._expand_move_macros(self.route_steps)
        errs += self._validate_waypoints(expanded)
        if errs:
            self.route_status_var.set(f"Errores: {errs[0]}")
        else:
            self.route_status_var.set("OK")

    def _route_load_waypoints(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            self.route_status_var.set(f"No pude leer {path}: {e}")
            return
        parsed = self._parse_waypoints(text)
        self.route_steps = parsed.steps
        if parsed.errors:
            self.route_status_var.set(f"Parse con errores: {parsed.errors[0]}")
        else:
            self.route_status_var.set(f"Cargado {path}")
        # guess setup path
        guess_setup = path.parent / "setup_ek.json"
        if guess_setup.exists():
            self.route_setup_path_var.set(str(guess_setup))
        self._route_refresh_tree()

    def _route_refresh_tree(self) -> None:
        tree = getattr(self, "route_tree", None)
        if tree is None:
            return
        tree.delete(*tree.get_children())
        for idx, s in enumerate(self.route_steps, start=1):
            params = ""
            if s.params:
                params = ",".join(f"{k}={v}" for k, v in s.params.items())
            tree.insert("", "end", iid=str(idx - 1), values=(idx, s.kind, s.x or "", s.y or "", s.z or "", s.name, params, s.comment, "yes" if s.enabled else "no"))

    def _route_get_selected_index(self) -> int | None:
        tree = getattr(self, "route_tree", None)
        if tree is None:
            return None
        sel = tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except Exception:
            return None

    def _route_on_select(self) -> None:
        idx = self._route_get_selected_index()
        if idx is None or idx >= len(self.route_steps):
            return
        s = self.route_steps[idx]
        self.route_kind_var.set(s.kind)
        self.route_x_var.set("" if s.x is None else str(s.x))
        self.route_y_var.set("" if s.y is None else str(s.y))
        self.route_z_var.set("" if s.z is None else str(s.z))
        self.route_name_var.set(s.name)
        self.route_comment_var.set(s.comment)
        self.route_enabled_var.set(bool(s.enabled))
        if s.params:
            self.route_params_var.set(",".join(f"{k}={v}" for k, v in s.params.items()))
        else:
            self.route_params_var.set("")

    def _route_apply_form(self) -> None:
        idx = self._route_get_selected_index()
        if idx is None or idx >= len(self.route_steps):
            return
        s = self.route_steps[idx]
        s.kind = self.route_kind_var.get()
        s.name = self.route_name_var.get()
        s.comment = self.route_comment_var.get()
        s.enabled = bool(self.route_enabled_var.get())
        s.x = _to_int_or_none(self.route_x_var.get())
        s.y = _to_int_or_none(self.route_y_var.get())
        s.z = _to_int_or_none(self.route_z_var.get())
        s.params = _parse_params(self.route_params_var.get())
        self._route_refresh_tree()

    def _route_add_step(self, insert: bool = False) -> None:
        s = self._WaypointStep(
            kind=self.route_kind_var.get(),
            x=_to_int_or_none(self.route_x_var.get()),
            y=_to_int_or_none(self.route_y_var.get()),
            z=_to_int_or_none(self.route_z_var.get()),
            name=self.route_name_var.get(),
            params=_parse_params(self.route_params_var.get()),
            comment=self.route_comment_var.get(),
            enabled=bool(self.route_enabled_var.get()),
        )
        idx = self._route_get_selected_index()
        if insert and idx is not None:
            self.route_steps.insert(idx, s)
        else:
            self.route_steps.append(s)
        self._route_refresh_tree()

    def _route_delete_step(self) -> None:
        idx = self._route_get_selected_index()
        if idx is None:
            return
        if 0 <= idx < len(self.route_steps):
            self.route_steps.pop(idx)
        self._route_refresh_tree()

    def _route_duplicate_step(self) -> None:
        idx = self._route_get_selected_index()
        if idx is None or idx >= len(self.route_steps):
            return
        import copy

        clone = copy.deepcopy(self.route_steps[idx])
        self.route_steps.insert(idx + 1, clone)
        self._route_refresh_tree()

    def _route_move_step(self, delta: int) -> None:
        idx = self._route_get_selected_index()
        if idx is None:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self.route_steps):
            return
        self.route_steps[idx], self.route_steps[new_idx] = self.route_steps[new_idx], self.route_steps[idx]
        self._route_refresh_tree()
        try:
            self.route_tree.selection_set(str(new_idx))
        except Exception:
            pass

    def _route_add_from_position(self) -> None:
        pos = self._get_current_position()
        if pos is None:
            try:
                x = _to_int_or_none(self._simpledialog.askstring("X", "Coord X"))
                y = _to_int_or_none(self._simpledialog.askstring("Y", "Coord Y"))
                z = _to_int_or_none(self._simpledialog.askstring("Z", "Coord Z"))
                pos = (x, y, z)
            except Exception:
                pos = None
        if pos is None or pos[0] is None or pos[1] is None or pos[2] is None:
            self.route_status_var.set("Posicion no disponible")
            return
        self.route_kind_var.set("node")
        self.route_x_var.set(str(pos[0]))
        self.route_y_var.set(str(pos[1]))
        self.route_z_var.set(str(pos[2]))
        self._route_add_step()

    def _route_add_move(self) -> None:
        try:
            dir_token = self._simpledialog.askstring("MOVE", "Direccion (N/S/E/W)", initialvalue="N")
            steps = self._simpledialog.askstring("MOVE", "Pasos", initialvalue="1")
        except Exception:
            return
        if not dir_token or not steps:
            return
        s = self._WaypointStep(kind="move", params={"dir": dir_token.upper(), "steps": _to_int_or_none(steps) or 0})
        self.route_steps.append(s)
        self._route_refresh_tree()

    def _route_template_action(self, action: str) -> None:
        self.route_kind_var.set("action")
        self.route_name_var.set(action)
        self._route_add_step()

    def _route_load_setup(self) -> None:
        path = self.route_setup_path_var.get().strip()
        if not path:
            try:
                base = Path(self.route_path_var.get() or (self._repo_root / "routes"))
                path = self._filedialog.askopenfilename(title="Abrir setup_*.json", initialdir=str(base), filetypes=[("setup json", "*.json"), ("All", "*.*")])
            except Exception:
                path = ""
        if not path:
            return
        self.route_setup_path_var.set(path)
        cfg = self._load_setup(path)
        self.route_setup_cfg = cfg
        hc = cfg.hunt_config
        self.hc_mana_name.set(str(hc.get("mana_name", "")))
        self.hc_take_mana.set(int(hc.get("take_mana", 0) or 0))
        self.hc_mana_leave.set(int(hc.get("mana_leave", 0) or 0))
        self.hc_cap_leave.set(int(hc.get("cap_leave", 0) or 0))
        self._route_refresh_items_list()
        self.route_status_var.set(f"Setup cargado {path}")

    def _route_save_setup(self) -> None:
        cfg = self.route_setup_cfg
        if cfg is None:
            cfg = self._SetupConfig(raw={})
            self.route_setup_cfg = cfg
        cfg.set_hunt_field("mana_name", self.hc_mana_name.get())
        cfg.set_hunt_field("take_mana", int(self.hc_take_mana.get() or 0))
        cfg.set_hunt_field("mana_leave", int(self.hc_mana_leave.get() or 0))
        cfg.set_hunt_field("cap_leave", int(self.hc_cap_leave.get() or 0))
        # items already synced via list
        path = self.route_setup_path_var.get().strip()
        if not path:
            try:
                path = self._filedialog.asksaveasfilename(title="Guardar setup", defaultextension=".json", initialdir=str(self._repo_root / "routes"))
            except Exception:
                path = ""
        if not path:
            return
        try:
            self._save_setup(cfg, path)
            self.route_status_var.set(f"Setup guardado {path}")
        except Exception as e:
            self.route_status_var.set(f"Error guardando setup: {e}")

    def _route_refresh_items_list(self) -> None:
        lb = getattr(self, "route_items_list", None)
        if lb is None:
            return
        lb.delete(0, self._tk.END)
        cfg = self.route_setup_cfg
        if cfg is None:
            return
        for name in sorted(cfg.items.keys()):
            lb.insert(self._tk.END, name)

    def _route_on_item_select(self) -> None:
        lb = getattr(self, "route_items_list", None)
        if lb is None:
            return
        sel = lb.curselection()
        if not sel:
            return
        name = lb.get(sel[0])
        cfg = self.route_setup_cfg
        if cfg is None:
            return
        item = cfg.items.get(name, {})
        self.item_name_var.set(name)
        self.item_hotkey_var.set(str(item.get("hotkey", "")))
        self.item_use_var.set(str(item.get("use", "self")))

    def _route_add_update_item(self) -> None:
        name = self.item_name_var.get().strip()
        if not name:
            return
        hotkey = self.item_hotkey_var.get().strip()
        use = self.item_use_var.get().strip() or "self"
        cfg = self.route_setup_cfg
        if cfg is None:
            cfg = self._SetupConfig(raw={})
            self.route_setup_cfg = cfg
        cfg.set_item(name, hotkey, use)
        self._route_refresh_items_list()

    def _route_delete_item(self) -> None:
        if self.route_setup_cfg is None:
            return
        name = self.item_name_var.get().strip()
        if not name:
            return
        self.route_setup_cfg.delete_item(name)
        self._route_refresh_items_list()

    def _get_current_position(self):
        try:
            tel = self._config.telemetry_snapshot()
            if tel.pos_x is not None and tel.pos_y is not None and tel.pos_z is not None:
                return tel.pos_x, tel.pos_y, tel.pos_z
        except Exception:
            return None
        return None

    # ------------------------------------------------------------------
    # UI settings persistence
    # ------------------------------------------------------------------
    def _save_ui_settings(self) -> None:
        p = self._ui_settings_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        try:
            payload = {
                "version": 1,
                "overlay_preset": str(self.overlay_preset.get()),
                "telemetry_preset": str(self.telemetry_preset.get()),
                "overlay": {
                    "enabled": bool(self.overlay_enabled.get()),
                    "out_dir": str(self.overlay_out_dir.get()).strip() or "logs/debug_overlay",
                    "interval_s": float(self.overlay_interval_s.get()),
                    "tile_grid": bool(self.overlay_tile_grid.get()),
                    "tile_px": int(self.overlay_tile_px.get()),
                    "rois": str(self.overlay_rois.get()).strip(),
                },
                "healing": {
                    "enabled": bool(self.healing_enabled.get()),
                    "hp_below_pct": int(self.heal_hp_below_pct.get()),
                    "hp_recover_pct": int(self.heal_hp_recover_pct.get()),
                    "mp_below_pct": int(self.heal_mp_below_pct.get()),
                    "mp_recover_pct": int(self.heal_mp_recover_pct.get()),
                    "action": str(self.heal_action.get()),
                    "hp_action": str(self.heal_hp_action.get()),
                    "mp_action": str(self.heal_mp_action.get()),
                    "cooldown_s": float(self.heal_cooldown_s.get()),
                },
                "cavebot": {
                    "enabled": bool(self.cavebot_enabled.get()),
                    "route_path": str(self.cavebot_route_path.get()),
                    "mode": str(self.cavebot_mode.get()),
                    "coords_provider": str(self.coords_provider.get()),
                    "minimap_seed_x": str(self.minimap_seed_x.get()).strip(),
                    "minimap_seed_y": str(self.minimap_seed_y.get()).strip(),
                    "minimap_seed_z": str(self.minimap_seed_z.get()).strip(),
                    "minimap_fallback_mode": str(self.minimap_fallback_mode.get()).strip().lower(),
                    "minimap_fallback_conf_threshold": float(self.minimap_fallback_conf_threshold.get()),
                    "minimap_fallback_n_ticks": int(self.minimap_fallback_n_ticks.get()),
                    "loop": bool(self.cavebot_loop.get()),
                    "stop_on_low_cap": bool(self.cavebot_stop_on_low_cap.get()),
                    "cap_threshold": int(self.cavebot_cap_threshold.get()),
                    "stop_on_low_potions": bool(self.cavebot_stop_on_low_potions.get()),
                    "potions_remaining": int(self.cavebot_potions_remaining.get()),
                    "potions_min": int(self.cavebot_potions_min.get()),
                    "recovery_enabled": bool(self.cavebot_recovery_enabled.get()),
                    "recovery_idle_s": float(self.cavebot_recovery_idle_s.get()),
                    "recovery_max_attempts": int(self.cavebot_recovery_max_attempts.get()),
                    "recovery_stop_on_fail": bool(self.cavebot_recovery_stop_on_fail.get()),
                },
                "simulation": {
                    "enabled": bool(self.sim_enabled.get()),
                    "paralyzed": bool(self.sim_paralyzed.get()),
                    "haste_active": bool(self.sim_haste_active.get()),
                    "utamo_active": bool(self.sim_utamo_active.get()),
                    "hungry": bool(self.sim_hungry.get()),
                },
                "assistant": {
                    "enabled": bool(self.asst_enabled.get()),
                    "confirm_actions": bool(self.asst_confirm.get()),
                    "sound_alerts": bool(self.asst_sound.get()),
                    "input_mode": str(self.asst_input_mode.get()),
                    "target_hotkey": str(self.asst_target_hotkey.get()),
                    "minimap_hotkey": str(self.asst_minimap_hotkey.get()),
                    "live_input_armed": bool(self.asst_live_input_armed.get()),
                    "allowed_window_titles": [
                        s.strip() for s in str(self.asst_allowed_window_titles.get() or "").split(",") if s.strip()
                    ],
                },
                "replay": {
                    "enabled": bool(self.replay_enabled.get()),
                    "interval_ms": int(self.replay_interval_ms.get()),
                    "out_dir": str(self.replay_out_dir.get()),
                },
                "logging": {
                    "enabled": bool(self.log_enabled.get()),
                    "interval_ms": int(self.log_interval_ms.get()),
                    "out_file": str(self.log_out_file.get()),
                },
                "idle": {
                    "alert_s": float(self.idle_alert_s.get()),
                    "repeat_s": float(self.idle_repeat_s.get()),
                    "ui_fail_s": float(self.ui_idle_fail_s.get()),
                },
                "rois": {
                    "config_override": str(self.rois_config_override.get()).strip(),
                },
            }
        except Exception:
            return

        try:
            p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _apply_overlay_preset(self, name: str) -> None:
        n = (name or "").strip().lower()
        try:
            if n in {"custom", ""}:
                return

            if n in {"minimal"}:
                self.overlay_enabled.set(True)
                self.overlay_interval_s.set(1.0)
                self.overlay_out_dir.set("logs/debug_overlay")
                self.overlay_tile_grid.set(False)
                self.overlay_tile_px.set(32)
                self.overlay_rois.set("")
                return

            if n in {"debug hud", "debug"}:
                self.overlay_enabled.set(True)
                self.overlay_interval_s.set(1.0)
                self.overlay_out_dir.set("logs/debug_overlay")
                self.overlay_tile_grid.set(True)
                self.overlay_tile_px.set(32)
                self.overlay_rois.set(
                    "coords_ocr,minimap_content,hp_top_ocr,mp_top_ocr,hp_low_bar,mp_low_bar,states_icons,equipment_slots,battlelist_rows"
                )
                return

            if n in {"full hud", "full"}:
                self.overlay_enabled.set(True)
                self.overlay_interval_s.set(0.5)
                self.overlay_out_dir.set("logs/debug_overlay")
                self.overlay_tile_grid.set(True)
                self.overlay_tile_px.set(32)
                self.overlay_rois.set(
                    "coords_ocr,minimap_content,hpmp_top_strip,hp_top_ocr,mp_top_ocr,hpmp_low_panel,hp_low_bar,mp_low_bar,states_icons,equipment_slots,skills_panel,right_hud_panel,battlelist_rows,chat_panel"
                )
                return
        except Exception:
            pass

    def _apply_telemetry_preset(self, name: str) -> None:
        n = (name or "").strip().lower()
        try:
            if n in {"custom", ""}:
                return

            if n in {"off", "disabled"}:
                self.replay_enabled.set(False)
                self.log_enabled.set(False)
                return

            if n in {"debug"}:
                self.replay_enabled.set(True)
                self.replay_interval_ms.set(2000)
                self.replay_out_dir.set("logs/replay")
                self.log_enabled.set(True)
                self.log_interval_ms.set(250)
                self.log_out_file.set("logs/telemetry.jsonl")
                return

            if n in {"soak", "soak run"}:
                self.replay_enabled.set(True)
                self.replay_interval_ms.set(1500)
                self.replay_out_dir.set("logs/replay")
                self.log_enabled.set(True)
                self.log_interval_ms.set(250)
                self.log_out_file.set("logs/telemetry.jsonl")
                return

            if n in {"soak full", "soak_full", "soakfull"}:
                # Full soak = replay+jsonl + overlay configured.
                self.replay_enabled.set(True)
                self.replay_interval_ms.set(1500)
                self.replay_out_dir.set("logs/replay_soak")
                self.log_enabled.set(True)
                self.log_interval_ms.set(250)
                self.log_out_file.set("logs/telemetry.jsonl")
                try:
                    self.overlay_preset.set("Full HUD")
                    self._apply_overlay_preset("Full HUD")
                    # Keep soak overlay separate to simplify debugging.
                    self.overlay_out_dir.set("logs/debug_overlay_soak")
                    self.overlay_interval_s.set(0.5)
                except Exception:
                    pass
                return
        except Exception:
            pass

    def _reset_ui_defaults(self) -> None:
        # Best-effort reset; keep it conservative.
        try:
            # Healing
            self.healing_enabled.set(False)
            self.heal_hp_below_pct.set(70)
            self.heal_hp_recover_pct.set(80)
            self.heal_mp_below_pct.set(30)
            self.heal_mp_recover_pct.set(50)
            self.heal_action.set("")
            self.heal_hp_action.set("")
            self.heal_mp_action.set("")
            self.heal_cooldown_s.set(1.0)
        except Exception:
            pass

    def _find_latest_soak_dir(self, base_dir: str) -> Path | None:
        """Return the newest YYYYMMDD_HHMMSS subdir under base_dir, if any."""
        try:
            base = Path(base_dir)
            if not base.exists() or not base.is_dir():
                return None
            dirs = [p for p in base.iterdir() if p.is_dir()]
            if not dirs:
                return None
            # Prefer lexicographic order: timestamp format sorts correctly.
            dirs.sort(key=lambda p: p.name)
            return dirs[-1]
        except Exception:
            return None

    def _find_latest_soak_jsonl(self) -> Path | None:
        try:
            logs_dir = Path("logs")
            if not logs_dir.exists() or not logs_dir.is_dir():
                return None
            files = [p for p in logs_dir.glob("telemetry_soak_*.jsonl") if p.is_file()]
            if not files:
                return None
            files.sort(key=lambda p: p.name)
            return files[-1]
        except Exception:
            return None

        try:
            # Cavebot
            self.cavebot_enabled.set(False)
            self.cavebot_route_path.set("configs/route.json")
        except Exception:
            pass

        try:
            # Simulation
            self.sim_enabled.set(True)
            self.sim_paralyzed.set(False)
            self.sim_haste_active.set(False)
            self.sim_utamo_active.set(False)
            self.sim_hungry.set(False)
        except Exception:
            pass

        try:
            # Assistant
            self.asst_enabled.set(True)
            self.asst_confirm.set(True)
            self.asst_sound.set(True)
            self.asst_input_mode.set("log")
            self.asst_target_hotkey.set(os.getenv("TARGET_HOTKEY", "").strip())
            self.asst_minimap_hotkey.set(os.getenv("MINIMAP_CLICK_HOTKEY", "").strip())
            self.asst_live_input_armed.set(False)
            self.asst_allowed_window_titles.set("TibiaClone,MyClient,TibiaClone Harness")
        except Exception:
            pass

        try:
            # Replay/Logging
            self.telemetry_preset.set("Custom")
            self.replay_enabled.set(False)
            self.replay_interval_ms.set(2000)
            self.replay_out_dir.set(self._config.replay_snapshot().out_dir)
            self.log_enabled.set(False)
            self.log_interval_ms.set(250)
            self.log_out_file.set(self._config.logging_snapshot().out_file)
        except Exception:
            pass

        try:
            # Idle
            self.idle_alert_s.set(0.0)
            self.idle_repeat_s.set(10.0)
            self.ui_idle_fail_s.set(0.0)
        except Exception:
            pass

        try:
            # ROIs override
            self.rois_config_override.set("")
        except Exception:
            pass

        try:
            # Overlay
            self.overlay_preset.set("Custom")
            self.overlay_enabled.set(False)
            self.overlay_interval_s.set(1.0)
            self.overlay_out_dir.set("logs/debug_overlay")
            self.overlay_tile_grid.set(True)
            self.overlay_tile_px.set(32)
            self.overlay_rois.set(
                "coords_ocr,minimap_content,hp_top_ocr,mp_top_ocr,hp_low_bar,mp_low_bar,states_icons,equipment_slots,battlelist_rows"
            )
        except Exception:
            pass

    def start(self) -> None:
        if self._is_running():
            return

        # If running a soak preset, isolate outputs per session.
        try:
            tel_preset = str(getattr(self, "telemetry_preset", None).get()).strip().lower()  # type: ignore[union-attr]
        except Exception:
            tel_preset = ""
        if tel_preset in {"soak", "soak run", "soak full", "soak_full", "soakfull"}:
            try:
                run_id = time.strftime("%Y%m%d_%H%M%S")
                try:
                    self._last_soak_run_id = str(run_id)
                except Exception:
                    self._last_soak_run_id = None
                try:
                    if self.soak_run_id_var is not None:
                        self.soak_run_id_var.set(str(run_id))
                except Exception:
                    pass

                # Replay dir
                try:
                    base = str(self.replay_out_dir.get()).strip() or "logs/replay_soak"
                    self.replay_out_dir.set(str(Path(base) / run_id))
                except Exception:
                    pass

                # Overlay dir
                try:
                    base = str(self.overlay_out_dir.get()).strip() or "logs/debug_overlay_soak"
                    self.overlay_out_dir.set(str(Path(base) / run_id))
                except Exception:
                    pass

                # JSONL file (unique per run)
                try:
                    # Keep logs under logs/ by default.
                    self.log_out_file.set(str(Path("logs") / f"telemetry_soak_{run_id}.jsonl"))
                except Exception:
                    pass
            except Exception:
                pass
        else:
            # Not a soak run.
            try:
                self._last_soak_run_id = None
            except Exception:
                pass
            try:
                if self.soak_run_id_var is not None:
                    self.soak_run_id_var.set("-")
            except Exception:
                pass

        # Reset UI idle tracking for this run.
        self._reset_idle_ui()
        self._reset_cavebot_ui()

        # Apply cavebot mode + coords provider for this run.
        try:
            os.environ["CAVEBOT_MODE"] = str(self.cavebot_mode.get() or "").strip() or "steps"
        except Exception:
            pass
        try:
            os.environ["CAVEBOT_LOOP"] = "1" if bool(self.cavebot_loop.get()) else "0"
        except Exception:
            pass
        try:
            cp = str(self.coords_provider.get() or "").strip().lower()
            if not cp or cp == "auto":
                os.environ.pop("COORDS_PROVIDER", None)
            else:
                os.environ["COORDS_PROVIDER"] = cp
        except Exception:
            pass

        # Apply minimap calibration/fallback (only when provider is minimap).
        try:
            if str(self.coords_provider.get() or "").strip().lower() == "minimap":
                sx = str(self.minimap_seed_x.get() or "").strip()
                sy = str(self.minimap_seed_y.get() or "").strip()
                sz = str(self.minimap_seed_z.get() or "").strip()
                if sx:
                    os.environ["COORDS_SEED_X"] = sx
                else:
                    os.environ.pop("COORDS_SEED_X", None)
                if sy:
                    os.environ["COORDS_SEED_Y"] = sy
                else:
                    os.environ.pop("COORDS_SEED_Y", None)
                if sz:
                    os.environ["COORDS_SEED_Z"] = sz
                else:
                    os.environ.pop("COORDS_SEED_Z", None)

                mode = str(self.minimap_fallback_mode.get() or "steps").strip().lower()
                if mode not in {"steps", "ocr"}:
                    mode = "steps"
                os.environ["MINIMAP_FALLBACK_MODE"] = mode
                os.environ["MINIMAP_FALLBACK_CONF_THRESHOLD"] = str(float(self.minimap_fallback_conf_threshold.get()))
                os.environ["MINIMAP_FALLBACK_N_TICKS"] = str(max(1, int(self.minimap_fallback_n_ticks.get())))
            else:
                for k in [
                    "COORDS_SEED_X",
                    "COORDS_SEED_Y",
                    "COORDS_SEED_Z",
                    "MINIMAP_FALLBACK_MODE",
                    "MINIMAP_FALLBACK_CONF_THRESHOLD",
                    "MINIMAP_FALLBACK_N_TICKS",
                ]:
                    os.environ.pop(k, None)
        except Exception:
            pass

        # Apply cavebot stop conditions (assistant-only).
        # CAP_LEAVE_* already exists in core; POTIONS_* is a manual operator input.
        try:
            os.environ["CAP_LEAVE_ENABLED"] = "1" if bool(self.cavebot_stop_on_low_cap.get()) else "0"
            os.environ["CAP_LEAVE_THRESHOLD"] = str(max(0, int(self.cavebot_cap_threshold.get())))
        except Exception:
            pass
        try:
            os.environ["POTIONS_STOP_ENABLED"] = "1" if bool(self.cavebot_stop_on_low_potions.get()) else "0"
            os.environ["POTIONS_MIN"] = str(max(0, int(self.cavebot_potions_min.get())))
            os.environ["POTIONS_REMAINING"] = str(max(0, int(self.cavebot_potions_remaining.get())))
        except Exception:
            pass

        # Apply recovery policy (assistant-only).
        try:
            os.environ["ASSIST_RECOVERY_ENABLED"] = "1" if bool(self.cavebot_recovery_enabled.get()) else "0"
        except Exception:
            pass
        try:
            idle_s = float(self.cavebot_recovery_idle_s.get())
        except Exception:
            idle_s = 0.0
        try:
            if idle_s > 0.0:
                os.environ["ASSIST_RECOVERY_IDLE_S"] = str(float(idle_s))
            else:
                os.environ.pop("ASSIST_RECOVERY_IDLE_S", None)
        except Exception:
            pass
        try:
            os.environ["ASSIST_RECOVERY_MAX_ATTEMPTS"] = str(max(1, int(self.cavebot_recovery_max_attempts.get())))
        except Exception:
            pass
        try:
            os.environ["ASSIST_RECOVERY_STOP_ON_FAIL"] = "1" if bool(self.cavebot_recovery_stop_on_fail.get()) else "0"
        except Exception:
            pass

        # Apply ROIs override for this bot run (used by src/main.py:load_roi_config).
        try:
            rois_path = str(self.rois_config_override.get()).strip()
            if rois_path:
                os.environ["ROIS_CONFIG"] = rois_path
            else:
                os.environ.pop("ROIS_CONFIG", None)
        except Exception:
            pass

        # Apply idle-alert settings for this bot run (used by src/main.py decision loop, and UI idle line).
        try:
            idle_warn = float(self.idle_alert_s.get())
        except Exception:
            idle_warn = 0.0
        try:
            idle_rep = float(self.idle_repeat_s.get())
        except Exception:
            idle_rep = 10.0
        try:
            idle_fail = float(self.ui_idle_fail_s.get())
        except Exception:
            idle_fail = 0.0

        try:
            idle_warn = max(0.0, float(idle_warn))
            idle_rep = max(1.0, float(idle_rep))
            idle_fail = max(0.0, float(idle_fail))

            os.environ["ASSIST_IDLE_ALERT_S"] = str(idle_warn)
            os.environ["ASSIST_IDLE_REPEAT_S"] = str(idle_rep)

            # UI-specific thresholds (optional overrides)
            if idle_warn > 0.0:
                os.environ["UI_IDLE_WARN_S"] = str(idle_warn)
            else:
                os.environ.pop("UI_IDLE_WARN_S", None)

            if idle_fail > 0.0:
                os.environ["UI_IDLE_FAIL_S"] = str(idle_fail)
            else:
                os.environ.pop("UI_IDLE_FAIL_S", None)
        except Exception:
            pass

        # Apply overlay exporter settings for this bot run (used by src/main.py overlay exporter).
        try:
            ov_enabled = bool(self.overlay_enabled.get())
        except Exception:
            ov_enabled = False
        try:
            ov_out_dir = str(self.overlay_out_dir.get()).strip() or "logs/debug_overlay"
        except Exception:
            ov_out_dir = "logs/debug_overlay"
        try:
            ov_interval_s = float(self.overlay_interval_s.get())
        except Exception:
            ov_interval_s = 1.0
        try:
            ov_tile_grid = bool(self.overlay_tile_grid.get())
        except Exception:
            ov_tile_grid = False
        try:
            ov_tile_px = int(float(self.overlay_tile_px.get()))
        except Exception:
            ov_tile_px = 32
        try:
            ov_rois = str(self.overlay_rois.get()).strip()
        except Exception:
            ov_rois = ""

        try:
            if ov_enabled:
                os.environ["OVERLAY_ENABLED"] = "1"
                os.environ["OVERLAY_OUT_DIR"] = str(ov_out_dir)
                os.environ["OVERLAY_INTERVAL_S"] = str(max(0.1, float(ov_interval_s)))
                if ov_tile_grid:
                    os.environ["OVERLAY_TILE_PX"] = str(max(4, int(ov_tile_px)))
                else:
                    os.environ.pop("OVERLAY_TILE_PX", None)

                if ov_rois:
                    os.environ["OVERLAY_ROIS"] = ov_rois
                else:
                    os.environ.pop("OVERLAY_ROIS", None)
            else:
                os.environ.pop("OVERLAY_ENABLED", None)
                os.environ.pop("OVERLAY_OUT_DIR", None)
                os.environ.pop("OVERLAY_INTERVAL_S", None)
                os.environ.pop("OVERLAY_TILE_PX", None)
                os.environ.pop("OVERLAY_ROIS", None)
        except Exception:
            pass

        # Persist UI settings (best-effort) so the next UI open restores them.
        try:
            self._save_ui_settings()
        except Exception:
            pass

        self._stop_event = threading.Event()

        def _runner() -> None:
            try:
                self._run_bot(stop_event=self._stop_event, runtime_config=self._config)
            except Exception as e:
                # Si explota, reflejarlo en el UI
                self.status_var.set(f"Error: {e}")

        self._thread = threading.Thread(target=_runner, daemon=True)
        self._thread.start()

        self.status_var.set("Ejecutandose")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

    def stop(self) -> None:
        if not self._is_running():
            self.status_var.set("Detenido")
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self._reset_idle_ui()
            self._reset_cavebot_ui()
            return

        if self._stop_event is not None:
            self._stop_event.set()

        # Esperar un poco sin congelar totalmente la UI
        self.root.after(100, self._poll_stopped)

    def _poll_stopped(self) -> None:
        if self._is_running():
            self.root.after(100, self._poll_stopped)
            return

        self.status_var.set("Detenido")
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self._reset_idle_ui()
        self._reset_cavebot_ui()

    def on_close(self) -> None:
        self._allow_close = True
        self._stop_tray_icon()
        if self._is_running():
            if not self._messagebox.askyesno("Salir", "El bot esta corriendo. Quieres pararlo y salir?"):
                return
            self.stop()
            # Dar un pequeno margen antes de cerrar
            self.root.after(300, self.root.destroy)
            return

        self.root.destroy()

    def run(self) -> None:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            # Permite cerrar la UI desde consola sin traceback ruidoso
            try:
                if self._is_running() and self._stop_event is not None:
                    self._stop_event.set()
            except Exception:
                pass
            try:
                self.root.destroy()
            except Exception:
                pass


# -----------------------------
# Helpers (module-level)
# -----------------------------
def _to_int_or_none(val: str | None):
    try:
        if val is None:
            return None
        s = str(val).strip()
        if s == "":
            return None
        return int(float(s))
    except Exception:
        return None


def _parse_params(raw: str | None) -> dict:
    params: dict[str, str | int] = {}
    if not raw:
        return params
    try:
        tokens = [t.strip() for t in str(raw).split(",") if t.strip()]
        for tok in tokens:
            if "=" not in tok:
                continue
            k, v = tok.split("=", 1)
            k = k.strip()
            v = v.strip()
            if not k:
                continue
            iv = _to_int_or_none(v)
            params[k] = iv if iv is not None else v
    except Exception:
        return params
    return params


if __name__ == "__main__":
    BotUI().run()
