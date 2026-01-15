from __future__ import annotations

from dataclasses import dataclass

from navigation.route import Waypoint
from navigation.step_navigator import StepNavigator


@dataclass
class _GS:
    pos_x: int | None
    pos_y: int | None
    pos_z: int | None


def test_step_navigator_surfaces_call_step() -> None:
    nav = StepNavigator(
        [
            Waypoint(1, 1, z=7, type="node"),
            Waypoint(0, 0, has_xy=False, call="setup_default"),
            Waypoint(2, 1, z=7, type="node"),
        ]
    )

    # Preview should not mutate idx.
    p = nav.preview(gamestate=_GS(1, 1, 7))
    assert p.reached_waypoint is True
    assert p.waypoint is not None
    assert p.waypoint.call == "setup_default"
    assert nav.idx == 0

    # Decide consumes the call step (but doesn't move OS inputs).
    d = nav.decide(gamestate=_GS(1, 1, 7))
    assert d.reached_waypoint is True
    assert d.waypoint is not None
    assert d.waypoint.call == "setup_default"
    assert nav.idx == 2


def test_step_navigator_surfaces_load_step() -> None:
    nav = StepNavigator(
        [
            Waypoint(10, 10, z=7, type="node"),
            Waypoint(0, 0, has_xy=False, load="route_alt"),
            Waypoint(11, 10, z=7, type="node"),
        ]
    )

    d = nav.decide(gamestate=_GS(10, 10, 7))
    assert d.reached_waypoint is True
    assert d.waypoint is not None
    assert d.waypoint.load == "route_alt"
    assert nav.idx == 2
