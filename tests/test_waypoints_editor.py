from route_editor.waypoints import parse_waypoints, serialize_waypoints, expand_move_macros, validate_waypoints
from route_editor.models import WaypointStep


def test_parse_roundtrip_with_spaces():
    src = """
    label start
    node (32603,31704,7)
    stand (32603, 31704, 7)
    rope (32603, 31704, 8)
    action deposit
    # action skip_me
    """
    parsed = parse_waypoints(src)
    assert not parsed.errors
    out = serialize_waypoints(parsed.steps)
    again = parse_waypoints(out)
    assert not again.errors
    assert len(again.steps) == len(parsed.steps)
    assert again.steps[1].x == 32603
    assert not again.steps[5].enabled  # commented action


def test_move_expansion_and_cursor():
    steps = [
        WaypointStep(kind="node", x=100, y=100, z=7),
        WaypointStep(kind="move", params={"dir": "N", "steps": 2}),
        WaypointStep(kind="move", params={"dir": "E", "steps": 1}),
    ]
    expanded, errs = expand_move_macros(steps)
    assert not errs
    coords = [(s.x, s.y, s.z) for s in expanded if s.kind == "node"]
    assert coords == [(100, 100, 7), (100, 99, 7), (100, 98, 7), (101, 98, 7)]


def test_validate_flags_errors():
    steps = [WaypointStep(kind="move", params={"dir": "N", "steps": 0})]
    errors = validate_waypoints(steps)
    assert errors
