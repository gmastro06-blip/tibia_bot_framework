from __future__ import annotations


def test_capture_trace_snapshot_has_reason_and_never_nullish() -> None:
    import main

    class DummyCapture:
        # Intentionally missing many fields to validate defaults.
        pass

    snap = main._capture_trace_snapshot(DummyCapture(), is_foreground=False)

    assert isinstance(snap, dict)

    # Required keys (schema-lite).
    for k in [
        "backend",
        "capture_target",
        "target_title",
        "target_hwnd",
        "state",
        "monitor_index",
        "target_found",
        "reason",
        "fail_count",
        "reacquire_count",
        "last_error",
        "is_foreground",
    ]:
        assert k in snap

    # Ensure the most important fields are strings, not None.
    assert isinstance(snap["backend"], str) and snap["backend"]
    assert isinstance(snap["state"], str) and snap["state"]
    assert isinstance(snap["reason"], str)
    assert isinstance(snap["last_error"], str)
