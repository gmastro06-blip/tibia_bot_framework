from __future__ import annotations

from types import SimpleNamespace

import numpy as np


def test_dxgi_capture_minimized_falls_back_to_fullscreen(monkeypatch):
    """When the chosen capture target is minimized, capture() should still return frames.

    This prevents the vision thread from starving (STALE_GS).
    """

    from capture.dxgi_capture import DXGICapture
    import capture.dxgi_capture as dxgi_capture

    # Avoid touching real windows in unit tests.
    monkeypatch.setattr(DXGICapture, "find_window", lambda self: 0)

    st = SimpleNamespace(
        capture_backend="dxgi",
        capture_target="auto",
        target_hwnd=123,
        target_title="Proyector en ventana (Fuente) - Tibia_Fuente",
        target_bounds=(0, 0, 640, 360),
        target_found=True,
        reason="obs_projector",
        target_is_minimized=True,
        target_is_maximized=False,
    )

    monkeypatch.setattr(dxgi_capture, "update_capture_target_state", lambda: st)

    # Ensure we don't call MSS/BitBlt in this unit test.
    frame = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)
    monkeypatch.setattr(DXGICapture, "capture_fullscreen", lambda self, preferred_monitor=None: frame)

    cap = DXGICapture(title_partial="Tibia")
    out = cap.capture()

    assert out is not None
    assert out.shape[0] >= 1 and out.shape[1] >= 1
