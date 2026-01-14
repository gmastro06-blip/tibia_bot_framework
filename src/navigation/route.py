from __future__ import annotations

import json
import os
import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional



@dataclass(frozen=True)
class Waypoint:
    x: int
    y: int
    # True if this waypoint came from a route item that actually had x/y.
    # Label-only / action-only steps are allowed by the route schema and should
    # not be treated as (0,0) coordinates.
    has_xy: bool = True
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
    # Preserve unknown fields for roundtrip stability.
    extras: Dict[str, Any] = field(default_factory=dict)



def load_route(path: str) -> List[Waypoint]:
    """Carga una ruta desde JSON.

    Formato esperado: lista de objetos con {x, y, z?, name?, action?, label?, call?, load?, conditional_jump?, type?, comment?, raw_line?}.
    """
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))

    # Optional schema validation (helps catch typos early).
    # Opt-out: ROUTE_SCHEMA_VALIDATE=0
    try:
        validate_enabled = (os.getenv("ROUTE_SCHEMA_VALIDATE", "1") or "1").strip().lower() not in {
            "0",
            "false",
            "no",
        }
        if validate_enabled:
            js = importlib.import_module("jsonschema")
            validate = getattr(js, "validate", None)
            if callable(validate):
                schema_path = Path(__file__).resolve().parents[2] / "configs" / "schemas" / "route.schema.json"
                if schema_path.is_file():
                    schema = json.loads(schema_path.read_text(encoding="utf-8"))
                    validate(instance=data, schema=schema)
    except Exception:
        pass

    if not isinstance(data, list):
        raise ValueError("route.json must be a list")

    out: List[Waypoint] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        known_keys = {
            "x",
            "y",
            "z",
            "name",
            "action",
            "label",
            "call",
            "load",
            "conditional_jump",
            "type",
            "comment",
            "raw_line",
        }
        extras: Dict[str, Any] = {k: v for k, v in item.items() if k not in known_keys}
        x = item.get("x")
        y = item.get("y")
        z = item.get("z")
        try:
            xi = int(x) if x is not None else None
            yi = int(y) if y is not None else None
        except Exception:
            xi = None
            yi = None
        has_xy = bool(xi is not None and yi is not None)
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
                has_xy=has_xy,
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
                extras=extras,
            )
        )

    return out


def save_route(path: str, route: List[Waypoint]) -> None:
    """Guarda una ruta JSON preservando campos desconocidos (extras)."""

    p = Path(path)
    items: List[Dict[str, Any]] = []
    for wp in route:
        d: Dict[str, Any] = {}
        try:
            if wp.extras:
                d.update(dict(wp.extras))
        except Exception:
            pass

        if bool(getattr(wp, "has_xy", True)):
            d["x"] = int(wp.x)
            d["y"] = int(wp.y)
        if wp.z is not None:
            d["z"] = int(wp.z)

        for key in ["name", "action", "label", "call", "load", "type", "comment", "raw_line"]:
            val = getattr(wp, key, None)
            if val is not None:
                d[key] = val

        cj = getattr(wp, "conditional_jump", None)
        if cj is not None:
            d["conditional_jump"] = cj

        items.append(d)

    p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
