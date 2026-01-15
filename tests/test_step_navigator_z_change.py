from __future__ import annotations

from dataclasses import dataclass

import pytest

from navigation.route import Waypoint
from navigation.step_navigator import StepNavigator


@dataclass
class _GS:
    pos_x: int | None
    pos_y: int | None
    pos_z: int | None


def test_rope_waits_for_z_change(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAVEBOT_LOOP", "0")
    monkeypatch.setenv("CAVEBOT_Z_CHANGE_TIMEOUT_S", "5")

    nav = StepNavigator(
        [
            Waypoint(100, 200, z=10, type="rope"),
            Waypoint(100, 200, z=9, type="node"),
        ]
    )

    # Arrive at rope tile: should trigger rope action and wait for z to drop.
    d1 = nav.decide(gamestate=_GS(100, 200, 10))
    assert d1.reached_waypoint is True
    assert d1.waypoint is not None
    assert str(d1.waypoint.type).lower() == "rope"
    assert nav.idx == 0  # do not advance until z changes

    d2 = nav.decide(gamestate=_GS(100, 200, 10))
    assert d2.reached_waypoint is False
    assert "waiting_z" in (d2.note or "")
    assert (d2.stuck_reason or "") == ""
    assert nav.idx == 0

    # Z changed: should advance past rope and the next node immediately.
    _ = nav.decide(gamestate=_GS(100, 200, 9))
    assert nav.idx >= 1


def test_rope_timeout_sets_stuck_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAVEBOT_LOOP", "0")
    monkeypatch.setenv("CAVEBOT_Z_CHANGE_TIMEOUT_S", "0.5")

    # Control time.time() in the module.
    import navigation.step_navigator as sn

    t0 = 1000.0
    times = [t0, t0 + 0.6]

    def _fake_time() -> float:
        return times.pop(0) if times else (t0 + 0.2)

    monkeypatch.setattr(sn.time, "time", _fake_time)

    nav = StepNavigator([Waypoint(1, 1, z=8, type="rope")])

    _ = nav.decide(gamestate=_GS(1, 1, 8))
    d2 = nav.decide(gamestate=_GS(1, 1, 8))
    assert d2.reached_waypoint is False
    assert (d2.stuck_reason or "").endswith("TIMEOUT")
