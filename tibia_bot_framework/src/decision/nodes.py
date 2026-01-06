# src/decision/nodes.py
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
import py_trees

from src.gamestate.models import GameState


Action = Dict[str, Any]
GetState = Callable[[], Optional[GameState]]
EmitAction = Callable[[Action], None]


class HealNode(py_trees.behaviour.Behaviour):
    def __init__(self, get_state: GetState, emit: EmitAction, hp_threshold: int = 45):
        super().__init__(name="Heal")
        self.get_state = get_state
        self.emit = emit
        self.hp_threshold = hp_threshold

    def update(self) -> py_trees.common.Status:
        state = self.get_state()
        if state is None:
            return py_trees.common.Status.FAILURE

        if state.hp <= self.hp_threshold:
            self.emit({"type": "heal", "reason": f"hp<=%d" % self.hp_threshold})
            return py_trees.common.Status.SUCCESS

        return py_trees.common.Status.FAILURE


class AttackNode(py_trees.behaviour.Behaviour):
    def __init__(self, get_state: GetState, emit: EmitAction):
        super().__init__(name="Attack")
        self.get_state = get_state
        self.emit = emit

    def update(self) -> py_trees.common.Status:
        state = self.get_state()
        if state is None:
            return py_trees.common.Status.FAILURE

        # Ejemplo: atacar si hay entidades
        if getattr(state, "entities", None):
            target = state.entities[0]
            self.emit({"type": "attack", "target": getattr(target, "name", "unknown")})
            return py_trees.common.Status.SUCCESS

        return py_trees.common.Status.FAILURE


class MoveToNode(py_trees.behaviour.Behaviour):
    def __init__(self, get_state: GetState, emit: EmitAction):
        super().__init__(name="MoveTo")
        self.get_state = get_state
        self.emit = emit

    def update(self) -> py_trees.common.Status:
        state = self.get_state()
        if state is None:
            return py_trees.common.Status.FAILURE

        # Placeholder: navegación
        self.emit({"type": "move", "to": "next_waypoint"})
        return py_trees.common.Status.SUCCESS
