from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from action.input_driver import ActionRequest


def _norm(s: str) -> str:
    return (s or "").strip().lower()


@dataclass(frozen=True)
class WaypointActionConfig:
    quick_loot_hotkey: str = "ctrl+l"
    rope_hotkey: str = "rope"
    shovel_hotkey: str = "shovel"
    weapon_switch: str = "weapon_switch"


def config_from_env() -> WaypointActionConfig:
    return WaypointActionConfig(
        quick_loot_hotkey=os.getenv("ASSIST_QUICK_LOOT", "ctrl+l").strip() or "ctrl+l",
        rope_hotkey=os.getenv("ASSIST_TOOL_ROPE", "rope").strip() or "rope",
        shovel_hotkey=os.getenv("ASSIST_TOOL_SHOVEL", "shovel").strip() or "shovel",
        weapon_switch=os.getenv("ASSIST_ANTITRAP_SWITCH", "weapon_switch").strip() or "weapon_switch",
    )


def build_requests_from_waypoint_action(
    action: Optional[str],
    *,
    committed: bool,
    cfg: Optional[WaypointActionConfig] = None,
) -> List[ActionRequest]:
    """Parse Waypoint.action and turn it into mock ActionRequest(s).

    Supported examples (case-insensitive):
    - "loot" or "quick_loot" -> ActionRequest(kind="loot", value="ctrl+l")
    - "rope" / "shovel" -> ActionRequest(kind="tool", value="rope")
    - "antitrap" / "weapon_switch" -> ActionRequest(kind="switch", value="weapon_switch")
    - "buy_potions" / "sell_potions" -> ActionRequest(kind="trade", value="buy_potions")

    Multiple actions can be separated by ';' or '|'.
    """

    if not action:
        return []

    c = cfg or config_from_env()
    note = "committed" if committed else "preview"

    parts = []
    raw = str(action)
    for sep in (";", "|"):
        raw = raw.replace(sep, ";")
    for p in raw.split(";"):
        p = _norm(p)
        if p:
            parts.append(p)

    out: List[ActionRequest] = []
    for p in parts:
        if p in {"loot", "quick_loot", "ql"}:
            out.append(ActionRequest(kind="loot", value=c.quick_loot_hotkey, note=note))
        elif p in {"rope", "use_rope"}:
            out.append(ActionRequest(kind="tool", value=c.rope_hotkey, note=note))
        elif p in {"shovel", "use_shovel"}:
            out.append(ActionRequest(kind="tool", value=c.shovel_hotkey, note=note))
        elif p in {"antitrap", "weapon_switch", "switch_weapon"}:
            out.append(ActionRequest(kind="switch", value=c.weapon_switch, note=note))
        elif p in {"sell_potions", "buy_potions"}:
            out.append(ActionRequest(kind="trade", value=p, note=note))
        else:
            # Pass-through for custom labels.
            out.append(ActionRequest(kind="waypoint_action", value=p, note=note))

    return out
