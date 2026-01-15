from __future__ import annotations

from pathlib import Path

from navigation.route import load_route


def test_load_route_supports_scripts_master_waypoints_in() -> None:
    p = Path(__file__).resolve().parents[1] / "scripts-master" / "wasp_ab" / "waypoints.in"
    assert p.is_file(), f"fixture missing: {p}"

    route = load_route(str(p))
    assert len(route) > 10

    # The fixture contains no-spaces coords, rope steps, and a ladder step.
    assert any((getattr(wp, "raw_line", "") or "").startswith("stand (32603,31704,7)") for wp in route)
    assert any(str(getattr(wp, "type", "") or "").lower() == "rope" for wp in route)
    assert any(str(getattr(wp, "type", "") or "").lower() == "ladder" for wp in route)

    # Basic sanity: a rope step at z=10 exists.
    rope10 = [wp for wp in route if str(getattr(wp, "type", "") or "").lower() == "rope" and getattr(wp, "z", None) == 10]
    assert rope10, "expected at least one rope(z=10) step"
