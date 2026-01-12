from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional



@dataclass(frozen=True)
class Waypoint:
    x: int
    y: int
    z: Optional[int] = None
    name: Optional[str] = None
    action: Optional[str] = None
    label: Optional[str] = None
    call: Optional[str] = None
    load: Optional[str] = None
    conditional_jump: Optional[dict] = None
    type: Optional[str] = None
    comment: Optional[str] = None
    raw_line: Optional[str] = None



def load_route(path: str) -> List[Waypoint]:
    """Carga una ruta desde JSON.

    Formato esperado: lista de objetos con {x, y, z?, name?, action?, label?, call?, load?, conditional_jump?, type?, comment?, raw_line?}.
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
        z = item.get("z")
        try:
            xi = int(x) if x is not None else None
            yi = int(y) if y is not None else None
        except Exception:
            xi = None
            yi = None
        zi: Optional[int] = None
        if z is not None and str(z).strip() != "":
            try:
                zi = int(z)
            except Exception:
                zi = None
        name = item.get("name")
        action = item.get("action")
        label = item.get("label")
        call = item.get("call")
        load = item.get("load")
        conditional_jump = item.get("conditional_jump")
        typ = item.get("type")
        comment = item.get("comment")
        raw_line = item.get("raw_line")
        # Only require x/y for waypoints that have them; allow label-only or action-only steps
        out.append(
            Waypoint(
                x=xi if xi is not None else 0,
                y=yi if yi is not None else 0,
                z=zi,
                name=str(name) if name is not None else None,
                action=str(action) if action is not None else None,
                label=str(label) if label is not None else None,
                call=str(call) if call is not None else None,
                load=str(load) if load is not None else None,
                conditional_jump=conditional_jump if conditional_jump is not None else None,
                type=str(typ) if typ is not None else None,
                comment=str(comment) if comment is not None else None,
                raw_line=str(raw_line) if raw_line is not None else None,
            )
        )

    return out
