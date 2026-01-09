from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from gamestate.builder import GameState
    from runtime_config import HealingConfig, SimulationConfig


@dataclass(frozen=True)
class SignalResult:
    hp_current: int | None
    hp_max: int | None
    hp_pct: float | None
    mp_current: int | None
    mp_max: int | None
    mp_pct: float | None
    low_hp: bool | None
    low_mp: bool | None
    paralyzed: bool | None
    haste_active: bool | None
    utamo_active: bool | None
    hungry: bool | None
    healing_trigger: bool


def _pct(cur: int | None, mx: int | None) -> float | None:
    try:
        if cur is None or not mx:
            return None
        return (float(cur) / float(mx)) * 100.0
    except Exception:
        return None


def evaluate_signals(
    gamestate: object,
    healing_cfg: Optional["HealingConfig"],
    simulation_cfg: Optional["SimulationConfig"],
) -> SignalResult:
    """Evalúa señales derivadas a partir del gamestate + config.

    Importante:
    - Acepta `gamestate` como `object` para ser tolerante a fakes en tests/E2E.
    - No ejecuta acciones. Solo computa señales y el flag `healing_trigger`.
    """

    hp_current = getattr(gamestate, "hp_current", None)
    hp_max = getattr(gamestate, "hp_max", None)
    mp_current = getattr(gamestate, "mp_current", None)
    mp_max = getattr(gamestate, "mp_max", None)

    # Preferimos señales pre-calculadas si existen.
    hp_pct = getattr(gamestate, "hp_pct", None)
    mp_pct = getattr(gamestate, "mp_pct", None)

    if hp_pct is None:
        hp_pct = _pct(hp_current, hp_max)
    if mp_pct is None:
        mp_pct = _pct(mp_current, mp_max)

    low_hp: bool | None = None
    low_mp: bool | None = None
    healing_trigger = False

    if healing_cfg is not None:
        try:
            if hp_pct is not None:
                low_hp = bool(hp_pct < float(healing_cfg.hp_below_pct))
            if mp_pct is not None:
                low_mp = bool(mp_pct < float(healing_cfg.mp_below_pct))
            healing_trigger = bool(getattr(healing_cfg, "enabled", False)) and bool(low_hp or low_mp)
        except Exception:
            low_hp = None
            low_mp = None
            healing_trigger = False

    # Estados no detectados todavía: usar simulación si está habilitada.
    paralyzed: bool | None = None
    haste_active: bool | None = None
    utamo_active: bool | None = None
    hungry: bool | None = None

    if simulation_cfg is not None and bool(getattr(simulation_cfg, "enabled", False)):
        paralyzed = bool(getattr(simulation_cfg, "paralyzed", False))
        haste_active = bool(getattr(simulation_cfg, "haste_active", False))
        utamo_active = bool(getattr(simulation_cfg, "utamo_active", False))
        hungry = bool(getattr(simulation_cfg, "hungry", False))

    return SignalResult(
        hp_current=hp_current,
        hp_max=hp_max,
        hp_pct=hp_pct,
        mp_current=mp_current,
        mp_max=mp_max,
        mp_pct=mp_pct,
        low_hp=low_hp,
        low_mp=low_mp,
        paralyzed=paralyzed,
        haste_active=haste_active,
        utamo_active=utamo_active,
        hungry=hungry,
        healing_trigger=healing_trigger,
    )
