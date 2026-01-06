from __future__ import annotations

import py_trees as bt
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gamestate.state import GameState  # AJUSTA esta ruta si tu GameState está en otro archivo


class BehaviorTree:
    def __init__(self):
        self.root = bt.composites.Sequence(name="Root")
        self.root.add_children([
            bt.behaviours.CheckBlackboardVariableValue("hp_low", lambda x: x < 50, "Heal"),
            bt.behaviours.CheckBlackboardVariableValue("targets", lambda x: len(x) > 0, "Attack"),
        ])
        self.tree = bt.trees.BehaviourTree(self.root)

    def tick(self, gamestate: GameState) -> str:
        bb = bt.blackboard.Blackboard()
        bb.set("hp_low", gamestate.hp_cur)
        bb.set("targets", gamestate.battlelist)
        self.tree.tick()
        return "Success" if self.root.status == bt.common.Status.SUCCESS else "Failure"


class PrioritySelector(bt.composites.Selector):
    pass
