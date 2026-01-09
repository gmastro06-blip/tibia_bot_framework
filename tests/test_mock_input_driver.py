from __future__ import annotations

from action.input_driver import ActionRequest, MockInputDriver


def test_mock_input_driver_records_actions() -> None:
    d = MockInputDriver(max_items=3)
    assert d.last() is None

    assert d.send(ActionRequest(kind="move", value="north")) is True
    assert d.last() == ActionRequest(kind="move", value="north", note="")

    d.send(ActionRequest(kind="move", value="east"))
    d.send(ActionRequest(kind="move", value="south"))
    d.send(ActionRequest(kind="move", value="west"))

    # max_items=3 => dropped the oldest (north)
    assert len(d.actions) == 3
    assert d.actions[0].value == "east"
    assert d.actions[-1].value == "west"
