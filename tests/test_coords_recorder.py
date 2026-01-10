from __future__ import annotations

from navigation.coords_recorder import CoordsRecorder


def test_coords_recorder_dedup_and_threshold() -> None:
    rec = CoordsRecorder(min_manhattan=2)

    assert rec.maybe_add(x=1, y=1, z=7, ts=1.0) is True

    # duplicate
    assert rec.maybe_add(x=1, y=1, z=7, ts=1.1) is False

    # below threshold (distance 1)
    assert rec.maybe_add(x=2, y=1, z=7, ts=1.2) is False

    # meets threshold (distance 2)
    assert rec.maybe_add(x=3, y=1, z=7, ts=1.3) is True

    # floor change should be kept if not duplicate
    assert rec.maybe_add(x=3, y=1, z=8, ts=1.4) is False  # dist=0 => below threshold

    rec2 = CoordsRecorder(min_manhattan=0)
    assert rec2.maybe_add(x=3, y=1, z=7, ts=1.0) is True
    assert rec2.maybe_add(x=3, y=1, z=8, ts=1.1) is True

    wps = rec2.to_route_jsonable(name_prefix="p")
    assert wps[0]["z"] == 7
    assert wps[1]["z"] == 8
    assert wps[0]["name"] == "p0000"
    assert wps[1]["name"] == "p0001"
