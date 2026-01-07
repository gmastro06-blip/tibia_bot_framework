from __future__ import annotations

from typing import List, Callable, Dict, TYPE_CHECKING
import pyautogui
from collections import deque
import time

if TYPE_CHECKING:
    from gamestate.state import GameState  # ajusta si tu GameState está en otro módulo

class ActionExecutor:
    def __init__(self):
        self.queue: deque = deque()
        self.cooldowns: Dict[str, float] = {}

    def add_action(self, action: Callable, confirm_signals: List[Callable], timeout: float = 2.0, retries: int = 3):
        self.queue.append((action, confirm_signals, timeout, retries))

    def execute(self, gamestate: GameState):
        if not self.queue:
            return
        act, signals, timeout, retries = self.queue[0]
        if time.time() < self.cooldowns.get(act.__name__, 0):
            return
        act()
        self.cooldowns[act.__name__] = time.time() + 0.5
        start = time.time()
        old_gs = gamestate
        while time.time() - start < timeout:
            new_gs = ...  # Obtener nuevo state
            if all(sig(old_gs, new_gs) for sig in signals):  # e.g., lambda old, new: new.hp_cur > old.hp_cur
                self.queue.popleft()
                return
        if retries > 0:
            self.queue[0] = (act, signals, timeout, retries-1)
        else:
            self.queue.popleft()
