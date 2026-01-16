from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, List

from action.input_driver import ActionRequest


@dataclass(frozen=True)
class PlannedInput:
    """A low-level *planned* input (LOG-ONLY).

    This is intentionally NOT executed. It's used for:
    - debugging
    - operator review
    - correlating decisions to intended input sequences
    """

    kind: str  # e.g. "key", "hotkey", "macro", "note"
    value: str


def _env(name: str, default: str) -> str:
    try:
        v = os.getenv(name, "").strip()
        return v or default
    except Exception:
        return default


class InputPlanner:
    """Translate high-level ActionRequest(s) into LOG-ONLY PlannedInput(s)."""

    def plan_one(self, req: ActionRequest) -> List[PlannedInput]:
        kind = (req.kind or "").strip().lower()
        value = (req.value or "").strip()

        if kind == "wait":
            # Log-only: represent as an explicit wait.
            ms = 0
            try:
                ms = int(float(value)) if value else 0
            except Exception:
                ms = 0
            if ms <= 0:
                ms = 500
            return [PlannedInput(kind="wait", value=f"{ms}ms")]

        if kind == "note":
            if not value:
                return []
            return [PlannedInput(kind="note", value=value)]

        if kind == "beep":
            # Value can be empty (defaults) or "freq:dur".
            return [PlannedInput(kind="beep", value=value or "default")]

        if kind == "require":
            # Not an input; keep as a trace.
            if not value:
                return [PlannedInput(kind="note", value="require")]
            return [PlannedInput(kind="note", value=f"require:{value}")]

        if kind == "move":
            # Default: arrows. Can be overridden by env.
            mapping = {
                "north": _env("ASSIST_MOVE_NORTH", "Up"),
                "south": _env("ASSIST_MOVE_SOUTH", "Down"),
                "west": _env("ASSIST_MOVE_WEST", "Left"),
                "east": _env("ASSIST_MOVE_EAST", "Right"),
            }
            key = mapping.get(value.lower(), "")
            if not key:
                return [PlannedInput(kind="note", value=f"move:{value}")]
            return [PlannedInput(kind="key", value=key)]

        if kind in {"heal", "loot", "tool", "switch", "maintenance"}:
            # These already carry a hotkey/macro identifier in .value.
            # Keep it generic on purpose.
            if kind == "maintenance" and value.lower() in {"eat_food", "food"}:
                value = _env("ASSIST_EAT_FOOD", value)
            return [PlannedInput(kind="hotkey", value=value or kind)]

        if kind in {"target", "battlelist_target"}:
            # Target selection hotkey (commonly "next target").
            # Value may carry the desired target name for UI/telemetry; the
            # actual hotkey is configured externally.
            hk = _env("TARGET_HOTKEY", "")
            if hk:
                return [PlannedInput(kind="hotkey", value=hk)]
            return [PlannedInput(kind="note", value="target")]

        if kind in {"trade", "waypoint_action"}:
            # No universal input mapping; keep a trace.
            return [PlannedInput(kind="macro", value=f"{kind}:{value}")]

        if kind in {"npc_trade", "depot", "bank", "supplies"}:
            # Higher-level service intents (still assistant-only): keep a clear trace.
            return [PlannedInput(kind="macro", value=f"{kind}:{value}")]

        # Fallback.
        if kind or value:
            return [PlannedInput(kind="note", value=f"{kind}:{value}")]
        return []

    def plan_many(self, reqs: Iterable[ActionRequest]) -> List[PlannedInput]:
        out: List[PlannedInput] = []
        for r in reqs:
            try:
                out.extend(self.plan_one(r))
            except Exception:
                continue
        return out


def format_plan(plan: Iterable[PlannedInput]) -> str:
    parts: List[str] = []
    for p in plan:
        try:
            parts.append(f"{p.kind}:{p.value}")
        except Exception:
            continue
    return ";".join(parts)
