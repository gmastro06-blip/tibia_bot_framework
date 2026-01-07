import py_trees as bt
from gamestate.builder import GameState
import pyautogui


class HealNode(bt.behaviour.Behaviour):
    def __init__(self):
        super().__init__(name="HealIfLow")

    def update(self) -> bt.common.Status:
        gs: GameState = bt.blackboard.Blackboard().get("gamestate")
        if gs and gs.hp_max > 0 and gs.hp_cur / gs.hp_max < 0.5:
            pyautogui.press('f1')  # Heal hotkey
            return bt.common.Status.SUCCESS
        return bt.common.Status.FAILURE


class AttackNode(bt.behaviour.Behaviour):
    def __init__(self):
        super().__init__(name="AttackTarget")

    def update(self) -> bt.common.Status:
        gs: GameState = bt.blackboard.Blackboard().get("gamestate")
        if gs and gs.battlelist:
            pyautogui.press('f2')  # Attack hotkey
            return bt.common.Status.SUCCESS
        return bt.common.Status.FAILURE


class MoveNode(bt.behaviour.Behaviour):
    def __init__(self):
        super().__init__(name="MoveToWaypoint")

    def update(self) -> bt.common.Status:
        gs: GameState = bt.blackboard.Blackboard().get("gamestate")
        if gs.player_pos != (0, 0):  # Placeholder waypoint
            pyautogui.press('up')  # Move example
            return bt.common.Status.RUNNING
        return bt.common.Status.SUCCESS


class BehaviorTree:
    def __init__(self):
        self.root = bt.composites.Selector(name="Root", memory=False)
        self.root.add_children([
            HealNode(),
            AttackNode(),
            MoveNode(),
            bt.behaviours.Success(name="Idle")
        ])
        self.tree = bt.trees.BehaviourTree(self.root)

    def tick(self, gamestate: GameState) -> str:
        bt.blackboard.Blackboard().set("gamestate", gamestate)
        self.tree.tick()
        return "idle"  # Placeholder; extiende con status check si needed
