from __future__ import annotations

from decision.scheduler import PeriodicTrigger


def test_periodic_trigger_fires_once_per_interval() -> None:
    trig = PeriodicTrigger(interval_s=1.0)

    assert trig.should_fire(now=10.0) is True
    assert trig.should_fire(now=10.2) is False
    assert trig.should_fire(now=10.9) is False
    assert trig.should_fire(now=11.0) is True
    assert trig.should_fire(now=11.1) is False


def test_periodic_trigger_disabled_when_interval_non_positive() -> None:
    assert PeriodicTrigger(interval_s=0.0).should_fire(now=1.0) is False
    assert PeriodicTrigger(interval_s=-1.0).should_fire(now=1.0) is False
