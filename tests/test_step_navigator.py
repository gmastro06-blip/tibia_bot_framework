from __future__ import annotations

from navigation.route import Waypoint
from navigation.step_navigator import StepNavigator


def test_step_navigator_executes_dx_then_dy(monkeypatch):
    monkeypatch.setenv("CAVEBOT_LOOP", "0")

    nav = StepNavigator([Waypoint(0, 0), Waypoint(2, -1)])

    # dx=+2 => east,east; dy=-1 => north; then reached
    d1 = nav.decide()
    assert d1.direction == "east"

    d2 = nav.decide()
    assert d2.direction == "east"

    d3 = nav.decide()
    assert d3.direction == "north"

    d4 = nav.decide()
    assert d4.reached_waypoint is True
    assert d4.direction is None
    assert d4.waypoint is not None
    assert (d4.waypoint.x, d4.waypoint.y) == (2, -1)


def test_step_navigator_loops(monkeypatch):
    monkeypatch.setenv("CAVEBOT_LOOP", "1")

    nav = StepNavigator([Waypoint(0, 0), Waypoint(1, 0)])

    # move to wp1: east, reached
    _ = nav.decide()
    _ = nav.decide()

    # now loop back to wp0: west, reached
    d3 = nav.decide()
    assert d3.direction == "west"
    d4 = nav.decide()
    assert d4.reached_waypoint is True


def test_step_navigator_consumes_action_after_waypoint(monkeypatch):
    monkeypatch.setenv("CAVEBOT_LOOP", "0")

    nav = StepNavigator(
        [
            Waypoint(0, 0),
            Waypoint(0, 0, action="loot", has_xy=False),
            Waypoint(1, 0),
        ]
    )

    # Action should be surfaced before movement.
    d0 = nav.decide()
    assert d0.reached_waypoint is True
    assert d0.direction is None
    assert d0.waypoint is not None
    assert d0.waypoint.action == "loot"

    d1 = nav.decide()
    assert d1.direction == "east"


def test_step_navigator_conditional_jump_uses_env(monkeypatch):
    monkeypatch.setenv("CAVEBOT_LOOP", "0")
    monkeypatch.setenv("ROUTE_VAR_HUNT_DOWN", "1")

    nav = StepNavigator(
        [
            Waypoint(0, 0),
            Waypoint(0, 0, conditional_jump={"var_name": "hunt_down", "label_jump": "A", "label_skip": "B"}, has_xy=False),
            Waypoint(0, 0, label="A", has_xy=False),
            Waypoint(1, 0),
            Waypoint(0, 0, label="B", has_xy=False),
            Waypoint(9, 0),
        ]
    )

    # Jump should take us to label A => next coordinate is (1,0), so first move is east.
    d1 = nav.decide()
    assert d1.direction == "east"
