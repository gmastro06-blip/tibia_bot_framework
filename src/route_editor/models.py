from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class WaypointStep:
    """Represents one line in waypoints.in (or an internal macro like MOVE).

    kind: label | action | node | stand | rope | ladder | move | custom
    name: label/action token
    params: extra params (e.g., {"dir": "N", "steps": 3} for MOVE)
    enabled: False -> exported as commented line (# ...)
    """

    kind: str
    x: Optional[int] = None
    y: Optional[int] = None
    z: Optional[int] = None
    name: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    comment: str = ""
    enabled: bool = True


@dataclass
class SetupConfig:
    """Wrapper around setup_*.json preserving unknown fields for roundtrip."""

    raw: Dict[str, Any]

    @property
    def hunt_config(self) -> Dict[str, Any]:
        return self.raw.setdefault("hunt_config", {})

    @property
    def items(self) -> Dict[str, Any]:
        return self.raw.setdefault("items", {})

    def set_hunt_field(self, key: str, value: Any) -> None:
        self.hunt_config[key] = value

    def set_item(self, name: str, hotkey: str, use: str) -> None:
        if name:
            self.items[name] = {"hotkey": hotkey, "use": use}

    def delete_item(self, name: str) -> None:
        if name in self.items:
            del self.items[name]

    def clone(self) -> "SetupConfig":
        import copy

        return SetupConfig(raw=copy.deepcopy(self.raw))


@dataclass
class WaypointParseResult:
    steps: List[WaypointStep]
    errors: List[str] = field(default_factory=list)
