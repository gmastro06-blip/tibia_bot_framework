from __future__ import annotations

from decision.behavior_tree import BehaviorTreeRunner
from decision.signals import SignalResult
from runtime_config import HealingConfig


def test_bt_healing_move_waypoint_food() -> None:
    bt = BehaviorTreeRunner()

    sig = SignalResult(
        hp_current=100,
        hp_max=200,
        hp_pct=50.0,
        mp_current=50,
        mp_max=100,
        mp_pct=50.0,
        low_hp=True,
        low_mp=False,
        paralyzed=None,
        haste_active=None,
        utamo_active=None,
        hungry=None,
        healing_trigger=True,
    )

    heal_cfg = HealingConfig(enabled=True, hp_below_pct=70, mp_below_pct=30, action="exura")

    reqs = bt.tick(
        sig=sig,
        healing_cfg=heal_cfg,
        cavebot_next="north",
        cavebot_action="loot;rope",
        commit_flag=True,
        eat_food=True,
    )

    # Order matters (Sequence): heal -> move -> waypoint actions -> food
    assert len(reqs) >= 5
    assert reqs[0].kind == "heal" and reqs[0].value == "exura" and reqs[0].note == "preview"
    assert reqs[1].kind == "move" and reqs[1].value == "north" and reqs[1].note == "committed"

    # Waypoint parsed actions
    kinds = [r.kind for r in reqs]
    assert "loot" in kinds
    assert "tool" in kinds

    # Maintenance
    assert any(r.kind == "maintenance" and r.value == "eat_food" for r in reqs)


def test_bt_disabled_conditions_produce_no_actions() -> None:
    bt = BehaviorTreeRunner()

    sig = SignalResult(
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

    heal_cfg = HealingConfig(enabled=False, action="exura")

    reqs = bt.tick(
        sig=sig,
        healing_cfg=heal_cfg,
        cavebot_next="",
        cavebot_action="",
        commit_flag=False,
        eat_food=False,
    )

    assert reqs == []
