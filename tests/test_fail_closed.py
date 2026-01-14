import threading

from action.input_driver import ActionRequest, MockInputDriver
from action.input_manager import InputManager
from fail_closed import fail_closed


class _FakeDriver:
    def __init__(self) -> None:
        self.sent: list[ActionRequest] = []

    def send(self, action: ActionRequest) -> bool:
        self.sent.append(action)
        return True


def test_input_manager_disable_switches_to_fallback_and_blocks_injection() -> None:
    fallback = MockInputDriver(max_items=10)
    fake = _FakeDriver()

    mgr = InputManager(driver=fake, fallback=fallback, injection_enabled=True)

    preview = ActionRequest(kind="move", value="north", note="preview")
    # injection-enabled should block preview
    assert mgr.send(preview) is False
    assert fake.sent == []

    mgr.disable("TEST")
    assert mgr.injection_enabled is False
    assert mgr.driver is fallback

    # After disable, we should be able to record (safe) actions via fallback.
    assert mgr.send(preview) is True
    assert fallback.last() == preview


def test_fail_closed_sets_stop_event_and_disables_manager() -> None:
    stop_event = threading.Event()
    fallback = MockInputDriver(max_items=10)
    fake = _FakeDriver()
    mgr = InputManager(driver=fake, fallback=fallback, injection_enabled=True)

    fail_closed(stop_event=stop_event, input_manager=mgr, reason="TEST", set_stop=True)

    assert stop_event.is_set() is True
    assert mgr.injection_enabled is False
    assert mgr.driver is fallback
