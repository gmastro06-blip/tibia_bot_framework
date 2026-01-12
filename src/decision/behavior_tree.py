from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

import py_trees

from action.input_driver import ActionRequest
from decision.waypoint_actions import build_requests_from_waypoint_action


@dataclass(frozen=True)
class BTInputs:
    sig: Any
    healing_cfg: Any
    cavebot_next: str
    cavebot_action: str
    commit_flag: bool
    eat_food: bool


class _BB:
    # Blackboard keys (keep stable strings for debugging)
    # NOTE: py_trees' Blackboard.set() treats dot-separated names as nested
    # attributes and will raise if the root doesn't exist. Keep keys flat.
    inputs = "bt_inputs"
    out_requests = "bt_out_requests"


class _ResetPlan(py_trees.behaviour.Behaviour):
    def __init__(self) -> None:
        super().__init__(name="ResetPlan")
        self.bb = py_trees.blackboard.Blackboard()

    def update(self) -> py_trees.common.Status:
        self.bb.set(_BB.out_requests, [])
        return py_trees.common.Status.SUCCESS


class _PlanHealing(py_trees.behaviour.Behaviour):
    def __init__(self) -> None:
        super().__init__(name="PlanHealing")
        self.bb = py_trees.blackboard.Blackboard()

    def update(self) -> py_trees.common.Status:
        inp: BTInputs | None = self.bb.get(_BB.inputs)
        if inp is None:
            return py_trees.common.Status.SUCCESS

        try:
            enabled = bool(getattr(inp.healing_cfg, "enabled", False))
            triggered = bool(getattr(inp.sig, "healing_trigger", False)) if inp.sig is not None else False
            if enabled and triggered:
                act = (getattr(inp.healing_cfg, "action", "") or "").strip() or "heal"
                reqs: list[ActionRequest] = list(self.bb.get(_BB.out_requests) or [])
                reqs.append(ActionRequest(kind="heal", value=str(act), note="preview"))
                self.bb.set(_BB.out_requests, reqs)
        except Exception:
            pass

        return py_trees.common.Status.SUCCESS


class _PlanCavebotMove(py_trees.behaviour.Behaviour):
    def __init__(self) -> None:
        super().__init__(name="PlanCavebotMove")
        self.bb = py_trees.blackboard.Blackboard()

    def update(self) -> py_trees.common.Status:
        inp: BTInputs | None = self.bb.get(_BB.inputs)
        if inp is None:
            return py_trees.common.Status.SUCCESS

        try:
            if inp.cavebot_next in {"north", "south", "east", "west"}:
                note = "committed" if bool(inp.commit_flag) else "preview"
                reqs: list[ActionRequest] = list(self.bb.get(_BB.out_requests) or [])
                reqs.append(ActionRequest(kind="move", value=str(inp.cavebot_next), note=note))
                self.bb.set(_BB.out_requests, reqs)
        except Exception:
            pass

        return py_trees.common.Status.SUCCESS


class _PlanWaypointAction(py_trees.behaviour.Behaviour):
    def __init__(self) -> None:
        super().__init__(name="PlanWaypointAction")
        self.bb = py_trees.blackboard.Blackboard()

    def update(self) -> py_trees.common.Status:
        inp: BTInputs | None = self.bb.get(_BB.inputs)
        if inp is None:
            return py_trees.common.Status.SUCCESS

        try:
            if inp.cavebot_action:
                reqs: list[ActionRequest] = list(self.bb.get(_BB.out_requests) or [])
                reqs.extend(
                    build_requests_from_waypoint_action(inp.cavebot_action, committed=bool(inp.commit_flag))
                )
                self.bb.set(_BB.out_requests, reqs)
        except Exception:
            pass

        return py_trees.common.Status.SUCCESS


class _PlanFood(py_trees.behaviour.Behaviour):
    def __init__(self) -> None:
        super().__init__(name="PlanFood")
        self.bb = py_trees.blackboard.Blackboard()

    def update(self) -> py_trees.common.Status:
        inp: BTInputs | None = self.bb.get(_BB.inputs)
        if inp is None:
            return py_trees.common.Status.SUCCESS

        try:
            if bool(inp.eat_food):
                reqs: list[ActionRequest] = list(self.bb.get(_BB.out_requests) or [])
                reqs.append(ActionRequest(kind="maintenance", value="eat_food", note="preview"))
                self.bb.set(_BB.out_requests, reqs)
        except Exception:
            pass

        return py_trees.common.Status.SUCCESS


class BehaviorTreeRunner:
    """Assistant-only behavior tree.

    Produces ActionRequest objects (no input injection).

    Enable/disable via env:
    - BT_ENABLED (default: 1)
    """

    def __init__(self) -> None:
        root = py_trees.composites.Sequence(name="BT", memory=False)
        root.add_children([
            _ResetPlan(),
            _PlanHealing(),
            _PlanCavebotMove(),
            _PlanWaypointAction(),
            _PlanFood(),
        ])
        self.tree = py_trees.trees.BehaviourTree(root)
        self.bb = py_trees.blackboard.Blackboard()

    @staticmethod
    def enabled_from_env() -> bool:
        raw = (os.getenv("BT_ENABLED", "1") or "1").strip().lower()
        return raw in {"1", "true", "yes"}

    def tick(
        self,
        *,
        sig: Any,
        healing_cfg: Any,
        cavebot_next: str,
        cavebot_action: str,
        commit_flag: bool,
        eat_food: bool,
    ) -> list[ActionRequest]:
        self.bb.set(
            _BB.inputs,
            BTInputs(
                sig=sig,
                healing_cfg=healing_cfg,
                cavebot_next=str(cavebot_next or ""),
                cavebot_action=str(cavebot_action or ""),
                commit_flag=bool(commit_flag),
                eat_food=bool(eat_food),
            ),
        )

        try:
            self.tree.tick()
        except Exception:
            # Fail-safe: don't crash decision thread.
            pass

        out = self.bb.get(_BB.out_requests)
        if isinstance(out, list):
            try:
                return [r for r in out if isinstance(r, ActionRequest)]
            except Exception:
                return []
        return []
