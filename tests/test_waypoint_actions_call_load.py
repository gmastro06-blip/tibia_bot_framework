from __future__ import annotations

from decision.waypoint_actions import build_requests_from_waypoint_action


def test_waypoint_actions_support_call_and_load_tokens() -> None:
    reqs = build_requests_from_waypoint_action(
        "call:setup_default;load:route_alt",
        committed=False,
    )
    kinds = [r.kind for r in reqs]
    assert kinds == ["call", "load"]
    assert reqs[0].value == "setup_default"
    assert reqs[1].value == "route_alt"
