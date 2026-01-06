# src/decision/engine.py - Versión corregida completa (copia y reemplaza el archivo)

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import py_trees
from typing import List, Any
from scripting.engine import ScriptEngine  # Sin relative ..
from gamestate.models import GameState

# Nodos placeholder (implementa lógica real después)
class HealNode(py_trees.behaviour.Behaviour):
    def __init__(self):
        super().__init__(name="Heal")
    
    def update(self) -> py_trees.common.Status:
        print("[BT] Checking heal...")
        # TODO: if state.hp < 50: heal action
        return py_trees.common.Status.SUCCESS

class AttackNode(py_trees.behaviour.Behaviour):
    def __init__(self):
        super().__init__(name="Attack")
    
    def update(self) -> py_trees.common.Status:
        print("[BT] Checking attack...")
        # TODO: if target: attack
        return py_trees.common.Status.SUCCESS

class MoveToNode(py_trees.behaviour.Behaviour):
    def __init__(self):
        super().__init__(name="Move To")
    
    def update(self) -> py_trees.common.Status:
        print("[BT] Moving to waypoint...")
        # TODO: navigator move
        return py_trees.common.Status.SUCCESS

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
        self.tree.tick()
        actions: List[Any] = []  # Recopila de nodos si implementas blackboard/actions
        return actions