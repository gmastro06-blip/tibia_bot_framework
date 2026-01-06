import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import py_trees
from typing import List, Any
from scripting.engine import ScriptEngine
from gamestate.models import GameState

class HealNode(py_trees.behaviour.Behaviour):
    def __init__(self):
        super().__init__(name="Heal")
    
    def update(self) -> py_trees.common.Status:
        state = py_trees.blackboard.Blackboard().get("state")
        if state and state.hp_cur < 0.5 * state.hp_max:
            print("[BT] Healing...")
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.FAILURE

class AttackNode(py_trees.behaviour.Behaviour):
    def __init__(self):
        super().__init__(name="Attack")
    
    def update(self) -> py_trees.common.Status:
        state = py_trees.blackboard.Blackboard().get("state")
        if state and state.battlelist:
            print("[BT] Attacking...")
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.FAILURE

class MoveToNode(py_trees.behaviour.Behaviour):
    def __init__(self):
        super().__init__(name="Move To")
    
    def update(self) -> py_trees.common.Status:
        state = py_trees.blackboard.Blackboard().get("state")
        if state:
            print("[BT] Moving...")
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.FAILURE

class DecisionEngine:
    def __init__(self):
        self.script_engine = ScriptEngine()
        self.root = py_trees.composites.Selector(name="Root", memory=False)
        self.root.add_children([
            HealNode(),
            AttackNode(),
            MoveToNode(),
        ])
        self.tree = py_trees.trees.BehaviourTree(self.root)

    def evaluate(self, state: GameState) -> List[Any]:
        self.script_engine.load_script()
        py_trees.blackboard.Blackboard().set("state", state)
        self.tree.tick()
        actions: List[Any] = []  # Recopila de blackboard si implementas
        return actions