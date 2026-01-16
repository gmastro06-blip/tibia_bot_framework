from __future__ import annotations

from navigation.pathfinding import astar, make_bounded_walkable
from navigation.navigator import Navigator
from navigation.route import Waypoint


def test_astar_finds_shortest_path_in_empty_grid() -> None:
    is_walkable = make_bounded_walkable(min_x=-10, max_x=10, min_y=-10, max_y=10)
    res = astar((0, 0), (3, 0), is_walkable=is_walkable, max_nodes=1000)
    assert res is not None
    assert res.path[0] == (0, 0)
    assert res.path[-1] == (3, 0)
    # 3 steps => 4 points
    assert len(res.path) == 4


def test_navigator_astar_replans_when_blocked(monkeypatch) -> None:
    monkeypatch.setenv("CAVEBOT_PATHFIND", "astar")
    monkeypatch.setenv("CAVEBOT_ASTAR_RADIUS", "10")
    monkeypatch.setenv("CAVEBOT_ASTAR_MAX_NODES", "2000")

    nav = Navigator([Waypoint(x=3, y=0)])

    # With the direct east tile blocked, A* should detour (north or south).
    d1 = nav.decide((0, 0), blocked={(1, 0)})
    assert d1.direction in {"north", "south"}

    # If the block disappears, the optimal first step becomes east.
    d2 = nav.decide((0, 0), blocked=set())
    assert d2.direction == "east"


def test_navigator_default_mode_unchanged(monkeypatch) -> None:
    monkeypatch.delenv("CAVEBOT_PATHFIND", raising=False)

    nav = Navigator([Waypoint(x=3, y=0)])
    d = nav.decide((0, 0))
    assert d.direction == "east"
