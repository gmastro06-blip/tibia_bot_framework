from __future__ import annotations

from navigation.route import Waypoint
from navigation.step_navigator import StepNavigator


def test_step_navigator_preview_does_not_mutate_state() -> None:
    nav = StepNavigator([Waypoint(0, 0), Waypoint(2, -1)])

    # Preview should indicate first east, but not consume it.
    d1 = nav.preview()
    assert d1.direction == "east"

    d2 = nav.preview()
    assert d2.direction == "east"  # still east, since we didn't advance

    # Now advance one step; next preview should still be east (second step).
    nav.decide()
    d3 = nav.preview()
    assert d3.direction == "east"


def test_step_navigator_preview_reached_waypoint_when_segment_complete() -> None:
    nav = StepNavigator([Waypoint(0, 0), Waypoint(1, 0)])

    # First step moves east.
    assert nav.preview().direction == "east"
    nav.decide()

    # Next should be reached waypoint (direction None, reached True)
    nxt = nav.preview()
    assert nxt.reached_waypoint is True
    assert nxt.direction is None
