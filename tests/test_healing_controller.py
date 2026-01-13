from __future__ import annotations

from decision.healing import HealingController
from runtime_config import HealingConfig


class _Sig:
    def __init__(self, hp_pct: float | None = None, mp_pct: float | None = None) -> None:
        self.hp_pct = hp_pct
        self.mp_pct = mp_pct


def test_healing_controller_hysteresis_and_cooldown_hp() -> None:
    cfg = HealingConfig(enabled=True, hp_below_pct=70, hp_recover_pct=80, cooldown_s=1.0)
    ctrl = HealingController()

    sig = _Sig(hp_pct=60.0)

    d1 = ctrl.update(sig, cfg, now=0.0)
    assert d1.heal_hp is True

    # Within cooldown, should not fire again.
    d2 = ctrl.update(sig, cfg, now=0.5)
    assert d2.heal_hp is False

    # After cooldown, still armed because still below recover.
    d3 = ctrl.update(sig, cfg, now=1.2)
    assert d3.heal_hp is True

    # Recover above threshold -> disarm.
    sig.hp_pct = 82.0
    d4 = ctrl.update(sig, cfg, now=2.5)
    assert d4.heal_hp is False


def test_healing_controller_mp_independent_state() -> None:
    cfg = HealingConfig(enabled=True, mp_below_pct=20, mp_recover_pct=30, cooldown_s=0.5)
    ctrl = HealingController()

    sig = _Sig(hp_pct=90.0, mp_pct=10.0)

    d1 = ctrl.update(sig, cfg, now=0.0)
    assert d1.heal_mp is True
    assert d1.heal_hp is False

    sig.mp_pct = 25.0  # Between below and recover -> still armed but obey cooldown.
    d2 = ctrl.update(sig, cfg, now=0.2)
    assert d2.heal_mp is False

    d3 = ctrl.update(sig, cfg, now=0.6)
    assert d3.heal_mp is True

    sig.mp_pct = 35.0  # Above recover -> disarm.
    d4 = ctrl.update(sig, cfg, now=1.2)
    assert d4.heal_mp is False
