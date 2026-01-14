from __future__ import annotations

from typing import Any

from action.input_driver import ActionRequest
from decision.action_planner import plan_action_requests
from decision.signals import SignalResult
from runtime_config import HealingConfig


class _SpyRunner:
    def __init__(self) -> None:
        self.called = 0

    def tick(self, **kwargs: Any) -> list[ActionRequest]:
        self.called += 1
        return [ActionRequest(kind="maintenance", value="eat_food", note="preview")]


class _FailingRunner:
    def __init__(self) -> None:
        self.called = 0

    def tick(self, **kwargs: Any) -> list[ActionRequest]:
        self.called += 1
        raise RuntimeError("boom")


def _base_sig() -> SignalResult:
    return SignalResult(
        hp_current=100,
        hp_max=200,
        hp_pct=50.0,
        mp_current=50,
        mp_max=100,
        mp_pct=50.0,
        low_hp=False,
        low_mp=False,
        paralyzed=None,
        haste_active=None,
        utamo_active=None,
        hungry=None,
        healing_trigger=False,
    )


def test_bt_enabled_invokes_runner() -> None:
    runner = _SpyRunner()

    fallback_called = {"n": 0}

    def fallback() -> list[ActionRequest]:
        fallback_called["n"] += 1
        return [ActionRequest(kind="heal", value="exura", note="preview")]

    sig = _base_sig()
    heal_cfg = HealingConfig(enabled=True, hp_below_pct=70, mp_below_pct=30, action="exura")

    reqs, src = plan_action_requests(
        bt_enabled=True,
        bt_runner=runner,
        fallback=fallback,
        sig=sig,
        healing_cfg=heal_cfg,
        heal_hp=False,
        heal_mp=False,
        battlelist_target="",
        battlelist_conf=None,
        target_cls="",
        target_conf=None,
        cavebot_next="",
        cavebot_action="",
        commit_flag=False,
        eat_food=False,
    )

    assert runner.called == 1
    assert fallback_called["n"] == 0
    assert src == "bt"
    assert reqs and reqs[0].kind == "maintenance"


def test_bt_runner_exception_falls_back_without_inputs() -> None:
    runner = _FailingRunner()

    def fallback() -> list[ActionRequest]:
        # Fallback planner returns preview-only actions; no OS injection.
        return [ActionRequest(kind="move", value="north", note="preview")]

    sig = _base_sig()
    heal_cfg = HealingConfig(enabled=False, action="")

    reqs, src = plan_action_requests(
        bt_enabled=True,
        bt_runner=runner,
        fallback=fallback,
        sig=sig,
        healing_cfg=heal_cfg,
        heal_hp=False,
        heal_mp=False,
        battlelist_target="",
        battlelist_conf=None,
        target_cls="",
        target_conf=None,
        cavebot_next="north",
        cavebot_action="",
        commit_flag=False,
        eat_food=False,
    )

    assert runner.called == 1
    assert src == "fallback_exception"
    assert reqs and reqs[0].kind == "move"
    assert all(r.note != "committed" for r in reqs)
