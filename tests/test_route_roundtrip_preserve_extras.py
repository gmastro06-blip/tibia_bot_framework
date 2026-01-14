import json

from navigation.route import load_route, save_route


def test_route_json_roundtrip_preserves_unknown_keys(tmp_path) -> None:
    p = tmp_path / "route.json"
    original = [
        {"x": 10, "y": 20, "z": 7, "type": "node", "foo": "bar", "nested": {"a": 1}},
        {"label": "start", "extra_label": 123},
        {"action": "say_hi", "comment": "ok", "weird": [1, 2, 3]},
    ]
    p.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")

    route = load_route(str(p))
    save_route(str(p), route)

    reread = json.loads(p.read_text(encoding="utf-8"))

    # Unknown keys must survive.
    assert reread[0]["foo"] == "bar"
    assert reread[0]["nested"] == {"a": 1}
    assert reread[1]["extra_label"] == 123
    assert reread[2]["weird"] == [1, 2, 3]
