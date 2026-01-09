from __future__ import annotations

from dataclasses import dataclass

from decision.signals import evaluate_signals
from runtime_config import HealingConfig, SimulationConfig


@dataclass
class FakeGS:
    hp_current: int | None = None
    hp_max: int | None = None
    mp_current: int | None = None
    mp_max: int | None = None
    hp_pct: float | None = None
    mp_pct: float | None = None


def test_evaluate_signals_derives_pct_when_missing() -> None:
    gs = FakeGS(hp_current=50, hp_max=100, mp_current=25, mp_max=100)
    healing = HealingConfig(enabled=True, hp_below_pct=60, mp_below_pct=30)
    sim = SimulationConfig(enabled=False)

    sig = evaluate_signals(gs, healing, sim)
    assert sig.hp_pct == 50.0
    assert sig.mp_pct == 25.0
    assert sig.low_hp is True
    assert sig.low_mp is True
    assert sig.healing_trigger is True
    assert sig.paralyzed is None


def test_evaluate_signals_uses_precomputed_pct() -> None:
    gs = FakeGS(hp_current=999, hp_max=1000, hp_pct=12.5, mp_current=1, mp_max=1, mp_pct=90.0)
    healing = HealingConfig(enabled=True, hp_below_pct=20, mp_below_pct=10)

    sig = evaluate_signals(gs, healing, None)
    assert sig.hp_pct == 12.5
    assert sig.mp_pct == 90.0
    assert sig.low_hp is True
    assert sig.low_mp is False


def test_evaluate_signals_simulation_overrides_states_when_enabled() -> None:
    gs = FakeGS(hp_current=100, hp_max=100, mp_current=100, mp_max=100)
    healing = HealingConfig(enabled=False)
    sim = SimulationConfig(enabled=True, paralyzed=True, haste_active=False, utamo_active=True, hungry=True)

    sig = evaluate_signals(gs, healing, sim)
    assert sig.paralyzed is True
    assert sig.haste_active is False
    assert sig.utamo_active is True
    assert sig.hungry is True


def test_evaluate_signals_simulation_disabled_yields_unknown_states() -> None:
    gs = FakeGS(hp_current=100, hp_max=100, mp_current=100, mp_max=100)
    sim = SimulationConfig(enabled=False, paralyzed=True, haste_active=True, utamo_active=True, hungry=True)

    sig = evaluate_signals(gs, None, sim)
    assert sig.paralyzed is None
    assert sig.haste_active is None
    assert sig.utamo_active is None
    assert sig.hungry is None
