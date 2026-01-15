from __future__ import annotations

from runtime_config import HealingConfig

from decision.fallback_planner import build_fallback_action_requests


def test_fallback_planner_priority_heal_then_target_then_cavebot() -> None:
    heal_cfg = HealingConfig(enabled=True, hp_below_pct=70, mp_below_pct=30, action="exura")

    reqs = build_fallback_action_requests(
        healing_cfg=heal_cfg,
        heal_hp=True,
        heal_mp=False,
        heal_committed=False,
        target_cls="orc",
        cavebot_next="north",
        cavebot_action="loot;rope",
        commit_flag=True,
        eat_food=True,
    )

    assert [r.kind for r in reqs[:3]] == ["heal", "target", "move"]

    # Cavebot actions are after movement.
    kinds = [r.kind for r in reqs]
    assert "loot" in kinds
    assert "tool" in kinds

    # Maintenance is last (or at least after cavebot actions).
    maint_i = next(i for i, r in enumerate(reqs) if r.kind == "maintenance")
    move_i = next(i for i, r in enumerate(reqs) if r.kind == "move")
    assert maint_i > move_i
