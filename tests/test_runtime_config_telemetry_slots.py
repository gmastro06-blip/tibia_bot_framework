from src.runtime_config import RuntimeConfig


def test_telemetry_ring_amulet_roundtrip() -> None:
    rc = RuntimeConfig()
    rc.update_telemetry(ring_equipped=True, amulet_equipped=False)
    tel = rc.telemetry_snapshot()
    assert tel.ring_equipped is True
    assert tel.amulet_equipped is False
