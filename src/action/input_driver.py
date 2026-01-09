from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol


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
