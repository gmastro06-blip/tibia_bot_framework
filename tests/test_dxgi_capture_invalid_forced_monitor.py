from __future__ import annotations

from types import SimpleNamespace

import numpy as np


def test_dxgi_capture_invalid_forced_monitor_disables_strict_and_falls_back(monkeypatch):
    """If FORCE_MONITOR is out of range, capture must auto-disable strict pin.

    This prevents starving the pipeline (STALE_GS) when the operator misconfigures
    FORCE_MONITOR (e.g., forced_monitor=99).
    """

    from capture.dxgi_capture import DXGICapture
    import capture.dxgi_capture as dxgi_capture

    # Avoid touching real windows in unit tests.
    monkeypatch.setattr(DXGICapture, "find_window", lambda self: 0)

    # Fake MSS monitors (indices 0..2 valid).
    class _FakeMSS:
        def __init__(self):
            self.monitors = [
                {"left": 0, "top": 0, "width": 1920, "height": 1080},
                {"left": 0, "top": 0, "width": 1920, "height": 1080},
                {"left": 1920, "top": 0, "width": 1920, "height": 1080},
            ]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(dxgi_capture, "mss", lambda: _FakeMSS())

    # Avoid focus-guard integration.
    st = SimpleNamespace(
        capture_backend="dxgi",
        capture_target="auto",
        target_hwnd=1,
        target_title="Tibia",
        target_bounds=None,
        target_found=True,
        reason="client",
        target_is_minimized=False,
        target_is_maximized=False,
    )
    monkeypatch.setattr(dxgi_capture, "update_capture_target_state", lambda: st)

    # Ensure we do NOT attempt capture_specific_monitor when the forced monitor is invalid.
    monkeypatch.setattr(DXGICapture, "capture_specific_monitor", lambda self, i: (_ for _ in ()).throw(AssertionError("should not be called")))

    # Provide a safe fullscreen fallback frame.
    frame = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)
    monkeypatch.setattr(DXGICapture, "capture_fullscreen", lambda self, preferred_monitor=None: frame)

    cap = DXGICapture(title_partial="Tibia", force_monitor=99, strict_force_monitor=True)

    out = cap.capture()

    assert cap.strict_force_monitor is False
    assert cap.force_monitor is None
    assert out is not None
