from route_editor.conversion import route_items_to_waypoints, waypoints_to_route_items
from route_editor.models import WaypointStep
from route_editor.waypoints import parse_waypoints, serialize_waypoints


def test_waypoints_to_route_items_and_back() -> None:
    steps = [
        WaypointStep(kind="label", name="start"),
        WaypointStep(kind="call", name="setup_default"),
        WaypointStep(kind="node", x=100, y=200, z=7),
        WaypointStep(kind="cond", name="HAS_ROPE", params={"label_true": "ok", "label_false": "fail"}),
        WaypointStep(kind="label", name="ok"),
        WaypointStep(kind="rope", x=101, y=200, z=7),
        WaypointStep(kind="label", name="fail"),
        WaypointStep(kind="action", name="say_no"),
        WaypointStep(kind="load", name="route_alt"),
    ]

    items = waypoints_to_route_items(steps)
    back = route_items_to_waypoints(items)

    # Serialize/parse gives a stable representation for supported commands.
    text = serialize_waypoints(back)
    parsed = parse_waypoints(text)
    assert parsed.errors == []
    assert serialize_waypoints(parsed.steps) == text
