from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time


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
    input_mode: str = "log"  # "log"|"keyboard"
    target_hotkey: str = ""
    minimap_hotkey: str = ""


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
    cap_current: int | None = None
    pos_x: int | None = None
    pos_y: int | None = None
    pos_z: int | None = None
    coords_provider: str = ""  # "ocr"|"minimap"|"file"|"env"|"disabled"|...
    # Coords quality
    coords_status: str = ""  # "OK"|"NO_COORDS"|"BAD_JUMP"|"UNSTABLE"|""
    coords_jump: int | None = None  # manhattan jump vs last coords
    coords_confidence: float | None = None  # provider-specific confidence (e.g., minimap)
    coords_provider_status: str = ""  # provider-specific status text
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
    target: str = ""
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
    action_committed: bool = False
    # Log-only: planned inputs (keys/hotkeys/macros) derived from ActionRequest(s)
    input_plan: str = ""
    # Texto amigable opcional
    note: str = ""
    # Diagnóstico estructurado (sin inputs)
    stuck_reason: str = ""  # "STALE_GS"|"NO_COORDS"|"BLOCKED"|"MOVE_COMMITTED_NO_CHANGE"|"IDLE"|""
    stuck_idle_s: float | None = None
    stuck_blockers: int | None = None
    stuck_extra: str = ""


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
    replay: ReplayConfig = field(default_factory=ReplayConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    telemetry: TelemetrySnapshot = field(default_factory=TelemetrySnapshot)
    health: HealthSnapshot = field(default_factory=HealthSnapshot)
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

    def telemetry_snapshot(self) -> TelemetrySnapshot:
        with self._lock:
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
                target=str(self.telemetry.target),
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
                action_committed=bool(self.telemetry.action_committed),
                input_plan=str(getattr(self.telemetry, "input_plan", "")),
                note=str(self.telemetry.note),
                stuck_reason=str(getattr(self.telemetry, "stuck_reason", "")),
                stuck_idle_s=getattr(self.telemetry, "stuck_idle_s", None),
                stuck_blockers=getattr(self.telemetry, "stuck_blockers", None),
                stuck_extra=str(getattr(self.telemetry, "stuck_extra", "")),
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

    def assistant_snapshot(self) -> AssistantConfig:
        with self._lock:
            return AssistantConfig(
                enabled=bool(self.assistant.enabled),
                confirm_actions=bool(self.assistant.confirm_actions),
                sound_alerts=bool(self.assistant.sound_alerts),
                input_mode=str(getattr(self.assistant, "input_mode", "log") or "log"),
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
                if mode not in {"keyboard", "wininput", "log"}:
                    mode = "log"
                self.assistant.input_mode = mode
            if target_hotkey is not None:
                self.assistant.target_hotkey = str(target_hotkey)
            if minimap_hotkey is not None:
                self.assistant.minimap_hotkey = str(minimap_hotkey)

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
        cap_current: int | None = None,
        pos_x: int | None = None,
        pos_y: int | None = None,
        pos_z: int | None = None,
        coords_provider: str | None = None,
        coords_status: str | None = None,
        coords_jump: int | None = None,
        coords_confidence: float | None = None,
        coords_provider_status: str | None = None,
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
        target: str | None = None,
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
        action_committed: bool | None = None,
        input_plan: str | None = None,
        note: str | None = None,
        stuck_reason: str | None = None,
        stuck_idle_s: float | None = None,
        stuck_blockers: int | None = None,
        stuck_extra: str | None = None,
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
            if cap_current is not None:
                self.telemetry.cap_current = int(cap_current)
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
            if target is not None:
                self.telemetry.target = str(target)
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
            if action_committed is not None:
                self.telemetry.action_committed = bool(action_committed)
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
