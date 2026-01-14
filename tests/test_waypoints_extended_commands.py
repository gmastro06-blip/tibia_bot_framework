from route_editor.waypoints import parse_waypoints, serialize_waypoints, validate_waypoints


def test_waypoints_call_load_cond_roundtrip() -> None:
    text = """
label start
call setup_default
node (100, 200, 7)
cond HAVE_ROPE rope_ok rope_fail
label rope_ok
rope (101, 200, 7)
label rope_fail
action say_no_rope
load route_alt
""".strip()

    r1 = parse_waypoints(text)
    assert r1.errors == []

    out = serialize_waypoints(r1.steps)
    r2 = parse_waypoints(out)
    assert r2.errors == []

    assert serialize_waypoints(r2.steps) == out
    assert validate_waypoints(r2.steps) == []
