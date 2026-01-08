from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Waypoint:
    x: int
    y: int
    name: Optional[str] = None
    action: Optional[str] = None


def load_route(path: str) -> List[Waypoint]:
    """Carga una ruta desde JSON.

    Formato esperado: lista de objetos con {x, y, name?, action?}.
    """
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("route.json must be a list")

    out: List[Waypoint] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        x = item.get("x")
        y = item.get("y")
        if x is None or y is None:
            continue
        try:
            xi = int(x)
            yi = int(y)
        except Exception:
            continue
        name = item.get("name")
        action = item.get("action")
        out.append(
            Waypoint(
                x=xi,
                y=yi,
                name=str(name) if name is not None else None,
                action=str(action) if action is not None else None,
            )
        )

    return out
