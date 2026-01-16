from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from action.input_driver import ActionRequest, InputDriver, is_committed
from input_bridge import is_allowed_key
from input_bridge_client import InputBridgeClient


@dataclass
class InputBridgeDriver(InputDriver):
    client: InputBridgeClient
    target_hotkey: str | None = None

    def _map_move_key(self, direction: str) -> Optional[str]:
        def _env(name: str, default: str) -> str:
            try:
                return (os.getenv(name, default) or default).strip().upper()
            except Exception:
                return str(default).strip().upper()

        mapping = {
            "north": _env("ASSIST_MOVE_NORTH", "W"),
            "south": _env("ASSIST_MOVE_SOUTH", "S"),
            "west": _env("ASSIST_MOVE_WEST", "A"),
            "east": _env("ASSIST_MOVE_EAST", "D"),
        }
        key = mapping.get(direction.lower())
        if key and is_allowed_key(key):
            return key
        return None

    def _send_hotkey(self, key: str | None) -> bool:
        k = str(key or "").strip().upper()
        if not k or not is_allowed_key(k):
            return False
        return bool(self.client.send_key_press(k))

    def send(self, action: ActionRequest) -> bool:
        # Safety: only committed actions are sent through the bridge.
        if not is_committed(action):
            return False

        kind = (action.kind or "").strip().lower()
        val = (action.value or "").strip()

        if kind == "move":
            key = self._map_move_key(val)
            if not key:
                return False
            return bool(self.client.send_key_press(key))

        if kind in {"heal", "mana", "loot", "tool", "switch", "maintenance"}:
            return self._send_hotkey(val)

        if kind in {"target", "battlelist_target"}:
            return self._send_hotkey(self.target_hotkey or val)

        # Mouse requests are supported via client API but not mapped here yet.
        return False
