import py_trees as bt

class BehaviorTree:
    def __init__(self):
        self.root = bt.composites.Sequence(name="Root")
        self.root.add_children([
            bt.behaviours.CheckBlackboardVariableValue("hp_low", lambda x: x < 50, "Heal"),
            bt.behaviours.CheckBlackboardVariableValue("targets", lambda x: len(x) > 0, "Attack")
            # Agregar más
        ])
        self.tree = bt.trees.BehaviourTree(self.root)

    def tick(self, gamestate: GameState) -> str:
        bt.blackboard.Blackboard().set("hp_low", gamestate.hp_cur)
        bt.blackboard.Blackboard().set("targets", gamestate.battlelist)
        self.tree.tick()
        return "Success" if self.root.status == bt.common.Status.SUCCESS else "Failure"