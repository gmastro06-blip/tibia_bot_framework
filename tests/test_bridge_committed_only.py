from __future__ import annotations

from action.input_driver import ActionRequest
from action.input_bridge_driver import InputBridgeDriver


class FakeBridgeClient:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_key_press(self, key: str) -> bool:
        self.sent.append(key)
        return True


def test_bridge_committed_only() -> None:
    client = FakeBridgeClient()
    driver = InputBridgeDriver(client=client, target_hotkey="F1")

    preview = ActionRequest(kind="target", value="F1", note="preview")
    committed = ActionRequest(kind="target", value="F1", note="committed")

    assert driver.send(preview) is False
    assert driver.send(committed) is True
    assert client.sent == ["F1"]
