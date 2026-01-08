from __future__ import annotations

from navigation.navigator import Navigator
from navigation.route import Waypoint


def test_navigator_moves_towards_waypoint_prefers_major_axis(monkeypatch):
    monkeypatch.setenv("CAVEBOT_WAYPOINT_TOL", "0")
    monkeypatch.setenv("CAVEBOT_LOOP", "0")

    nav = Navigator([Waypoint(10, 0)])

    d1 = nav.decide((0, 0))
    assert d1.direction == "east"
    assert d1.reached_waypoint is False


def test_navigator_advances_on_reach(monkeypatch):
    monkeypatch.setenv("CAVEBOT_WAYPOINT_TOL", "0")
    monkeypatch.setenv("CAVEBOT_LOOP", "0")

    nav = Navigator([Waypoint(0, 0, name="start"), Waypoint(1, 0, name="next")])

    d0 = nav.decide((0, 0))
    assert d0.reached_waypoint is True
    assert d0.waypoint is not None
    assert d0.waypoint.name == "start"

    d1 = nav.decide((0, 0))
    assert d1.direction == "east"
    assert d1.reached_waypoint is False


def test_navigator_loops(monkeypatch):
    monkeypatch.setenv("CAVEBOT_WAYPOINT_TOL", "0")
    monkeypatch.setenv("CAVEBOT_LOOP", "1")

    nav = Navigator([Waypoint(0, 0), Waypoint(1, 0)])

    # reach wp0
    _ = nav.decide((0, 0))
    # reach wp1
    _ = nav.decide((1, 0))

    # should loop back to wp0; from (1,0) towards (0,0) => west
    d = nav.decide((1, 0))
    assert d.direction == "west"
