import pytest

from action.input_driver import ActionRequest, MockInputDriver, WindowsKeyboardDriver, is_committed


def test_is_committed_true_only_for_committed_note() -> None:
    assert is_committed(ActionRequest(kind="move", value="north", note="committed")) is True
    assert is_committed(ActionRequest(kind="move", value="north", note="Committed")) is True
    assert is_committed(ActionRequest(kind="move", value="north", note=" preview ")) is False
    assert is_committed(ActionRequest(kind="move", value="north", note="")) is False


def test_mock_driver_records_preview_actions() -> None:
    drv = MockInputDriver(max_items=10)
    preview = ActionRequest(kind="move", value="north", note="preview")
    assert drv.send(preview) is True
    assert drv.last() == preview


def test_windows_keyboard_driver_blocks_preview_actions() -> None:
    # Even if hotkeys are configured, preview must not inject.
    drv = WindowsKeyboardDriver(target_hotkey="F1", minimap_hotkey="CTRL+L")
    preview = ActionRequest(kind="target", value="orc", note="preview")
    assert drv.send(preview) is False


@pytest.mark.parametrize(
    "kind,value",
    [
        ("target", "orc"),
        ("minimap_click_sim", "center_tile"),
        ("heal", "F1"),
        ("move", "north"),
    ],
)
def test_windows_keyboard_driver_returns_bool_for_committed(kind: str, value: str) -> None:
    drv = WindowsKeyboardDriver(target_hotkey="F1", minimap_hotkey="CTRL+L")
    committed = ActionRequest(kind=kind, value=value, note="committed")
    out = drv.send(committed)
    assert isinstance(out, bool)
