from action.keyboard import EXTENDED_KEYS, SCANCODES


def test_arrow_keys_are_supported_scancodes() -> None:
    for k in ["UP", "DOWN", "LEFT", "RIGHT"]:
        assert k in SCANCODES
        assert k in EXTENDED_KEYS
