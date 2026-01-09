from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time


@dataclass
class HealingConfig:
    enabled: bool = False
    hp_below_pct: int = 70
    mp_below_pct: int = 30
    action: str = ""


@dataclass
class CavebotConfig:
    enabled: bool = False
    route_path: str = "configs/route.json"


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
    # Señales/estados
    low_hp: bool | None = None
    low_mp: bool | None = None
    paralyzed: bool | None = None
    haste_active: bool | None = None
    utamo_active: bool | None = None
    hungry: bool | None = None
    target: str = ""
    recommendation: str = ""
    cavebot_next: str = ""
    cavebot_waypoint: str = ""
    cavebot_action: str = ""
    # What the bot would do (assistant mode): serialized mock action(s)
    action_request: str = ""
    action_committed: bool = False
    # Texto amigable opcional
    note: str = ""


@dataclass
class RuntimeConfig:
    healing: HealingConfig = field(default_factory=HealingConfig)
    cavebot: CavebotConfig = field(default_factory=CavebotConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    assistant: AssistantConfig = field(default_factory=AssistantConfig)
    replay: ReplayConfig = field(default_factory=ReplayConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    telemetry: TelemetrySnapshot = field(default_factory=TelemetrySnapshot)
    _advance_counter: int = field(default=0, init=False, repr=False)
    _replay_force_counter: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def snapshot(self) -> tuple[HealingConfig, CavebotConfig]:
        with self._lock:
            return (
                HealingConfig(
                    enabled=bool(self.healing.enabled),
                    hp_below_pct=int(self.healing.hp_below_pct),
                    mp_below_pct=int(self.healing.mp_below_pct),
                    action=str(self.healing.action),
                ),
                CavebotConfig(
                    enabled=bool(self.cavebot.enabled),
                    route_path=str(self.cavebot.route_path),
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
                low_hp=self.telemetry.low_hp,
                low_mp=self.telemetry.low_mp,
                paralyzed=self.telemetry.paralyzed,
                haste_active=self.telemetry.haste_active,
                utamo_active=self.telemetry.utamo_active,
                hungry=self.telemetry.hungry,
                target=str(self.telemetry.target),
                recommendation=str(self.telemetry.recommendation),
                cavebot_next=str(self.telemetry.cavebot_next),
                cavebot_waypoint=str(self.telemetry.cavebot_waypoint),
                cavebot_action=str(self.telemetry.cavebot_action),
                action_request=str(self.telemetry.action_request),
                action_committed=bool(self.telemetry.action_committed),
                note=str(self.telemetry.note),
            )

    def assistant_snapshot(self) -> AssistantConfig:
        with self._lock:
            return AssistantConfig(
                enabled=bool(self.assistant.enabled),
                confirm_actions=bool(self.assistant.confirm_actions),
                sound_alerts=bool(self.assistant.sound_alerts),
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
        mp_below_pct: int | None = None,
        action: str | None = None,
    ) -> None:
        with self._lock:
            if enabled is not None:
                self.healing.enabled = bool(enabled)
            if hp_below_pct is not None:
                self.healing.hp_below_pct = int(hp_below_pct)
            if mp_below_pct is not None:
                self.healing.mp_below_pct = int(mp_below_pct)
            if action is not None:
                self.healing.action = str(action)

    def update_cavebot(self, *, enabled: bool | None = None, route_path: str | None = None) -> None:
        with self._lock:
            if enabled is not None:
                self.cavebot.enabled = bool(enabled)
            if route_path is not None:
                self.cavebot.route_path = str(route_path)

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
    ) -> None:
        with self._lock:
            if enabled is not None:
                self.assistant.enabled = bool(enabled)
            if confirm_actions is not None:
                self.assistant.confirm_actions = bool(confirm_actions)
            if sound_alerts is not None:
                self.assistant.sound_alerts = bool(sound_alerts)

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

    def request_advance(self) -> int:
        """El usuario confirmó que se puede avanzar una acción recomendada."""
        with self._lock:
            self._advance_counter += 1
            return int(self._advance_counter)

    def advance_counter_snapshot(self) -> int:
        with self._lock:
            return int(self._advance_counter)

    def request_replay_snapshot(self) -> int:
        """Solicita forzar un snapshot de replay en el próximo frame disponible."""
        with self._lock:
            self._replay_force_counter += 1
            return int(self._replay_force_counter)

    def replay_force_counter_snapshot(self) -> int:
        with self._lock:
            return int(self._replay_force_counter)

    def update_telemetry(
        self,
        *,
        hp_current: int | None = None,
        hp_max: int | None = None,
        hp_pct: float | None = None,
        mp_current: int | None = None,
        mp_max: int | None = None,
        mp_pct: float | None = None,
        low_hp: bool | None = None,
        low_mp: bool | None = None,
        paralyzed: bool | None = None,
        haste_active: bool | None = None,
        utamo_active: bool | None = None,
        hungry: bool | None = None,
        target: str | None = None,
        recommendation: str | None = None,
        cavebot_next: str | None = None,
        cavebot_waypoint: str | None = None,
        cavebot_action: str | None = None,
        action_request: str | None = None,
        action_committed: bool | None = None,
        note: str | None = None,
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
            if low_hp is not None:
                self.telemetry.low_hp = bool(low_hp)
            if low_mp is not None:
                self.telemetry.low_mp = bool(low_mp)
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
            if action_request is not None:
                self.telemetry.action_request = str(action_request)
            if action_committed is not None:
                self.telemetry.action_committed = bool(action_committed)
            if note is not None:
                self.telemetry.note = str(note)
