from __future__ import annotations

from input_bridge import validate_command


def test_protocol_requires_token() -> None:
    cmd = {"type": "key", "action": "press", "key": "F1", "token": "bad"}
    ok, reason = validate_command(cmd, token="secret")
    assert not ok
    assert reason == "bad_token"


def test_protocol_accepts_key_press() -> None:
    cmd = {"type": "key", "action": "press", "key": "F1", "token": "secret"}
    ok, reason = validate_command(cmd, token="secret")
    assert ok
    assert reason == "ok"


def test_protocol_accepts_mouse_click() -> None:
    cmd = {"type": "mouse", "action": "click", "button": "left", "x": 10, "y": 20, "token": "secret"}
    ok, reason = validate_command(cmd, token="secret")
    assert ok
    assert reason == "ok"


def test_protocol_kill_switch() -> None:
    cmd = {"type": "control", "action": "disable", "token": "secret"}
    ok, reason = validate_command(cmd, token="secret")
    assert ok
    assert reason == "ok"
