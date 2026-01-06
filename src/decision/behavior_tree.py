from __future__ import annotations

import py_trees as bt
from typing import TYPE_CHECKING, Callable, Any

if TYPE_CHECKING:
    from gamestate.state import GameState


class _CheckBlackboard(bt.behaviour.Behaviour):
    def __init__(self, name: str, key: str, predicate: Callable[[Any], bool]):
        super().__init__(name)
        self.key = key
        self.predicate = predicate
        self.bb = bt.blackboard.Blackboard()

    def update(self) -> bt.common.Status:
        try:
            value = self.bb.get(self.key)
        except KeyError:
            return bt.common.Status.FAILURE
        return bt.common.Status.SUCCESS if self.predicate(value) else bt.common.Status.FAILURE


class BehaviorTree:
    def __init__(self):
        self.root = bt.composites.Sequence(name="Root", memory=False)
        self.root.add_children([
            _CheckBlackboard("HP < 50", "hp_low", lambda x: x is not None and x < 50),
            _CheckBlackboard("Has targets", "targets", lambda x: hasattr(x, "__len__") and len(x) > 0),
        ])
        self.tree = bt.trees.BehaviourTree(self.root)

    def tick(self, gamestate: GameState) -> str:
        bb = bt.blackboard.Blackboard()
        bb.set("hp_low", getattr(gamestate, "hp_cur", None))
        bb.set("targets", getattr(gamestate, "battlelist", []))
        self.tree.tick()
        return "Success" if self.root.status == bt.common.Status.SUCCESS else "Failure"


class PrioritySelector(bt.composites.Selector):
    def __init__(self, name: str = "PrioritySelector", memory: bool = False):
        super().__init__(name=name, memory=memory)
