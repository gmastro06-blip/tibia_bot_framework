from typing import Callable, Any, Dict
from queue import PriorityQueue
import time
import pyautogui

class ActionExecutor:
    def __init__(self, max_retries: int = 3, cooldown_ms: int = 500):
        self.queue: PriorityQueue[tuple[int, Any, Callable]] = PriorityQueue()  # Anotación
        self.last_action_time = 0
        self.cooldown = cooldown_ms / 1000

    def queue_action(self, action: Any, confirm_callback: Callable[[str, Dict[str, Any]], bool], priority: int = 0) -> None:  # Dict hint
        self.queue.put((priority, action, confirm_callback))

    def process_queue(self) -> None:
        while not self.queue.empty():
            if time.time() - self.last_action_time < self.cooldown:
                time.sleep(self.cooldown - (time.time() - self.last_action_time))
            prio, action, confirm = self.queue.get()
            self._execute(action)
            for retry in range(3):
                if confirm("type", {"expected": "change"}):  # Visión confirm
                    break
                time.sleep(0.5)
            else:
                # Fallback retreat
                pass
            self.last_action_time = time.time()

    def _execute(self, action: Any) -> None:
        # e.g., if action == "move_up": pyautogui.press("up")
        pass