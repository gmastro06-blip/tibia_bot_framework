from __future__ import annotations

from dataclasses import dataclass, field
import threading


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
class RuntimeConfig:
    healing: HealingConfig = field(default_factory=HealingConfig)
    cavebot: CavebotConfig = field(default_factory=CavebotConfig)
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
