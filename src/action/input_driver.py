from __future__ import annotations

from dataclasses import dataclass
import os
from typing import List, Optional, Protocol

from action.keyboard import KeyboardSender, SCANCODES


@dataclass(frozen=True)
class ActionRequest:
    """An abstract action request.

    This project defaults to *assistant mode* (no real input injection). An
    ActionRequest is a structured representation of what the bot would like to
    do (e.g., move north), which can be handled by a mock driver for logging/
    testing.
    """

    kind: str  # e.g. "move", "cast", "use"
    value: str  # e.g. "north" or hotkey name
    note: str = ""


class InputDriver(Protocol):
    """Interface for action side-effects.

    IMPORTANT: We intentionally keep the default implementation as a mock.
    """

    def send(self, action: ActionRequest) -> bool:  # pragma: no cover
        ...


def is_committed(action: ActionRequest) -> bool:
    """Return True only for actions explicitly marked as committed.

    The bot runs in assistant mode by default. When OS input injection is enabled,
    we must never execute preview actions.
    """

    try:
        return str(getattr(action, "note", "") or "").strip().lower() == "committed"
    except Exception:
        return False


class MockInputDriver:
    """A safe input driver that only records actions (no OS/game input)."""

    def __init__(self, *, max_items: int = 200) -> None:
        self._max_items = max(1, int(max_items))
        self.actions: List[ActionRequest] = []

    def send(self, action: ActionRequest) -> bool:
        self.actions.append(action)
        if len(self.actions) > self._max_items:
            # Drop oldest to avoid unbounded growth.
            self.actions = self.actions[-self._max_items :]
        return True

    def last(self) -> Optional[ActionRequest]:
        return self.actions[-1] if self.actions else None


class WindowsKeyboardDriver:
    """Send ActionRequests to the OS using SendInput (WASD + F-keys)."""

    def __init__(self, *, target_hotkey: str | None = None, minimap_hotkey: str | None = None) -> None:
        self.ks = KeyboardSender()
        key = (target_hotkey or "").strip().upper()
        self.target_hotkey = key if key in SCANCODES else None

        mm = (minimap_hotkey or "").strip().upper()
        self.minimap_hotkey = mm if mm in SCANCODES or "+" in mm else None

    def _tap_hotkey(self, key: str) -> bool:
        raw = (key or "").strip().upper()
        if not raw:
            return False

        parts = [p for p in (raw.replace(" ", "").split("+")) if p]
        if not parts:
            return False

        try:
            if len(parts) == 1:
                if parts[0] not in SCANCODES:
                    return False
                self.ks.tap(parts[0])
                return True
            # Combo (e.g., CTRL+L)
            if any(p not in SCANCODES for p in parts):
                return False
            return self.ks.tap_combo(parts)
        except Exception:
            return False

    def send(self, action: ActionRequest) -> bool:
        # Defense-in-depth: never inject OS input unless explicitly committed.
        if not is_committed(action):
            return False

        # Focus guard (strict): inputs only if the client window is foreground.
        try:
            from input_focus_guard import get_client_hwnd, is_allowed_to_inject

            hwnd = int(get_client_hwnd() or 0)
            ok, _reason = is_allowed_to_inject(hwnd)
            if not ok:
                return False
        except Exception:
            # Fail-closed: if we cannot validate focus, do not inject.
            return False

        kind = (action.kind or "").strip().lower()
        val = (action.value or "").strip()

        if kind in {"heal", "maintenance", "keyboard_sim", "loot", "tool", "switch", "npc_trade", "depot", "bank", "supplies"}:
            return self._tap_hotkey(val)

        if kind == "minimap_click_sim" and self.minimap_hotkey:
            return self._tap_hotkey(self.minimap_hotkey)

        if kind == "target" and self.target_hotkey:
            return self._tap_hotkey(self.target_hotkey)

        if kind == "move":
            def _mv(name: str, default: str) -> str:
                try:
                    return (os.getenv(name, default) or default).strip().upper()
                except Exception:
                    return str(default).strip().upper()

            # Defaults match Tibia's common movement bindings (arrow keys).
            dir_map = {
                "north": _mv("ASSIST_MOVE_NORTH", "UP"),
                "south": _mv("ASSIST_MOVE_SOUTH", "DOWN"),
                "west": _mv("ASSIST_MOVE_WEST", "LEFT"),
                "east": _mv("ASSIST_MOVE_EAST", "RIGHT"),
            }
            mapped = dir_map.get(val.lower())
            if mapped:
                return self._tap_hotkey(mapped)
        return False
