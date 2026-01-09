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
    # Texto amigable opcional
    note: str = ""


@dataclass
class RuntimeConfig:
    healing: HealingConfig = field(default_factory=HealingConfig)
    cavebot: CavebotConfig = field(default_factory=CavebotConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    telemetry: TelemetrySnapshot = field(default_factory=TelemetrySnapshot)
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
                note=str(self.telemetry.note),
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
            if note is not None:
                self.telemetry.note = str(note)
