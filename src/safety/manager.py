import keyboard
import time
import GameState, List, pyautogui
from threading import Thread

class SafetyManager:
    def __init__(self):
        keyboard.add_hotkey('ctrl+alt+del', self.kill_switch)

    def check_critical(self, gamestate: GameState, metrics: Dict) -> bool:
        if gamestate.hp_cur < 20 or metrics['drift'] > 0.5 or metrics['fps'] < 10:
            return False
        return True

    def panic(self):
        pyautogui.press('esc')  # Retreat action

    def kill_switch(self):
        exit(0)

    def watchdog(self, threads: List[Thread]):
        while True:
            for t in threads:
                if not t.is_alive():
                    print("Thread muerto, restart")
            time.sleep(5)