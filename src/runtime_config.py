from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time


def compute_injection_state(
    *,
    input_mode: str,
    driver_name: str,
    injection_enabled: bool,
    disabled_reason: str,
    live_input_armed: bool,
    target_window_active: bool,
    gating_enabled: bool,
    advance_pulse_pending: bool,
    has_injectable_action: bool,
) -> tuple[str, str]:
    """Compute assistant injection state + reason.

    This is UI/telemetry-facing only (assistant-first design).
    allow_live_autocommit: bool = False,
    It does NOT enable any OS injection; it only reports why actions won't run.
    """

    try:
        mode = str(input_mode or "").strip().lower() or "log"
    except Exception:
        mode = "log"
    try:
        drv = str(driver_name or "").strip() or ""
    except Exception:
        drv = ""
    try:
        dis = str(disabled_reason or "").strip()
    except Exception:
        dis = ""

    if dis:
        return "DISABLED", "fail_closed"

    if mode == "log":
        return "DISABLED", "input_mode=log"
    if mode == "mock":
        return "DISABLED", "input_mode=mock"
    if mode not in {"keyboard", "wininput", "bridge"}:
        return "DISABLED", f"input_mode={mode or 'log'}"

    # Live input is always opt-in (armed) and window-scoped.
    if not bool(live_input_armed):
        return "DISABLED", "not_armed"
    if not bool(injection_enabled):
        # Even if UI asked for keyboard, the runtime driver may still be mock.
        return "DISABLED", "driver=mock"
    if not bool(target_window_active):
        return "DISABLED", "wrong_window"

    if not bool(has_injectable_action):
        return "DISABLED", "no_action"

    if bool(advance_pulse_pending):
        return "ARMED", "advance_pulse_pending"

    # For safety, when live input is armed we require a human pulse even if
    # the UI toggled gating off.
    effective_gating = bool(gating_enabled) or bool(live_input_armed)
    if bool(effective_gating):
        return "WAITING_CONFIRM", "no_committed_pulse"
    return "DISABLED", "no_committed_pulse"


@dataclass
class HealingConfig:
    enabled: bool = False
    hp_below_pct: int = 70
    hp_recover_pct: int = 80
    mp_below_pct: int = 30
    mp_recover_pct: int = 50
    action: str = ""
    hp_action: str = ""
    mp_action: str = ""
    cooldown_s: float = 1.0


@dataclass
class CavebotConfig:
    enabled: bool = False
    route_path: str = "configs/route.json"
    mode: str = "pos"  # "pos"|"steps"
    force_steps: bool = False
    auto_steps_enabled: bool = True
    auto_steps_activate_level: str = "red"
    auto_steps_recover_level: str = "amber"
    auto_steps_activate_s: float = 1.5
    auto_steps_recover_s: float = 2.0


@dataclass
class SimulationConfig:
    """Overrides/simulación de señales cuando el GameState aún no las detecta."""

    enabled: bool = True
    paralyzed: bool = False
    haste_active: bool = False
    utamo_active: bool = False
    hungry: bool = False


@dataclass
class AssistantConfig:
    """Opciones del modo asistente (sin inputs automáticos)."""

    enabled: bool = True
    confirm_actions: bool = True
    sound_alerts: bool = True
    input_mode: str = "log"  # "log"|"mock"|"keyboard"|"bridge"
    # Double opt-in for OS input injection.
    live_input_armed: bool = False
    # Only inject if the foreground window title matches one of these.
    allowed_window_titles: list[str] = field(default_factory=lambda: ["Tibia"])
    target_hotkey: str = ""
    minimap_hotkey: str = ""


@dataclass
class AutoTargetConfig:
    enabled: bool = False
    follow_on_enable: bool = True
    follow_distance_tiles: int = 1
    retarget_if_lost_ms: int = 800
    retarget_if_hp_zero: bool = True
    prefer_nearest: bool = True
    whitelist: list[str] = field(default_factory=list)
    blacklist: list[str] = field(default_factory=list)
    # battlelist.py exposes a stabilized confidence (vote share), not raw OCR.
    # Default low so AutoTarget works out-of-the-box.
    min_confidence: float = 0.1
@dataclass
class BattlelistTargetingConfig:
    autotarget_enabled: bool = False
    battlelist_alive_threshold: float = 0.5
    dead_debounce_frames: int = 6
    select_debounce_frames: int = 3
    scroll_cooldown_ms: int = 800
    target_cooldown_ms: int = 500
    ocr_names: bool = False


@dataclass
class ReplayConfig:
    """Configuración para guardar replays (ROI crops + JSON) periódicamente."""

    enabled: bool = False
    interval_ms: int = 2000
    out_dir: str = "logs/replay"


@dataclass
class LoggingConfig:
    """Configuración para export de telemetría en JSONL."""

    enabled: bool = False
    interval_ms: int = 250
    out_file: str = "logs/telemetry.jsonl"


@dataclass
class TelemetrySnapshot:
    """Telemetría mínima para UI (solo lectura)."""

    ts: float = 0.0
    hp_current: int | None = None
    hp_max: int | None = None
    hp_pct: float | None = None
    mp_current: int | None = None
    mp_max: int | None = None
    mp_pct: float | None = None
    hp_method: str = ""  # ocr_top|top_strip|rf_box|bar_low|bar_low_fusion|none|cached
    hp_reason: str = ""  # ok|parse_fail|invalid_roi|ratio_mismatch|fallback_no_ocr|...
    mp_method: str = ""
    mp_reason: str = ""
    cap_current: int | None = None
    cap_method: str = ""  # roi|panel|cached|...
    cap_reason: str = ""  # roi_only|panel_only|panel_suffix|panel_diff|roi_default|...
    cap_roi: int | None = None
    cap_panel: int | None = None
    cap_panel_source: str = ""  # regex|bbox_row|""
    pos_x: int | None = None
    pos_y: int | None = None
    pos_z: int | None = None
    coords_provider: str = ""  # "ocr"|"minimap"|"file"|"env"|"disabled"|...
    # Coords quality
    coords_status: str = ""  # "OK"|"NO_COORDS"|"BAD_JUMP"|"UNSTABLE"|""
    coords_jump: int | None = None  # manhattan jump vs last coords
    coords_confidence: float | None = None  # provider-specific confidence (e.g., minimap)
    coords_provider_status: str = ""  # provider-specific status text
    coords_provider_state: dict | None = None  # structured provider status (minimap_motion)
    coords_confidence_level: str = ""  # "green"|"amber"|"red"|""
    # Minimap-motion (EXPERIMENTAL) debug
    minimap_mode_used: str = ""  # "scroll"|"marker"|""
    minimap_response: float | None = None
    minimap_delta_dx: float | None = None
    minimap_delta_dy: float | None = None
    minimap_acc_dx: float | None = None
    minimap_acc_dy: float | None = None
    minimap_marker_dpx_dx: float | None = None
    minimap_marker_dpx_dy: float | None = None
    ring_equipped: bool | None = None
    amulet_equipped: bool | None = None
    # Señales/estados
    low_hp: bool | None = None
    low_mp: bool | None = None
    low_cap: bool | None = None
    # Supplies (manual/operator-fed, assistant-only)
    potions_remaining: int | None = None
    potions_min: int | None = None
    low_potions: bool | None = None
    paralyzed: bool | None = None
    haste_active: bool | None = None
    utamo_active: bool | None = None
    hungry: bool | None = None
    # Battlelist (assistant-only observability)
    battlelist_n_rows: int | None = None
    battlelist_n_valid: int | None = None
    battlelist_top_names: list[str] | None = None
    battlelist_confidence: float | None = None
    battlelist_target_state: str = ""
    battlelist_target_row: int | None = None
    battlelist_alive_prob: float | None = None
    battlelist_selected_prob: float | None = None
    battlelist_target_reason: str = ""
    target: str = ""
    # AutoTarget (assistant-only)
    autotarget_enabled: bool | None = None
    autotarget_current_target: str = ""
    autotarget_follow_active: bool | None = None
    autotarget_last_retarget_reason: str = ""
    recommendation: str = ""
    cavebot_next: str = ""
    cavebot_waypoint: str = ""
    cavebot_action: str = ""
    cavebot_step_idx: int | None = None
    cavebot_step_next_idx: int | None = None
    cavebot_step_total: int | None = None
    # Cavebot gating (assistant-only): e.g. require:<expr> failed
    cavebot_blocked: bool | None = None
    cavebot_block_reason: str = ""
    # Cavebot termination / stop state (assistant-only)
    cavebot_finished: bool | None = None
    cavebot_finish_reason: str = ""  # e.g. "ROUTE_END|LOW_CAP|LOW_POTIONS"
    # Navigation observability (pos mode)
    nav_mode: str = ""  # "axis"|"astar"|""
    nav_blockers: int | None = None
    nav_astar_found: bool | None = None
    nav_astar_path_len: int | None = None
    nav_astar_visited: int | None = None
    # What the bot would do (assistant mode): serialized mock action(s)
    action_request: str = ""
    # Structured action list (assistant mode): avoids parsing the serialized string.
    # Each entry is expected to be JSON-serializable (kind/value/note/committed).
    action_requests: list[dict[str, object]] = field(default_factory=list)
    action_committed: bool = False
    # Which planner produced the action(s): "bt"|"fallback_exception"|"fallback_empty"|"fallback_disabled"|""
    action_source: str = ""
    # Log-only: planned inputs (keys/hotkeys/macros) derived from ActionRequest(s)
    input_plan: str = ""
    # Texto amigable opcional
    note: str = ""
    # Diagnóstico estructurado (sin inputs)
    stuck_reason: str = ""  # "STALE_GS"|"NO_COORDS"|"BLOCKED"|"MOVE_COMMITTED_NO_CHANGE"|"IDLE"|""
    stuck_idle_s: float | None = None
    stuck_blockers: int | None = None
    stuck_extra: str = ""

    # Input injection status (UI/JSONL observability)
    injection_state: str = ""  # "ARMED"|"DISABLED"|"WAITING_CONFIRM"
    injection_reason: str = ""  # wrong_window|not_armed|input_mode=log|fail_closed|no_committed_pulse|driver=mock

    # Client window discovery / monitoring (Win32)
    client_hwnd: int | None = None
    client_title: str = ""
    client_is_foreground: bool | None = None
    client_is_minimized: bool | None = None
    client_is_maximized: bool | None = None
    # Capture target window (Win32) + capture backend selection
    capture_backend: str = ""  # dxgi|obs_websocket|virtualcam|...
    capture_target: str = "auto"  # auto|obs_projector|client
    target_title: str = ""
    target_hwnd: int | None = None
    target_bounds: list[int] | None = None  # [l,t,r,b]
    target_found: bool | None = None
    target_reason: str = ""
    capture_state: str = ""  # window_crop|fullscreen|minimized|...
    capture_bounds: list[int] | None = None  # [l,t,r,b]
    input_block_reason: str = ""  # focus-guard reason or other block reason
    # Input Bridge observability (assistant-only)
    input_bridge_connected: bool | None = None
    input_bridge_rate_sent: int | None = None
    input_bridge_rate_accepted: int | None = None
    input_bridge_rate_rejected: int | None = None
    input_bridge_last_error: str = ""


@dataclass
class AssistantStatus:
    """Thread-safe assistant status for UI/overlay.

    Goal: make it obvious why an action does (or doesn't) execute.
    """

    ts: float = 0.0
    cavebot_mode: str = ""
    step_index: int | None = None
    total_steps: int | None = None
    current_label: str = ""
    next_step_text: str = ""
    injection_state: str = "DISABLED"  # "ARMED"|"WAITING_CONFIRM"|"DISABLED"
    injection_reason: str = ""
    foreground_title: str = ""  # Last observed foreground window title (InputManager)
    input_block_reason: str = ""  # Last InputManager block reason (preview_only|wrong_window|...)
    input_mode: str = ""  # UI-requested mode: "log"|"keyboard"|"wininput"|"bridge"
    driver_name: str = ""  # actual driver in use (runtime)
    gating_enabled: bool = False
    advance_pulse_pending: bool = False


@dataclass
class HealthSnapshot:
    """Salud del pipeline para UI/observabilidad (solo lectura)."""

    ts: float = 0.0
    uptime_s: float | None = None
    frame_age_s: float | None = None
    gs_age_s: float | None = None
    dead_threads: str = ""
    # Counters
    capture_ok: int | None = None
    capture_none: int | None = None
    vision_ok: int | None = None
    vision_ex: int | None = None
    decision_ok: int | None = None
    decision_ex: int | None = None
    drop_frame_queue: int | None = None
    drop_gs_queue: int | None = None
    drop_replay_queue: int | None = None
    drop_jsonl_queue: int | None = None
    # Queues
    q_frame: int | None = None
    q_gs: int | None = None
    # Timings
    capture_ms_last: float | None = None
    vision_ms_last: float | None = None
    decision_ms_last: float | None = None
    capture_latency_ms: float | None = None
    # ROI auto-alignment (AnchorTracker)
    roi_offset_dx_px: float | None = None
    roi_offset_dy_px: float | None = None
    roi_offset_score: float | None = None
    # Optional warning text
    warn: str = ""


@dataclass
class RuntimeConfig:
    healing: HealingConfig = field(default_factory=HealingConfig)
    cavebot: CavebotConfig = field(default_factory=CavebotConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    assistant: AssistantConfig = field(default_factory=AssistantConfig)
    autotarget: AutoTargetConfig = field(default_factory=AutoTargetConfig)
    battlelist_targeting: BattlelistTargetingConfig = field(default_factory=BattlelistTargetingConfig)
    replay: ReplayConfig = field(default_factory=ReplayConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    telemetry: TelemetrySnapshot = field(default_factory=TelemetrySnapshot)
    health: HealthSnapshot = field(default_factory=HealthSnapshot)
    assistant_status: AssistantStatus = field(default_factory=AssistantStatus)
    _advance_counter: int = field(default=0, init=False, repr=False)
    _replay_force_counter: int = field(default=0, init=False, repr=False)
    _step_jump_counter: int = field(default=0, init=False, repr=False)
    _step_jump_index: int = field(default=0, init=False, repr=False)
    _reseed_counter: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def snapshot(self) -> tuple[HealingConfig, CavebotConfig]:
        with self._lock:
            return (
                HealingConfig(
                    enabled=bool(self.healing.enabled),
                    hp_below_pct=int(self.healing.hp_below_pct),
                    hp_recover_pct=int(getattr(self.healing, "hp_recover_pct", self.healing.hp_below_pct)),
                    mp_below_pct=int(self.healing.mp_below_pct),
                    mp_recover_pct=int(getattr(self.healing, "mp_recover_pct", self.healing.mp_below_pct)),
                    action=str(self.healing.action),
                    hp_action=str(getattr(self.healing, "hp_action", "")),
                    mp_action=str(getattr(self.healing, "mp_action", "")),
                    cooldown_s=float(getattr(self.healing, "cooldown_s", 1.0)),
                ),
                CavebotConfig(
                    enabled=bool(self.cavebot.enabled),
                    route_path=str(self.cavebot.route_path),
                    mode=str(getattr(self.cavebot, "mode", "pos")),
                    force_steps=bool(getattr(self.cavebot, "force_steps", False)),
                    auto_steps_enabled=bool(getattr(self.cavebot, "auto_steps_enabled", True)),
                    auto_steps_activate_level=str(getattr(self.cavebot, "auto_steps_activate_level", "red")),
                    auto_steps_recover_level=str(getattr(self.cavebot, "auto_steps_recover_level", "amber")),
                    auto_steps_activate_s=float(getattr(self.cavebot, "auto_steps_activate_s", 1.5)),
                    auto_steps_recover_s=float(getattr(self.cavebot, "auto_steps_recover_s", 2.0)),
                ),
            )

    def simulation_snapshot(self) -> SimulationConfig:
        with self._lock:
            return SimulationConfig(
                enabled=bool(self.simulation.enabled),
                paralyzed=bool(self.simulation.paralyzed),
                haste_active=bool(self.simulation.haste_active),
                utamo_active=bool(self.simulation.utamo_active),
                hungry=bool(self.simulation.hungry),
            )

    def autotarget_snapshot(self) -> AutoTargetConfig:
        with self._lock:
            return AutoTargetConfig(
                enabled=bool(getattr(self.autotarget, "enabled", False)),
                follow_on_enable=bool(getattr(self.autotarget, "follow_on_enable", True)),
                follow_distance_tiles=int(getattr(self.autotarget, "follow_distance_tiles", 1) or 1),
                retarget_if_lost_ms=int(getattr(self.autotarget, "retarget_if_lost_ms", 800) or 800),
                retarget_if_hp_zero=bool(getattr(self.autotarget, "retarget_if_hp_zero", True)),
                prefer_nearest=bool(getattr(self.autotarget, "prefer_nearest", True)),
                whitelist=list(getattr(self.autotarget, "whitelist", None) or []),
                blacklist=list(getattr(self.autotarget, "blacklist", None) or []),
                min_confidence=float(getattr(self.autotarget, "min_confidence", 0.1) or 0.1),
            )

    def battlelist_targeting_snapshot(self) -> BattlelistTargetingConfig:
        with self._lock:
            return BattlelistTargetingConfig(
                autotarget_enabled=bool(getattr(self.battlelist_targeting, "autotarget_enabled", False)),
                battlelist_alive_threshold=float(getattr(self.battlelist_targeting, "battlelist_alive_threshold", 0.5)),
                dead_debounce_frames=int(getattr(self.battlelist_targeting, "dead_debounce_frames", 6)),
                select_debounce_frames=int(getattr(self.battlelist_targeting, "select_debounce_frames", 3)),
                scroll_cooldown_ms=int(getattr(self.battlelist_targeting, "scroll_cooldown_ms", 800)),
                target_cooldown_ms=int(getattr(self.battlelist_targeting, "target_cooldown_ms", 500)),
                ocr_names=bool(getattr(self.battlelist_targeting, "ocr_names", False)),
            )

    def telemetry_snapshot(self) -> TelemetrySnapshot:
        with self._lock:
            cps = None
            try:
                if isinstance(getattr(self.telemetry, "coords_provider_state", None), dict):
                    cps = dict(getattr(self.telemetry, "coords_provider_state") or {})
            except Exception:
                cps = None

            ars: list[dict[str, object]] = []
            try:
                raw = getattr(self.telemetry, "action_requests", None)
                if isinstance(raw, list):
                    ars = [dict(x) for x in raw if isinstance(x, dict)]
            except Exception:
                ars = []
            return TelemetrySnapshot(
                ts=float(self.telemetry.ts),
                hp_current=self.telemetry.hp_current,
                hp_max=self.telemetry.hp_max,
                hp_pct=self.telemetry.hp_pct,
                mp_current=self.telemetry.mp_current,
                mp_max=self.telemetry.mp_max,
                mp_pct=self.telemetry.mp_pct,
                cap_current=self.telemetry.cap_current,
                pos_x=self.telemetry.pos_x,
                pos_y=self.telemetry.pos_y,
                pos_z=self.telemetry.pos_z,
                coords_provider=str(getattr(self.telemetry, "coords_provider", "")),
                coords_status=str(getattr(self.telemetry, "coords_status", "")),
                coords_jump=getattr(self.telemetry, "coords_jump", None),
                coords_confidence=getattr(self.telemetry, "coords_confidence", None),
                coords_provider_status=str(getattr(self.telemetry, "coords_provider_status", "")),
                coords_provider_state=cps,
                coords_confidence_level=str(getattr(self.telemetry, "coords_confidence_level", "")),
                minimap_mode_used=str(getattr(self.telemetry, "minimap_mode_used", "")),
                minimap_response=getattr(self.telemetry, "minimap_response", None),
                minimap_delta_dx=getattr(self.telemetry, "minimap_delta_dx", None),
                minimap_delta_dy=getattr(self.telemetry, "minimap_delta_dy", None),
                minimap_acc_dx=getattr(self.telemetry, "minimap_acc_dx", None),
                minimap_acc_dy=getattr(self.telemetry, "minimap_acc_dy", None),
                minimap_marker_dpx_dx=getattr(self.telemetry, "minimap_marker_dpx_dx", None),
                minimap_marker_dpx_dy=getattr(self.telemetry, "minimap_marker_dpx_dy", None),
                ring_equipped=self.telemetry.ring_equipped,
                amulet_equipped=self.telemetry.amulet_equipped,
                low_hp=self.telemetry.low_hp,
                low_mp=self.telemetry.low_mp,
                low_cap=self.telemetry.low_cap,
                potions_remaining=getattr(self.telemetry, "potions_remaining", None),
                potions_min=getattr(self.telemetry, "potions_min", None),
                low_potions=getattr(self.telemetry, "low_potions", None),
                paralyzed=self.telemetry.paralyzed,
                haste_active=self.telemetry.haste_active,
                utamo_active=self.telemetry.utamo_active,
                hungry=self.telemetry.hungry,
                battlelist_n_rows=getattr(self.telemetry, "battlelist_n_rows", None),
                battlelist_n_valid=getattr(self.telemetry, "battlelist_n_valid", None),
                battlelist_top_names=list(getattr(self.telemetry, "battlelist_top_names", None) or [])
                if getattr(self.telemetry, "battlelist_top_names", None) is not None
                else None,
                battlelist_confidence=getattr(self.telemetry, "battlelist_confidence", None),
                battlelist_target_state=str(getattr(self.telemetry, "battlelist_target_state", "") or ""),
                battlelist_target_row=getattr(self.telemetry, "battlelist_target_row", None),
                battlelist_alive_prob=getattr(self.telemetry, "battlelist_alive_prob", None),
                battlelist_selected_prob=getattr(self.telemetry, "battlelist_selected_prob", None),
                battlelist_target_reason=str(getattr(self.telemetry, "battlelist_target_reason", "") or ""),
                target=str(self.telemetry.target),
                autotarget_enabled=getattr(self.telemetry, "autotarget_enabled", None),
                autotarget_current_target=str(getattr(self.telemetry, "autotarget_current_target", "") or ""),
                autotarget_follow_active=getattr(self.telemetry, "autotarget_follow_active", None),
                autotarget_last_retarget_reason=str(getattr(self.telemetry, "autotarget_last_retarget_reason", "") or ""),
                recommendation=str(self.telemetry.recommendation),
                cavebot_next=str(self.telemetry.cavebot_next),
                cavebot_waypoint=str(self.telemetry.cavebot_waypoint),
                cavebot_action=str(self.telemetry.cavebot_action),
                cavebot_step_idx=getattr(self.telemetry, "cavebot_step_idx", None),
                cavebot_step_next_idx=getattr(self.telemetry, "cavebot_step_next_idx", None),
                cavebot_step_total=getattr(self.telemetry, "cavebot_step_total", None),
                cavebot_blocked=getattr(self.telemetry, "cavebot_blocked", None),
                cavebot_block_reason=str(getattr(self.telemetry, "cavebot_block_reason", "")),
                cavebot_finished=getattr(self.telemetry, "cavebot_finished", None),
                cavebot_finish_reason=str(getattr(self.telemetry, "cavebot_finish_reason", "")),
                nav_mode=str(getattr(self.telemetry, "nav_mode", "")),
                nav_blockers=getattr(self.telemetry, "nav_blockers", None),
                nav_astar_found=getattr(self.telemetry, "nav_astar_found", None),
                nav_astar_path_len=getattr(self.telemetry, "nav_astar_path_len", None),
                nav_astar_visited=getattr(self.telemetry, "nav_astar_visited", None),
                action_request=str(self.telemetry.action_request),
                action_requests=ars,
                action_committed=bool(self.telemetry.action_committed),
                action_source=str(getattr(self.telemetry, "action_source", "")),
                input_plan=str(getattr(self.telemetry, "input_plan", "")),
                note=str(self.telemetry.note),
                stuck_reason=str(getattr(self.telemetry, "stuck_reason", "")),
                stuck_idle_s=getattr(self.telemetry, "stuck_idle_s", None),
                stuck_blockers=getattr(self.telemetry, "stuck_blockers", None),
                stuck_extra=str(getattr(self.telemetry, "stuck_extra", "")),
                injection_state=str(getattr(self.telemetry, "injection_state", "") or ""),
                injection_reason=str(getattr(self.telemetry, "injection_reason", "") or ""),
            )

    def health_snapshot(self) -> HealthSnapshot:
        with self._lock:
            return HealthSnapshot(
                ts=float(self.health.ts),
                uptime_s=self.health.uptime_s,
                frame_age_s=self.health.frame_age_s,
                gs_age_s=self.health.gs_age_s,
                dead_threads=str(self.health.dead_threads),
                capture_ok=self.health.capture_ok,
                capture_none=self.health.capture_none,
                vision_ok=self.health.vision_ok,
                vision_ex=self.health.vision_ex,
                decision_ok=self.health.decision_ok,
                decision_ex=self.health.decision_ex,
                drop_frame_queue=self.health.drop_frame_queue,
                drop_gs_queue=self.health.drop_gs_queue,
                drop_replay_queue=self.health.drop_replay_queue,
                drop_jsonl_queue=self.health.drop_jsonl_queue,
                q_frame=self.health.q_frame,
                q_gs=self.health.q_gs,
                capture_ms_last=self.health.capture_ms_last,
                vision_ms_last=self.health.vision_ms_last,
                decision_ms_last=self.health.decision_ms_last,
                capture_latency_ms=self.health.capture_latency_ms,
                warn=str(self.health.warn),
            )

    def assistant_status_snapshot(self) -> AssistantStatus:
        with self._lock:
            return AssistantStatus(
                ts=float(getattr(self.assistant_status, "ts", 0.0) or 0.0),
                cavebot_mode=str(getattr(self.assistant_status, "cavebot_mode", "") or ""),
                step_index=getattr(self.assistant_status, "step_index", None),
                total_steps=getattr(self.assistant_status, "total_steps", None),
                current_label=str(getattr(self.assistant_status, "current_label", "") or ""),
                next_step_text=str(getattr(self.assistant_status, "next_step_text", "") or ""),
                injection_state=str(getattr(self.assistant_status, "injection_state", "DISABLED") or "DISABLED"),
                injection_reason=str(getattr(self.assistant_status, "injection_reason", "") or ""),
                foreground_title=str(getattr(self.assistant_status, "foreground_title", "") or ""),
                input_block_reason=str(getattr(self.assistant_status, "input_block_reason", "") or ""),
                input_mode=str(getattr(self.assistant_status, "input_mode", "") or ""),
                driver_name=str(getattr(self.assistant_status, "driver_name", "") or ""),
                gating_enabled=bool(getattr(self.assistant_status, "gating_enabled", False)),
                advance_pulse_pending=bool(getattr(self.assistant_status, "advance_pulse_pending", False)),
            )

    def update_assistant_status(
        self,
        *,
        cavebot_mode: str | None = None,
        step_index: int | None = None,
        total_steps: int | None = None,
        current_label: str | None = None,
        next_step_text: str | None = None,
        injection_state: str | None = None,
        injection_reason: str | None = None,
        foreground_title: str | None = None,
        input_block_reason: str | None = None,
        input_mode: str | None = None,
        driver_name: str | None = None,
        gating_enabled: bool | None = None,
        advance_pulse_pending: bool | None = None,
    ) -> None:
        with self._lock:
            self.assistant_status.ts = float(time.time())
            if cavebot_mode is not None:
                self.assistant_status.cavebot_mode = str(cavebot_mode)
            self.assistant_status.step_index = step_index
            self.assistant_status.total_steps = total_steps
            if current_label is not None:
                self.assistant_status.current_label = str(current_label)
            if next_step_text is not None:
                self.assistant_status.next_step_text = str(next_step_text)
            if injection_state is not None:
                self.assistant_status.injection_state = str(injection_state)
            if injection_reason is not None:
                self.assistant_status.injection_reason = str(injection_reason)
            if foreground_title is not None:
                self.assistant_status.foreground_title = str(foreground_title)
            if input_block_reason is not None:
                self.assistant_status.input_block_reason = str(input_block_reason)
            if input_mode is not None:
                self.assistant_status.input_mode = str(input_mode)
            if driver_name is not None:
                self.assistant_status.driver_name = str(driver_name)
            if gating_enabled is not None:
                self.assistant_status.gating_enabled = bool(gating_enabled)
            if advance_pulse_pending is not None:
                self.assistant_status.advance_pulse_pending = bool(advance_pulse_pending)

    def assistant_snapshot(self) -> AssistantConfig:
        with self._lock:
            return AssistantConfig(
                enabled=bool(self.assistant.enabled),
                confirm_actions=bool(self.assistant.confirm_actions),
                sound_alerts=bool(self.assistant.sound_alerts),
                input_mode=str(getattr(self.assistant, "input_mode", "log") or "log"),
                live_input_armed=bool(getattr(self.assistant, "live_input_armed", False)),
                allowed_window_titles=list(getattr(self.assistant, "allowed_window_titles", None) or ["TibiaClone", "MyClient"]),
                target_hotkey=str(getattr(self.assistant, "target_hotkey", "")),
                minimap_hotkey=str(getattr(self.assistant, "minimap_hotkey", "")),
            )

    def replay_snapshot(self) -> ReplayConfig:
        with self._lock:
            return ReplayConfig(
                enabled=bool(self.replay.enabled),
                interval_ms=int(self.replay.interval_ms),
                out_dir=str(self.replay.out_dir),
            )

    def logging_snapshot(self) -> LoggingConfig:
        with self._lock:
            return LoggingConfig(
                enabled=bool(self.logging.enabled),
                interval_ms=int(self.logging.interval_ms),
                out_file=str(self.logging.out_file),
            )

    def update_healing(
        self,
        *,
        enabled: bool | None = None,
        hp_below_pct: int | None = None,
        hp_recover_pct: int | None = None,
        mp_below_pct: int | None = None,
        mp_recover_pct: int | None = None,
        action: str | None = None,
        hp_action: str | None = None,
        mp_action: str | None = None,
        cooldown_s: float | None = None,
    ) -> None:
        with self._lock:
            if enabled is not None:
                self.healing.enabled = bool(enabled)
            if hp_below_pct is not None:
                self.healing.hp_below_pct = int(hp_below_pct)
            if hp_recover_pct is not None:
                self.healing.hp_recover_pct = int(hp_recover_pct)
            if mp_below_pct is not None:
                self.healing.mp_below_pct = int(mp_below_pct)
            if mp_recover_pct is not None:
                self.healing.mp_recover_pct = int(mp_recover_pct)
            if action is not None:
                self.healing.action = str(action)
            if hp_action is not None:
                self.healing.hp_action = str(hp_action)
            if mp_action is not None:
                self.healing.mp_action = str(mp_action)
            if cooldown_s is not None:
                self.healing.cooldown_s = float(cooldown_s)

    def update_cavebot(
        self,
        *,
        enabled: bool | None = None,
        route_path: str | None = None,
        mode: str | None = None,
        force_steps: bool | None = None,
        auto_steps_enabled: bool | None = None,
        auto_steps_activate_level: str | None = None,
        auto_steps_recover_level: str | None = None,
        auto_steps_activate_s: float | None = None,
        auto_steps_recover_s: float | None = None,
    ) -> None:
        with self._lock:
            if enabled is not None:
                self.cavebot.enabled = bool(enabled)
            if route_path is not None:
                self.cavebot.route_path = str(route_path)
            if mode is not None:
                self.cavebot.mode = str(mode)
            if force_steps is not None:
                self.cavebot.force_steps = bool(force_steps)
            if auto_steps_enabled is not None:
                self.cavebot.auto_steps_enabled = bool(auto_steps_enabled)
            if auto_steps_activate_level is not None:
                self.cavebot.auto_steps_activate_level = str(auto_steps_activate_level)
            if auto_steps_recover_level is not None:
                self.cavebot.auto_steps_recover_level = str(auto_steps_recover_level)
            if auto_steps_activate_s is not None:
                self.cavebot.auto_steps_activate_s = float(auto_steps_activate_s)
            if auto_steps_recover_s is not None:
                self.cavebot.auto_steps_recover_s = float(auto_steps_recover_s)

    def update_simulation(
        self,
        *,
        enabled: bool | None = None,
        paralyzed: bool | None = None,
        haste_active: bool | None = None,
        utamo_active: bool | None = None,
        hungry: bool | None = None,
    ) -> None:
        with self._lock:
            if enabled is not None:
                self.simulation.enabled = bool(enabled)
            if paralyzed is not None:
                self.simulation.paralyzed = bool(paralyzed)
            if haste_active is not None:
                self.simulation.haste_active = bool(haste_active)
            if utamo_active is not None:
                self.simulation.utamo_active = bool(utamo_active)
            if hungry is not None:
                self.simulation.hungry = bool(hungry)

    def update_assistant(
        self,
        *,
        enabled: bool | None = None,
        confirm_actions: bool | None = None,
        sound_alerts: bool | None = None,
        input_mode: str | None = None,
        live_input_armed: bool | None = None,
        allowed_window_titles: list[str] | None = None,
        target_hotkey: str | None = None,
        minimap_hotkey: str | None = None,
    ) -> None:
        with self._lock:
            if enabled is not None:
                self.assistant.enabled = bool(enabled)
            if confirm_actions is not None:
                self.assistant.confirm_actions = bool(confirm_actions)
            if sound_alerts is not None:
                self.assistant.sound_alerts = bool(sound_alerts)
            if input_mode is not None:
                mode = str(input_mode).strip().lower()
                if mode == "wininput":
                    mode = "keyboard"
                if mode not in {"keyboard", "mock", "log", "bridge"}:
                    mode = "log"
                self.assistant.input_mode = mode
            if live_input_armed is not None:
                self.assistant.live_input_armed = bool(live_input_armed)
            if allowed_window_titles is not None:
                # Normalize + keep order.
                out: list[str] = []
                for t in list(allowed_window_titles or []):
                    s = str(t).strip()
                    if not s:
                        continue
                    out.append(s)
                self.assistant.allowed_window_titles = out
            if target_hotkey is not None:
                self.assistant.target_hotkey = str(target_hotkey)
            if minimap_hotkey is not None:
                self.assistant.minimap_hotkey = str(minimap_hotkey)

    def update_autotarget(
        self,
        *,
        enabled: bool | None = None,
        follow_on_enable: bool | None = None,
        follow_distance_tiles: int | None = None,
        retarget_if_lost_ms: int | None = None,
        whitelist: list[str] | None = None,
        blacklist: list[str] | None = None,
        min_confidence: float | None = None,
    ) -> None:
        with self._lock:
            if enabled is not None:
                self.autotarget.enabled = bool(enabled)
            if follow_on_enable is not None:
                self.autotarget.follow_on_enable = bool(follow_on_enable)
            if follow_distance_tiles is not None:
                self.autotarget.follow_distance_tiles = max(0, int(follow_distance_tiles))
            if retarget_if_lost_ms is not None:
                self.autotarget.retarget_if_lost_ms = max(50, int(retarget_if_lost_ms))
            if whitelist is not None:
                out: list[str] = []
                for s in list(whitelist or []):
                    t = str(s).strip()
                    if t:
                        out.append(t)
                self.autotarget.whitelist = out
            if blacklist is not None:
                out2: list[str] = []
                for s in list(blacklist or []):
                    t = str(s).strip()
                    if t:
                        out2.append(t)
                self.autotarget.blacklist = out2
            if min_confidence is not None:
                try:
                    self.autotarget.min_confidence = float(min_confidence)
                except Exception:
                    pass

    def update_battlelist_targeting(
        self,
        *,
        autotarget_enabled: bool | None = None,
        battlelist_alive_threshold: float | None = None,
        dead_debounce_frames: int | None = None,
        select_debounce_frames: int | None = None,
        scroll_cooldown_ms: int | None = None,
        target_cooldown_ms: int | None = None,
        ocr_names: bool | None = None,
    ) -> None:
        with self._lock:
            if autotarget_enabled is not None:
                self.battlelist_targeting.autotarget_enabled = bool(autotarget_enabled)
            if battlelist_alive_threshold is not None:
                self.battlelist_targeting.battlelist_alive_threshold = float(battlelist_alive_threshold)
            if dead_debounce_frames is not None:
                self.battlelist_targeting.dead_debounce_frames = int(dead_debounce_frames)
            if select_debounce_frames is not None:
                self.battlelist_targeting.select_debounce_frames = int(select_debounce_frames)
            if scroll_cooldown_ms is not None:
                self.battlelist_targeting.scroll_cooldown_ms = int(scroll_cooldown_ms)
            if target_cooldown_ms is not None:
                self.battlelist_targeting.target_cooldown_ms = int(target_cooldown_ms)
            if ocr_names is not None:
                self.battlelist_targeting.ocr_names = bool(ocr_names)

    def update_replay(
        self,
        *,
        enabled: bool | None = None,
        interval_ms: int | None = None,
        out_dir: str | None = None,
    ) -> None:
        with self._lock:
            if enabled is not None:
                self.replay.enabled = bool(enabled)
            if interval_ms is not None:
                self.replay.interval_ms = max(50, int(interval_ms))
            if out_dir is not None:
                self.replay.out_dir = str(out_dir)

    def request_advance(self) -> int:
        """El usuario confirmó que se puede avanzar una acción recomendada."""
        with self._lock:
            self._advance_counter += 1
            return int(self._advance_counter)

    def advance_counter_snapshot(self) -> int:
        with self._lock:
            return int(self._advance_counter)

    def request_step_jump(self, idx: int) -> int:
        """Solicita saltar el StepNavigator a un índice específico.

        Nota: esto solo mueve el puntero/preview del cavebot (only-logs). No ejecuta inputs.
        """
        with self._lock:
            try:
                self._step_jump_index = max(0, int(idx))
            except Exception:
                self._step_jump_index = 0
            self._step_jump_counter += 1
            return int(self._step_jump_counter)

    def step_jump_snapshot(self) -> tuple[int, int]:
        """Devuelve (counter, index) para detectar cambios cross-thread."""
        with self._lock:
            return (int(self._step_jump_counter), int(self._step_jump_index))

    def request_replay_snapshot(self) -> int:
        """Solicita forzar un snapshot de replay en el próximo frame disponible."""
        with self._lock:
            self._replay_force_counter += 1
            return int(self._replay_force_counter)

    def request_reseed_minimap(self) -> int:
        """Solicita un reseed/realineación del provider de coords (minimap)."""
        with self._lock:
            self._reseed_counter += 1
            return int(self._reseed_counter)

    def reseed_counter_snapshot(self) -> int:
        with self._lock:
            return int(self._reseed_counter)

    def replay_force_counter_snapshot(self) -> int:
        with self._lock:
            return int(self._replay_force_counter)

    def update_logging(
        self,
        *,
        enabled: bool | None = None,
        interval_ms: int | None = None,
        out_file: str | None = None,
    ) -> None:
        with self._lock:
            if enabled is not None:
                self.logging.enabled = bool(enabled)
            if interval_ms is not None:
                self.logging.interval_ms = max(50, int(interval_ms))
            if out_file is not None:
                self.logging.out_file = str(out_file)

    def update_telemetry(
        self,
        *,
        hp_current: int | None = None,
        hp_max: int | None = None,
        hp_pct: float | None = None,
        mp_current: int | None = None,
        mp_max: int | None = None,
        mp_pct: float | None = None,
        hp_method: str | None = None,
        hp_reason: str | None = None,
        mp_method: str | None = None,
        mp_reason: str | None = None,
        cap_current: int | None = None,
        cap_method: str | None = None,
        cap_reason: str | None = None,
        cap_roi: int | None = None,
        cap_panel: int | None = None,
        cap_panel_source: str | None = None,
        pos_x: int | None = None,
        pos_y: int | None = None,
        pos_z: int | None = None,
        coords_provider: str | None = None,
        coords_status: str | None = None,
        coords_jump: int | None = None,
        coords_confidence: float | None = None,
        coords_provider_status: str | None = None,
        coords_provider_state: dict | None = None,
        coords_confidence_level: str | None = None,
        minimap_mode_used: str | None = None,
        minimap_response: float | None = None,
        minimap_delta_dx: float | None = None,
        minimap_delta_dy: float | None = None,
        minimap_acc_dx: float | None = None,
        minimap_acc_dy: float | None = None,
        minimap_marker_dpx_dx: float | None = None,
        minimap_marker_dpx_dy: float | None = None,
        ring_equipped: bool | None = None,
        amulet_equipped: bool | None = None,
        low_hp: bool | None = None,
        low_mp: bool | None = None,
        low_cap: bool | None = None,
        potions_remaining: int | None = None,
        potions_min: int | None = None,
        low_potions: bool | None = None,
        paralyzed: bool | None = None,
        haste_active: bool | None = None,
        utamo_active: bool | None = None,
        hungry: bool | None = None,
        battlelist_n_rows: int | None = None,
        battlelist_n_valid: int | None = None,
        battlelist_top_names: list[str] | None = None,
        battlelist_confidence: float | None = None,
        battlelist_target_state: str | None = None,
        battlelist_target_row: int | None = None,
        battlelist_alive_prob: float | None = None,
        battlelist_selected_prob: float | None = None,
        battlelist_target_reason: str | None = None,
        target: str | None = None,
        autotarget_enabled: bool | None = None,
        autotarget_current_target: str | None = None,
        autotarget_follow_active: bool | None = None,
        autotarget_last_retarget_reason: str | None = None,
        recommendation: str | None = None,
        cavebot_next: str | None = None,
        cavebot_waypoint: str | None = None,
        cavebot_action: str | None = None,
        cavebot_step_idx: int | None = None,
        cavebot_step_next_idx: int | None = None,
        cavebot_step_total: int | None = None,
        cavebot_blocked: bool | None = None,
        cavebot_block_reason: str | None = None,
        cavebot_finished: bool | None = None,
        cavebot_finish_reason: str | None = None,
        nav_mode: str | None = None,
        nav_blockers: int | None = None,
        nav_astar_found: bool | None = None,
        nav_astar_path_len: int | None = None,
        nav_astar_visited: int | None = None,
        action_request: str | None = None,
        action_requests: list[dict[str, object]] | None = None,
        action_committed: bool | None = None,
        action_source: str | None = None,
        input_plan: str | None = None,
        note: str | None = None,
        stuck_reason: str | None = None,
        stuck_idle_s: float | None = None,
        stuck_blockers: int | None = None,
        stuck_extra: str | None = None,
        injection_state: str | None = None,
        injection_reason: str | None = None,
        client_hwnd: int | None = None,
        client_title: str | None = None,
        client_is_foreground: bool | None = None,
        client_is_minimized: bool | None = None,
        client_is_maximized: bool | None = None,
        capture_state: str | None = None,
        capture_bounds: list[int] | None = None,
        capture_backend: str | None = None,
        capture_target: str | None = None,
        target_title: str | None = None,
        target_hwnd: int | None = None,
        target_bounds: list[int] | None = None,
        target_found: bool | None = None,
        target_reason: str | None = None,
        input_block_reason: str | None = None,
        input_bridge_connected: bool | None = None,
        input_bridge_rate_sent: int | None = None,
        input_bridge_rate_accepted: int | None = None,
        input_bridge_rate_rejected: int | None = None,
        input_bridge_last_error: str | None = None,
    ) -> None:
        with self._lock:
            self.telemetry.ts = time.time()
            if hp_current is not None:
                self.telemetry.hp_current = int(hp_current)
            if hp_max is not None:
                self.telemetry.hp_max = int(hp_max)
            if hp_pct is not None:
                self.telemetry.hp_pct = float(hp_pct)
            if mp_current is not None:
                self.telemetry.mp_current = int(mp_current)
            if mp_max is not None:
                self.telemetry.mp_max = int(mp_max)
            if mp_pct is not None:
                self.telemetry.mp_pct = float(mp_pct)
            if hp_method is not None:
                self.telemetry.hp_method = str(hp_method)
            if hp_reason is not None:
                self.telemetry.hp_reason = str(hp_reason)
            if mp_method is not None:
                self.telemetry.mp_method = str(mp_method)
            if mp_reason is not None:
                self.telemetry.mp_reason = str(mp_reason)
            if cap_current is not None:
                self.telemetry.cap_current = int(cap_current)
            if cap_method is not None:
                self.telemetry.cap_method = str(cap_method)
            if cap_reason is not None:
                self.telemetry.cap_reason = str(cap_reason)
            if cap_roi is not None:
                try:
                    self.telemetry.cap_roi = int(cap_roi)
                except Exception:
                    self.telemetry.cap_roi = None
            if cap_panel is not None:
                try:
                    self.telemetry.cap_panel = int(cap_panel)
                except Exception:
                    self.telemetry.cap_panel = None
            if cap_panel_source is not None:
                self.telemetry.cap_panel_source = str(cap_panel_source)
            if pos_x is not None:
                self.telemetry.pos_x = int(pos_x)
            if pos_y is not None:
                self.telemetry.pos_y = int(pos_y)
            if pos_z is not None:
                self.telemetry.pos_z = int(pos_z)
            if coords_provider is not None:
                self.telemetry.coords_provider = str(coords_provider)
            if coords_status is not None:
                self.telemetry.coords_status = str(coords_status)
            if coords_jump is not None:
                try:
                    self.telemetry.coords_jump = int(coords_jump)
                except Exception:
                    self.telemetry.coords_jump = None
            if coords_confidence is not None:
                try:
                    self.telemetry.coords_confidence = float(coords_confidence)
                except Exception:
                    self.telemetry.coords_confidence = None
            if coords_provider_status is not None:
                self.telemetry.coords_provider_status = str(coords_provider_status)
            if coords_provider_state is not None:
                try:
                    self.telemetry.coords_provider_state = dict(coords_provider_state)
                except Exception:
                    self.telemetry.coords_provider_state = None
            if coords_confidence_level is not None:
                self.telemetry.coords_confidence_level = str(coords_confidence_level)
            if minimap_mode_used is not None:
                self.telemetry.minimap_mode_used = str(minimap_mode_used)
            if minimap_response is not None:
                try:
                    self.telemetry.minimap_response = float(minimap_response)
                except Exception:
                    self.telemetry.minimap_response = None
            if minimap_delta_dx is not None:
                try:
                    self.telemetry.minimap_delta_dx = float(minimap_delta_dx)
                except Exception:
                    self.telemetry.minimap_delta_dx = None
            if minimap_delta_dy is not None:
                try:
                    self.telemetry.minimap_delta_dy = float(minimap_delta_dy)
                except Exception:
                    self.telemetry.minimap_delta_dy = None
            if minimap_acc_dx is not None:
                try:
                    self.telemetry.minimap_acc_dx = float(minimap_acc_dx)
                except Exception:
                    self.telemetry.minimap_acc_dx = None
            if minimap_acc_dy is not None:
                try:
                    self.telemetry.minimap_acc_dy = float(minimap_acc_dy)
                except Exception:
                    self.telemetry.minimap_acc_dy = None
            if minimap_marker_dpx_dx is not None:
                try:
                    self.telemetry.minimap_marker_dpx_dx = float(minimap_marker_dpx_dx)
                except Exception:
                    self.telemetry.minimap_marker_dpx_dx = None
            if minimap_marker_dpx_dy is not None:
                try:
                    self.telemetry.minimap_marker_dpx_dy = float(minimap_marker_dpx_dy)
                except Exception:
                    self.telemetry.minimap_marker_dpx_dy = None
            if ring_equipped is not None:
                self.telemetry.ring_equipped = bool(ring_equipped)
            if amulet_equipped is not None:
                self.telemetry.amulet_equipped = bool(amulet_equipped)
            if low_hp is not None:
                self.telemetry.low_hp = bool(low_hp)
            if low_mp is not None:
                self.telemetry.low_mp = bool(low_mp)
            if low_cap is not None:
                self.telemetry.low_cap = bool(low_cap)
            if potions_remaining is not None:
                try:
                    self.telemetry.potions_remaining = int(potions_remaining)
                except Exception:
                    self.telemetry.potions_remaining = None
            if potions_min is not None:
                try:
                    self.telemetry.potions_min = int(potions_min)
                except Exception:
                    self.telemetry.potions_min = None
            if low_potions is not None:
                self.telemetry.low_potions = bool(low_potions)
            if paralyzed is not None:
                self.telemetry.paralyzed = bool(paralyzed)
            if haste_active is not None:
                self.telemetry.haste_active = bool(haste_active)
            if utamo_active is not None:
                self.telemetry.utamo_active = bool(utamo_active)
            if hungry is not None:
                self.telemetry.hungry = bool(hungry)
            if battlelist_n_rows is not None:
                try:
                    self.telemetry.battlelist_n_rows = int(battlelist_n_rows)
                except Exception:
                    self.telemetry.battlelist_n_rows = None
            if battlelist_n_valid is not None:
                try:
                    self.telemetry.battlelist_n_valid = int(battlelist_n_valid)
                except Exception:
                    self.telemetry.battlelist_n_valid = None
            if battlelist_top_names is not None:
                try:
                    self.telemetry.battlelist_top_names = [str(x) for x in list(battlelist_top_names) if str(x)]
                except Exception:
                    self.telemetry.battlelist_top_names = None
            if battlelist_confidence is not None:
                try:
                    self.telemetry.battlelist_confidence = float(battlelist_confidence)
                except Exception:
                    self.telemetry.battlelist_confidence = None
            if battlelist_target_state is not None:
                self.telemetry.battlelist_target_state = str(battlelist_target_state)
            if battlelist_target_row is not None:
                try:
                    self.telemetry.battlelist_target_row = int(battlelist_target_row)
                except Exception:
                    self.telemetry.battlelist_target_row = None
            if battlelist_alive_prob is not None:
                try:
                    self.telemetry.battlelist_alive_prob = float(battlelist_alive_prob)
                except Exception:
                    self.telemetry.battlelist_alive_prob = None
            if battlelist_selected_prob is not None:
                try:
                    self.telemetry.battlelist_selected_prob = float(battlelist_selected_prob)
                except Exception:
                    self.telemetry.battlelist_selected_prob = None
            if battlelist_target_reason is not None:
                self.telemetry.battlelist_target_reason = str(battlelist_target_reason)
            if target is not None:
                self.telemetry.target = str(target)
            if autotarget_enabled is not None:
                self.telemetry.autotarget_enabled = bool(autotarget_enabled)
            if autotarget_current_target is not None:
                self.telemetry.autotarget_current_target = str(autotarget_current_target)
            if autotarget_follow_active is not None:
                self.telemetry.autotarget_follow_active = bool(autotarget_follow_active)
            if autotarget_last_retarget_reason is not None:
                self.telemetry.autotarget_last_retarget_reason = str(autotarget_last_retarget_reason)
            if recommendation is not None:
                self.telemetry.recommendation = str(recommendation)
            if cavebot_next is not None:
                self.telemetry.cavebot_next = str(cavebot_next)
            if cavebot_waypoint is not None:
                self.telemetry.cavebot_waypoint = str(cavebot_waypoint)
            if cavebot_action is not None:
                self.telemetry.cavebot_action = str(cavebot_action)
            if cavebot_step_idx is not None:
                try:
                    self.telemetry.cavebot_step_idx = int(cavebot_step_idx)
                except Exception:
                    self.telemetry.cavebot_step_idx = None
            if cavebot_step_next_idx is not None:
                try:
                    self.telemetry.cavebot_step_next_idx = int(cavebot_step_next_idx)
                except Exception:
                    self.telemetry.cavebot_step_next_idx = None
            if cavebot_step_total is not None:
                try:
                    self.telemetry.cavebot_step_total = int(cavebot_step_total)
                except Exception:
                    self.telemetry.cavebot_step_total = None
            if cavebot_blocked is not None:
                self.telemetry.cavebot_blocked = bool(cavebot_blocked)
            if cavebot_block_reason is not None:
                self.telemetry.cavebot_block_reason = str(cavebot_block_reason)
            if cavebot_finished is not None:
                self.telemetry.cavebot_finished = bool(cavebot_finished)
            if cavebot_finish_reason is not None:
                self.telemetry.cavebot_finish_reason = str(cavebot_finish_reason)
            if nav_mode is not None:
                self.telemetry.nav_mode = str(nav_mode)
            if nav_blockers is not None:
                try:
                    self.telemetry.nav_blockers = int(nav_blockers)
                except Exception:
                    self.telemetry.nav_blockers = None
            if nav_astar_found is not None:
                self.telemetry.nav_astar_found = bool(nav_astar_found)
            if nav_astar_path_len is not None:
                try:
                    self.telemetry.nav_astar_path_len = int(nav_astar_path_len)
                except Exception:
                    self.telemetry.nav_astar_path_len = None
            if nav_astar_visited is not None:
                try:
                    self.telemetry.nav_astar_visited = int(nav_astar_visited)
                except Exception:
                    self.telemetry.nav_astar_visited = None
            if action_request is not None:
                self.telemetry.action_request = str(action_request)
            if action_requests is not None:
                try:
                    self.telemetry.action_requests = [
                        dict(x) for x in list(action_requests) if isinstance(x, dict)
                    ]
                except Exception:
                    self.telemetry.action_requests = []
            if action_committed is not None:
                self.telemetry.action_committed = bool(action_committed)
            if action_source is not None:
                self.telemetry.action_source = str(action_source)
            if input_plan is not None:
                self.telemetry.input_plan = str(input_plan)
            if note is not None:
                self.telemetry.note = str(note)
            if stuck_reason is not None:
                self.telemetry.stuck_reason = str(stuck_reason)
            if stuck_idle_s is not None:
                try:
                    self.telemetry.stuck_idle_s = float(stuck_idle_s)
                except Exception:
                    self.telemetry.stuck_idle_s = None
            if stuck_blockers is not None:
                try:
                    self.telemetry.stuck_blockers = int(stuck_blockers)
                except Exception:
                    self.telemetry.stuck_blockers = None
            if stuck_extra is not None:
                self.telemetry.stuck_extra = str(stuck_extra)

            if injection_state is not None:
                self.telemetry.injection_state = str(injection_state)
            if injection_reason is not None:
                self.telemetry.injection_reason = str(injection_reason)

            if client_hwnd is not None:
                try:
                    self.telemetry.client_hwnd = int(client_hwnd)
                except Exception:
                    self.telemetry.client_hwnd = None
            if client_title is not None:
                self.telemetry.client_title = str(client_title)
            if client_is_foreground is not None:
                self.telemetry.client_is_foreground = bool(client_is_foreground)
            if client_is_minimized is not None:
                self.telemetry.client_is_minimized = bool(client_is_minimized)
            if client_is_maximized is not None:
                self.telemetry.client_is_maximized = bool(client_is_maximized)
            if capture_backend is not None:
                self.telemetry.capture_backend = str(capture_backend)
            if capture_target is not None:
                self.telemetry.capture_target = str(capture_target)
            if target_title is not None:
                self.telemetry.target_title = str(target_title)
            if target_hwnd is not None:
                try:
                    self.telemetry.target_hwnd = int(target_hwnd)
                except Exception:
                    self.telemetry.target_hwnd = None
            if target_bounds is not None:
                try:
                    self.telemetry.target_bounds = [int(x) for x in list(target_bounds)][:4]
                except Exception:
                    self.telemetry.target_bounds = None
            if target_found is not None:
                self.telemetry.target_found = bool(target_found)
            if target_reason is not None:
                self.telemetry.target_reason = str(target_reason)
            if capture_state is not None:
                self.telemetry.capture_state = str(capture_state)
            if capture_bounds is not None:
                try:
                    self.telemetry.capture_bounds = [int(x) for x in list(capture_bounds)][:4]
                except Exception:
                    self.telemetry.capture_bounds = None
            if input_block_reason is not None:
                self.telemetry.input_block_reason = str(input_block_reason)
            if input_bridge_connected is not None:
                self.telemetry.input_bridge_connected = bool(input_bridge_connected)
            if input_bridge_rate_sent is not None:
                self.telemetry.input_bridge_rate_sent = int(input_bridge_rate_sent)
            if input_bridge_rate_accepted is not None:
                self.telemetry.input_bridge_rate_accepted = int(input_bridge_rate_accepted)
            if input_bridge_rate_rejected is not None:
                self.telemetry.input_bridge_rate_rejected = int(input_bridge_rate_rejected)
            if input_bridge_last_error is not None:
                self.telemetry.input_bridge_last_error = str(input_bridge_last_error)

    def update_health(
        self,
        *,
        ts: float | None = None,
        uptime_s: float | None = None,
        frame_age_s: float | None = None,
        gs_age_s: float | None = None,
        dead_threads: str | None = None,
        capture_ok: int | None = None,
        capture_none: int | None = None,
        vision_ok: int | None = None,
        vision_ex: int | None = None,
        decision_ok: int | None = None,
        decision_ex: int | None = None,
        drop_frame_queue: int | None = None,
        drop_gs_queue: int | None = None,
        drop_replay_queue: int | None = None,
        drop_jsonl_queue: int | None = None,
        q_frame: int | None = None,
        q_gs: int | None = None,
        capture_ms_last: float | None = None,
        vision_ms_last: float | None = None,
        decision_ms_last: float | None = None,
        capture_latency_ms: float | None = None,
        roi_offset_dx_px: float | None = None,
        roi_offset_dy_px: float | None = None,
        roi_offset_score: float | None = None,
        warn: str | None = None,
    ) -> None:
        with self._lock:
            self.health.ts = float(time.time() if ts is None else ts)
            if uptime_s is not None:
                self.health.uptime_s = float(uptime_s)
            if frame_age_s is not None:
                self.health.frame_age_s = float(frame_age_s)
            if gs_age_s is not None:
                self.health.gs_age_s = float(gs_age_s)
            if dead_threads is not None:
                self.health.dead_threads = str(dead_threads)
            if capture_ok is not None:
                self.health.capture_ok = int(capture_ok)
            if capture_none is not None:
                self.health.capture_none = int(capture_none)
            if vision_ok is not None:
                self.health.vision_ok = int(vision_ok)
            if vision_ex is not None:
                self.health.vision_ex = int(vision_ex)
            if decision_ok is not None:
                self.health.decision_ok = int(decision_ok)
            if decision_ex is not None:
                self.health.decision_ex = int(decision_ex)
            if drop_frame_queue is not None:
                self.health.drop_frame_queue = int(drop_frame_queue)
            if drop_gs_queue is not None:
                self.health.drop_gs_queue = int(drop_gs_queue)
            if drop_replay_queue is not None:
                self.health.drop_replay_queue = int(drop_replay_queue)
            if drop_jsonl_queue is not None:
                self.health.drop_jsonl_queue = int(drop_jsonl_queue)
            if q_frame is not None:
                self.health.q_frame = int(q_frame)
            if q_gs is not None:
                self.health.q_gs = int(q_gs)
            if capture_ms_last is not None:
                self.health.capture_ms_last = float(capture_ms_last)
            if vision_ms_last is not None:
                self.health.vision_ms_last = float(vision_ms_last)
            if decision_ms_last is not None:
                self.health.decision_ms_last = float(decision_ms_last)
            if capture_latency_ms is not None:
                self.health.capture_latency_ms = float(capture_latency_ms)
            if roi_offset_dx_px is not None:
                self.health.roi_offset_dx_px = float(roi_offset_dx_px)
            if roi_offset_dy_px is not None:
                self.health.roi_offset_dy_px = float(roi_offset_dy_px)
            if roi_offset_score is not None:
                self.health.roi_offset_score = float(roi_offset_score)
            if warn is not None:
                self.health.warn = str(warn)
