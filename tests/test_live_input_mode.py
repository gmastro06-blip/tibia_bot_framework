from __future__ import annotations

import types

import pytest


def test_input_guard_title_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    from input_guard import is_target_window_active

    # Monkeypatch the WinAPI call wrapper.
    import input_guard

    monkeypatch.setattr(input_guard, "get_foreground_window_title", lambda: "TibiaClone Harness - test")

    assert is_target_window_active(["TibiaClone"]) is True
    assert is_target_window_active(["harness"]) is True
    assert is_target_window_active(["something else"]) is False
    assert is_target_window_active([]) is False


def test_input_manager_blocks_when_not_armed(monkeypatch: pytest.MonkeyPatch) -> None:
    from action.input_driver import ActionRequest, MockInputDriver
    from action.input_manager import InputManager

    drv = MockInputDriver()
    mgr = InputManager(driver=drv, fallback=drv, injection_enabled=True)
    mgr.set_live_policy(live_input_armed=False, allowed_window_titles=["TibiaClone"])

    ok = mgr.send(ActionRequest(kind="move", value="north", note="committed"))
    assert ok is False
    assert mgr.last_block_reason == "not_armed"


def test_input_manager_blocks_preview_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from action.input_driver import ActionRequest, MockInputDriver
    import action.input_manager as im
    from action.input_manager import InputManager

    # Pretend the window is correct.
    monkeypatch.setattr(im, "is_target_window_active", lambda titles: True)

    drv = MockInputDriver()
    mgr = InputManager(driver=drv, fallback=drv, injection_enabled=True)
    mgr.set_live_policy(live_input_armed=True, allowed_window_titles=["TibiaClone"])

    ok = mgr.send(ActionRequest(kind="move", value="north", note="preview"))
    assert ok is False
    assert mgr.last_block_reason == "preview_only"


def test_input_manager_blocks_wrong_window(monkeypatch: pytest.MonkeyPatch) -> None:
    from action.input_driver import ActionRequest, MockInputDriver
    import action.input_manager as im
    from action.input_manager import InputManager

    monkeypatch.setattr(im, "get_foreground_window_title", lambda: "Some other window")
    monkeypatch.setattr(im, "is_target_window_active", lambda titles: False)

    drv = MockInputDriver()
    mgr = InputManager(driver=drv, fallback=drv, injection_enabled=True)
    mgr.set_live_policy(live_input_armed=True, allowed_window_titles=["TibiaClone"])

    ok = mgr.send(ActionRequest(kind="move", value="north", note="committed"))
    assert ok is False
    assert mgr.last_block_reason == "wrong_window"
    assert "window" in mgr.last_foreground_title.lower() or mgr.last_foreground_title


def test_input_manager_allows_committed_when_window_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    from action.input_driver import ActionRequest, MockInputDriver
    import action.input_manager as im
    from action.input_manager import InputManager

    monkeypatch.setattr(im, "is_target_window_active", lambda titles: True)

    drv = MockInputDriver()
    mgr = InputManager(driver=drv, fallback=drv, injection_enabled=True)
    mgr.set_live_policy(live_input_armed=True, allowed_window_titles=["TibiaClone"])

    ok = mgr.send(ActionRequest(kind="move", value="north", note="committed"))
    assert ok is True
    assert mgr.last_block_reason == ""
    assert drv.last() is not None
    assert drv.last().kind == "move"
