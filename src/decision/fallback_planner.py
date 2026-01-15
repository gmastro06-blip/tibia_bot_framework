from __future__ import annotations

from typing import Any, List

from action.input_driver import ActionRequest
from decision.waypoint_actions import build_requests_from_waypoint_action


def build_fallback_action_requests(
    healing_cfg: Any,
    heal_hp: bool,
    heal_mp: bool,
    heal_committed: bool,
    target_cls: str = "",
    cavebot_next: str = "",
    cavebot_action: str = "",
    commit_flag: bool = False,
    eat_food: bool = False,
) -> List[ActionRequest]:
    """Pre-BT fallback planner with a stable priority order.

    Priority order (requested): Healing → Targeting → Cavebot.

    Notes:
    - Returns ActionRequest objects only (no OS input injection).
    - Waypoint actions are treated as part of cavebot (after move).
    - Maintenance (eat food) is appended last.
    """

    out: List[ActionRequest] = []

    # 1) Healing
    heal_note = "committed" if bool(heal_committed) else "preview"
    try:
        if healing_cfg is not None and bool(getattr(healing_cfg, "enabled", False)):
            if bool(heal_hp):
                act_hp = (
                    str(getattr(healing_cfg, "hp_action", "") or getattr(healing_cfg, "action", "") or "heal")
                    .strip()
                    or "heal"
                )
                out.append(ActionRequest(kind="heal", value=act_hp, note=heal_note))
            if bool(heal_mp):
                act_mp = (
                    str(getattr(healing_cfg, "mp_action", "") or getattr(healing_cfg, "action", "") or "heal")
                    .strip()
                    or "heal"
                )
                out.append(ActionRequest(kind="heal", value=act_mp, note=heal_note))
    except Exception:
        # Fail-safe: never crash planning.
        pass

    # 2) Targeting
    try:
        cls = str(target_cls or "").strip().lower()
        if cls:
            out.append(ActionRequest(kind="target", value=cls, note="preview"))
    except Exception:
        pass

    # 3) Cavebot movement
    try:
        if str(cavebot_next or "").strip().lower() in {"north", "south", "east", "west"}:
            note = "committed" if bool(commit_flag) else "preview"
            out.append(ActionRequest(kind="move", value=str(cavebot_next), note=note))
    except Exception:
        pass

    # 4) Cavebot waypoint actions (loot/tools/trade/etc.)
    try:
        if str(cavebot_action or "").strip():
            out.extend(build_requests_from_waypoint_action(cavebot_action, committed=bool(commit_flag)))
    except Exception:
        pass

    # 5) Maintenance
    try:
        if bool(eat_food):
            out.append(ActionRequest(kind="maintenance", value="eat_food", note="preview"))
    except Exception:
        pass

    return out
