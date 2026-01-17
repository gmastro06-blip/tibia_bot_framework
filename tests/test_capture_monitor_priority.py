from capture.dxgi_capture import _build_monitor_priority


def test_obs_mode_fallback_monitor_when_no_hints() -> None:
    order = _build_monitor_priority(
        n_monitors=3,  # indices: 0,1,2
        preferred_monitor=None,
        last_good_monitor=None,
        window_monitor=None,
        obs_mode=True,
        obs_fallback_monitor=1,
        scan_active_monitor=True,
        active_monitor=2,
    )
    # OBS fallback (monitor 1) should win; active monitor should NOT be used
    # when an OBS fallback exists.
    assert order[0] == 1
    assert 2 in order
    assert order.index(1) < order.index(2)


def test_active_monitor_only_used_when_no_hints() -> None:
    order = _build_monitor_priority(
        n_monitors=4,
        preferred_monitor=2,
        last_good_monitor=None,
        window_monitor=None,
        obs_mode=False,
        obs_fallback_monitor=1,
        scan_active_monitor=True,
        active_monitor=3,
    )
    # Preferred monitor should stay first; active heuristic should not jump ahead.
    assert order[0] == 2
    assert 3 in order
    assert order.index(2) < order.index(3)


def test_fills_all_monitors_and_virtual_last() -> None:
    order = _build_monitor_priority(
        n_monitors=3,
        preferred_monitor=2,
        last_good_monitor=None,
        window_monitor=None,
        obs_mode=False,
        obs_fallback_monitor=None,
        scan_active_monitor=False,
        active_monitor=None,
    )
    assert order[-1] == 0
    assert set(order) == {0, 1, 2}
