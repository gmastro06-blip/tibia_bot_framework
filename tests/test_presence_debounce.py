def test_debounced_boolean_debounces_and_decays_true_to_none() -> None:
    from src.gamestate.builder import DebouncedBoolean

    d = DebouncedBoolean(on_frames=2, off_frames=2, max_hold_ms=700)

    # Needs 2 consecutive True observations to become True.
    assert d.update(True, now_ts=0.0) is None
    assert d.update(True, now_ts=0.1) is True

    # Needs 2 consecutive False observations to become False.
    assert d.update(False, now_ts=0.2) is True
    assert d.update(False, now_ts=0.3) is False

    # Become True again with 2 trues.
    assert d.update(True, now_ts=0.4) is False
    assert d.update(True, now_ts=0.5) is True

    # Unknown input should not stick forever: after max_hold_ms, True -> None.
    assert d.update(None, now_ts=1.1) is True   # 600ms after last_true_ts=0.5
    assert d.update(None, now_ts=1.3) is None   # 800ms after last_true_ts=0.5


def test_update_telemetry_clears_presence_fields_to_none() -> None:
    from src.runtime_config import RuntimeConfig

    cfg = RuntimeConfig()

    cfg.update_telemetry(ring_equipped=True, amulet_equipped=True, hungry=True)
    snap = cfg.telemetry_snapshot()
    assert snap.ring_equipped is True
    assert snap.amulet_equipped is True
    assert snap.hungry is True

    # Explicit None should clear stored values (no-stickiness).
    cfg.update_telemetry(ring_equipped=None, amulet_equipped=None, hungry=None)
    snap2 = cfg.telemetry_snapshot()
    assert snap2.ring_equipped is None
    assert snap2.amulet_equipped is None
    assert snap2.hungry is None
