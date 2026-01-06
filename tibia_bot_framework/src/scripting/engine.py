# src/scripting/engine.py - Versión corregida completa (añadido Optional y otros imports de typing)

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from typing import Any, Optional, Dict  # Añadido Optional y Dict
from lupa import LuaRuntime
import pyautogui
import time
from ..gamestate.models import GameState

current_state: GameState = GameState(hp=100, mp=100, position=(0,0), entities=[], states=[], equipment={}, timestamp=0.0)

class ScriptEngine:
    def __init__(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals()['io'] = None
        self.lua.globals()['os'] = None
        self.lua.globals()['package'] = None
        self.lua.globals()['require'] = None

        def lua_log(msg):
            print(f"[Lua Log] {msg}")

        def lua_move_to(x: int, y: int):
            pyautogui.click(x, y)
            print(f"[Lua Move] Click ({x}, {y})")

        def lua_set_waypoint(name: str):
            print(f"[Lua Waypoint] {name}")

        def lua_wait(ms: int):
            time.sleep(ms / 1000.0)

        def lua_get_hp() -> int:
            return int(current_state.hp)

        def lua_get_mp() -> int:
            return int(current_state.mp)

        def lua_select_target(criteria: Dict[str, Any]) -> Optional[str]:  # Dict añadido para criteria
            if current_state.entities:
                target = current_state.entities[0].name
                print(f"[Lua Target] {target}")
                return target
            return None

        def lua_attack(target: str):
            if target:
                pyautogui.press('f1')
                print(f"[Lua Attack] {target}")

        def lua_heal_if_needed():
            if current_state.hp < 50:
                pyautogui.press('f2')
                print("[Lua Heal] Potion used")

        def lua_retreat():
            pyautogui.press('esc')
            print("[Lua Retreat]")

        self.lua.globals()['log'] = lua_log
        self.lua.globals()['move_to'] = lua_move_to
        self.lua.globals()['set_waypoint'] = lua_set_waypoint
        self.lua.globals()['wait'] = lua_wait
        self.lua.globals()['get_hp'] = lua_get_hp
        self.lua.globals()['get_mp'] = lua_get_mp
        self.lua.globals()['select_target'] = lua_select_target
        self.lua.globals()['attack'] = lua_attack
        self.lua.globals()['heal_if_needed'] = lua_heal_if_needed
        self.lua.globals()['retreat'] = lua_retreat

    def load_script(self, lua_path: str = "examples/scripts/hunt_cave.lua") -> None:
        full_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), lua_path)
        if not os.path.exists(full_path):
            print(f"Advertencia: Script no encontrado en {full_path}. Saltando.")
            return
        with open(full_path, 'r', encoding='utf-8') as f:
            code = f.read()
        try:
            self.lua.execute(code)
            print(f"Script Lua cargado: {lua_path}")
        except Exception as e:
            print(f"Error Lua: {e}")