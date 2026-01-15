from __future__ import annotations

from action.input_driver import ActionRequest, MockInputDriver
from action.input_manager import InputManager


def test_input_manager_blocks_when_focus_guard_denies(monkeypatch) -> None:
    class DummyWin:
        @staticmethod
        def get_foreground_hwnd() -> int:
            return 0

        @staticmethod
        def get_window_title(_hwnd: int) -> str:
            return ""

    import action.input_manager as im

    monkeypatch.setattr(im, "win_window", DummyWin, raising=False)
    monkeypatch.setattr(im, "get_client_hwnd", lambda: 123, raising=False)
    monkeypatch.setattr(im, "is_allowed_to_inject", lambda hwnd: (False, "not_foreground"), raising=False)

    drv = MockInputDriver(max_items=10)
    mgr = InputManager(driver=drv, fallback=drv, injection_enabled=True)
    mgr.set_live_policy(live_input_armed=True, allowed_window_titles=["Tibia"])

    ok = mgr.send(ActionRequest(kind="move", value="north", note="committed"))
    assert ok is False
    assert mgr.last_block_reason == "not_foreground"


def test_input_manager_allows_when_focus_guard_allows(monkeypatch) -> None:
    class DummyWin:
        @staticmethod
        def get_foreground_hwnd() -> int:
            return 123

        @staticmethod
        def get_window_title(_hwnd: int) -> str:
            return "Tibia"

    import action.input_manager as im

    monkeypatch.setattr(im, "win_window", DummyWin, raising=False)
    monkeypatch.setattr(im, "get_client_hwnd", lambda: 123, raising=False)
    monkeypatch.setattr(im, "is_allowed_to_inject", lambda hwnd: (True, "ok"), raising=False)

    drv = MockInputDriver(max_items=10)
    mgr = InputManager(driver=drv, fallback=drv, injection_enabled=True)
    mgr.set_live_policy(live_input_armed=True, allowed_window_titles=["Tibia"])

    ok = mgr.send(ActionRequest(kind="move", value="north", note="committed"))
    assert ok is True
    assert mgr.last_block_reason == ""
    assert drv.last() is not None
