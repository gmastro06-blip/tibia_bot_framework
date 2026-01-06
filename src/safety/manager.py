# src/safety/manager.py - Versión con pause/resume

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from typing import NoReturn
import keyboard
import time
from threading import Event
from gamestate.models import GameState

class SafetyManager:
    def __init__(self, kill_key: str = "esc"):
        self.paused = False
        keyboard.on_press_key(kill_key, self._kill_switch)

    def _kill_switch(self, event: keyboard.KeyboardEvent) -> None:
        self.paused = not self.paused  # Toggle pause/resume
        print("Bot pausado" if self.paused else "Bot resumido")

    def check_watchdogs(self, state: GameState) -> None:
        if state.hp is None or state.position is None:
            self.paused = True

    def monitor(self, pause_event: Event) -> NoReturn:
        while True:
            if self.paused:
                pause_event.set()
            else:
                pause_event.clear()
            time.sleep(0.1)

    def is_paused(self) -> bool:
        return self.paused