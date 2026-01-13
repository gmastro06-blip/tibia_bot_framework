from __future__ import annotations

from action.input_driver import ActionRequest
from action.input_planner import InputPlanner, format_plan


def test_plan_move_defaults_to_arrows() -> None:
    p = InputPlanner()
    plan = p.plan_one(ActionRequest(kind="move", value="north"))
    assert format_plan(plan) == "key:Up"


def test_plan_heal_is_hotkey() -> None:
    p = InputPlanner()
    plan = p.plan_one(ActionRequest(kind="heal", value="F1"))
    assert format_plan(plan) == "hotkey:F1"


def test_plan_waypoint_action_is_macro_trace() -> None:
    p = InputPlanner()
    plan = p.plan_one(ActionRequest(kind="waypoint_action", value="rope"))
    assert format_plan(plan) == "macro:waypoint_action:rope"


def test_plan_many_concatenates() -> None:
    p = InputPlanner()
    plan = p.plan_many(
        [
            ActionRequest(kind="move", value="west"),
            ActionRequest(kind="loot", value="ctrl+l"),
        ]
    )
    assert format_plan(plan) == "key:Left;hotkey:ctrl+l"


def test_plan_service_actions_are_macro_traces() -> None:
    p = InputPlanner()
    plan = p.plan_one(ActionRequest(kind="depot", value="deposit"))
    assert format_plan(plan) == "macro:depot:deposit"
