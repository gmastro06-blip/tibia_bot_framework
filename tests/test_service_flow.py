from action.input_driver import ActionRequest
from decision.service_flow import expand_service_requests


def test_service_flow_adds_note_and_beep_for_committed() -> None:
    base = [ActionRequest(kind="depot", value="deposit", note="committed")]
    out = expand_service_requests(base)

    assert [r.kind for r in out] == ["note", "beep", "depot"]
    assert out[0].value == "depot:deposit"
    assert out[1].value == "service_flow"
    assert out[2].value == "deposit"


def test_service_flow_preview_only_note() -> None:
    base = [ActionRequest(kind="npc_trade", value="buy_potions", note="preview")]
    out = expand_service_requests(base)

    assert [r.kind for r in out] == ["note", "npc_trade"]
    assert out[0].value == "npc_trade:buy_potions"
    assert out[1].value == "buy_potions"


def test_service_flow_passes_through_non_service() -> None:
    base = [ActionRequest(kind="move", value="north", note="preview")]
    out = expand_service_requests(base)

    assert len(out) == 1
    assert out[0].kind == "move"
    assert out[0].value == "north"
