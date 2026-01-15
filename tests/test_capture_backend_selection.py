from __future__ import annotations

import sys
import types

import pytest


class DummyDXGI:
    def __init__(self, *, force_monitor: int):
        self.force_monitor = int(force_monitor)


def test_capture_backend_default_dxgi(monkeypatch: pytest.MonkeyPatch) -> None:
    import main

    monkeypatch.setattr(main, "DXGICapture", DummyDXGI)
    monkeypatch.delenv("CAPTURE_BACKEND", raising=False)

    cap = main.build_capture_backend(force_monitor=7)
    assert isinstance(cap, DummyDXGI)
    assert cap.force_monitor == 7


def test_capture_backend_unknown_falls_back_dxgi(monkeypatch: pytest.MonkeyPatch) -> None:
    import main

    monkeypatch.setattr(main, "DXGICapture", DummyDXGI)
    monkeypatch.setenv("CAPTURE_BACKEND", "something_else")

    cap = main.build_capture_backend(force_monitor=2)
    assert isinstance(cap, DummyDXGI)
    assert cap.force_monitor == 2


def test_capture_backend_obs_import_error_falls_back_dxgi(monkeypatch: pytest.MonkeyPatch) -> None:
    import main

    monkeypatch.setattr(main, "DXGICapture", DummyDXGI)
    monkeypatch.setenv("CAPTURE_BACKEND", "obs_websocket")

    # Force an import failure by providing a module without OBSWebSocketCapture.
    sys.modules["capture.obs_websocket_capture"] = types.ModuleType("capture.obs_websocket_capture")

    cap = main.build_capture_backend(force_monitor=3)
    assert isinstance(cap, DummyDXGI)
    assert cap.force_monitor == 3


def test_capture_backend_obs_connect_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    import main

    monkeypatch.setattr(main, "DXGICapture", DummyDXGI)
    monkeypatch.setenv("CAPTURE_BACKEND", "obs")

    class DummyOBS:
        def __init__(
            self,
            *,
            host: str,
            port: int,
            password: str,
            capture_method: str,
            source_name: str,
        ) -> None:
            self.host = host
            self.port = int(port)
            self.password = password
            self.capture_method = capture_method
            self.source_name = source_name
            self.connected = False

        def connect(self) -> bool:
            self.connected = True
            return True

    mod = types.ModuleType("capture.obs_websocket_capture")
    mod.OBSWebSocketCapture = DummyOBS  # type: ignore[attr-defined]
    sys.modules["capture.obs_websocket_capture"] = mod

    monkeypatch.setenv("OBS_HOST", "example")
    monkeypatch.setenv("OBS_PORT", "1234")
    monkeypatch.setenv("OBS_PASSWORD", "pw")
    monkeypatch.setenv("OBS_CAPTURE_METHOD", "obs_source")
    monkeypatch.setenv("OBS_SOURCE_NAME", "Tibia_Fuente")

    cap = main.build_capture_backend(force_monitor=9)
    assert isinstance(cap, DummyOBS)
    assert cap.connected is True
    assert cap.host == "example"
    assert cap.port == 1234


def test_capture_backend_virtualcam_connect_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    import main

    monkeypatch.setattr(main, "DXGICapture", DummyDXGI)
    monkeypatch.setenv("CAPTURE_BACKEND", "virtualcam")

    class DummyVC:
        def __init__(self, *, camera_index: int) -> None:
            self.camera_index = int(camera_index)
            self.connected = False

        def connect(self) -> bool:
            self.connected = True
            return True

    mod = types.ModuleType("capture.virtualcam_capture")
    mod.VirtualCamCapture = DummyVC  # type: ignore[attr-defined]
    sys.modules["capture.virtualcam_capture"] = mod

    monkeypatch.setenv("VIRTUALCAM_INDEX", "5")

    cap = main.build_capture_backend(force_monitor=1)
    assert isinstance(cap, DummyVC)
    assert cap.connected is True
    assert cap.camera_index == 5
