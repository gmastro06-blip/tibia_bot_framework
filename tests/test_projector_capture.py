import numpy as np


class _FakeMSS:
    def __init__(self, monitors: list[dict]):
        self.monitors = monitors

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _fake_mss_factory(monitors: list[dict]):
    def _mss():
        return _FakeMSS(monitors)

    return _mss


def _valid_frame(w: int = 160, h: int = 120) -> np.ndarray:
    # Non-uniform, non-black frame that passes validate_capture(min_width=80,min_height=80)
    rng = np.random.default_rng(123)
    return rng.integers(0, 255, size=(h, w, 3), dtype=np.uint8)


def test_projector_forced_monitor_autoadjust(monkeypatch):
    from capture.dxgi_capture import DXGICapture
    import capture.dxgi_capture as dx

    monkeypatch.setenv("CAPTURE_TARGET", "projector")
    monkeypatch.setenv("CAPTURE_PROJECTOR_TITLE", "Proyector en ventana")

    # Stable MSS monitor inventory for deterministic tests.
    monkeypatch.setattr(
        dx,
        "mss",
        _fake_mss_factory(
            [
                {"left": 0, "top": 0, "width": 100, "height": 100},
                {"left": 0, "top": 0, "width": 100, "height": 100},
                {"left": 0, "top": 0, "width": 100, "height": 100},
            ]
        ),
    )

    # Avoid touching real window discovery.
    monkeypatch.setattr(DXGICapture, "find_window", lambda self: 0)

    cap = DXGICapture(force_monitor=1, strict_force_monitor=True)
    cap._startup_monitors_logged = True
    cap._startup_forced_monitor_validated = True

    # Projector exists on monitor 2 even if forced_monitor=1.
    monkeypatch.setattr(
        cap,
        "_list_visible_windows",
        lambda: [(123, "Proyector en ventana (Fuente) - Tibia_Fuente", 999)],
    )
    monkeypatch.setattr(cap, "find_window_monitor", lambda hwnd: 2)
    monkeypatch.setattr(cap, "capture_window", lambda hwnd=None: _valid_frame())

    frame = cap.capture()
    assert frame is not None
    assert int(cap.force_monitor) == 2
    assert str(cap.capture_state).startswith("projector@mon2")


def test_projector_never_sets_backend_null(monkeypatch):
    from capture.dxgi_capture import DXGICapture
    import capture.dxgi_capture as dx

    monkeypatch.setenv("CAPTURE_TARGET", "projector")
    monkeypatch.setenv("CAPTURE_PROJECTOR_TITLE", "Proyector")

    monkeypatch.setattr(
        dx,
        "mss",
        _fake_mss_factory(
            [
                {"left": 0, "top": 0, "width": 100, "height": 100},
                {"left": 0, "top": 0, "width": 100, "height": 100},
                {"left": 0, "top": 0, "width": 100, "height": 100},
            ]
        ),
    )
    monkeypatch.setattr(DXGICapture, "find_window", lambda self: 0)

    cap = DXGICapture(force_monitor=1, strict_force_monitor=True)
    cap._startup_monitors_logged = True
    cap._startup_forced_monitor_validated = True

    monkeypatch.setattr(
        cap,
        "_list_visible_windows",
        lambda: [(777, "Proyector en ventana (Fuente) - Tibia_Fuente", 0)],
    )
    monkeypatch.setattr(cap, "find_window_monitor", lambda hwnd: 2)
    monkeypatch.setattr(cap, "capture_bounds", (0, 0, 200, 200))

    calls = {"n": 0}

    def _next_frame():
        calls["n"] += 1
        if calls["n"] <= 5:
            return np.zeros((120, 160, 3), dtype=np.uint8)
        return _valid_frame()

    monkeypatch.setattr(cap, "capture_window", lambda hwnd=None: _next_frame())
    monkeypatch.setattr(cap, "capture_screen_crop", lambda bounds: _next_frame())

    got = None
    for _ in range(12):
        got = cap.capture()
        if got is not None:
            break

    assert got is not None
    assert str(cap.capture_backend or "").strip().lower() not in {"", "null", "none"}
    assert str(cap.capture_state).startswith("projector@mon2")
