from typing import List, Dict, Optional
from gamestate.builder import GameState
import pyautogui

class TargetingManager:
    def __init__(self, priorities: List[str] = ["Dragon", "Demon", "Boss"]):
        self.priorities = priorities
        self.current_target: Optional[Dict] = None

    def select_target(self, gs: GameState) -> Optional[Dict]:
        if not gs.battlelist:
            self.current_target = None
            return None
        # Priorizar por nombre resuelto y HP bajo
        candidates = [b for b in gs.battlelist if b.get('resolved')]
        if not candidates:
            self.current_target = None
            return None
        best = min(candidates, key=lambda b: (
            self.priorities.index(b['resolved']) if b['resolved'] in self.priorities else 999,
            b['hp_pct']
        ))
        if best['hp_pct'] > 0:
            self.current_target = best
            return best
        self.current_target = None
        return None

    def attack(self):
        if self.current_target:
            pyautogui.press('f2')  # Hotkey attack

    def update(self, gs: GameState):
        target = self.select_target(gs)
        if target:
            self.attack()