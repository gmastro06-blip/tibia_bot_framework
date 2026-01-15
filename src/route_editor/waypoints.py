from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Tuple

from route_editor.models import WaypointStep, WaypointParseResult

_LINE_RE = re.compile(r"^(?P<cmd>[a-zA-Z_]+)\s*(?P<rest>.*)$")
_COORD_RE = re.compile(r"^\(\s*(?P<x>-?\d+)\s*,\s*(?P<y>-?\d+)\s*,\s*(?P<z>-?\d+)\s*\)$")

_VALID_CMDS = {"label", "action", "call", "load", "cond", "node", "stand", "rope", "ladder"}


@dataclass
class _Cursor:
    x: int | None = None
    y: int | None = None
    z: int | None = None

    def update(self, step: WaypointStep) -> None:
        if step.x is not None and step.y is not None and step.z is not None:
            self.x, self.y, self.z = step.x, step.y, step.z


def parse_waypoints(text: str) -> WaypointParseResult:
    steps: List[WaypointStep] = []
    errors: List[str] = []

    for idx, raw_line in enumerate(text.splitlines(), start=1):
        original_raw = str(raw_line)
        line = original_raw.strip()
        if not line:
            continue
        enabled = True
        if line.startswith("#"):
            enabled = False
            line = line.lstrip("#").strip()
            if not line:
                continue

        m = _LINE_RE.match(line)
        if not m:
            errors.append(f"L{idx}: no pude parsear '{raw_line}'")
            continue
        cmd = m.group("cmd").lower()
        rest = m.group("rest").strip()
        if cmd not in _VALID_CMDS:
            errors.append(f"L{idx}: comando desconocido '{cmd}'")
            continue

        if cmd in {"label", "action", "call", "load"}:
            if not rest:
                errors.append(f"L{idx}: falta nombre para {cmd}")
                continue
            steps.append(WaypointStep(kind=cmd, name=rest, enabled=enabled, raw_line=line))
            continue

        if cmd == "cond":
            parts = [p for p in rest.split() if p]
            if len(parts) < 3:
                errors.append("L{}: cond requiere: cond <var> <label_true> <label_false>".format(idx))
                continue
            var_name, label_true, label_false = parts[0], parts[1], parts[2]
            steps.append(
                WaypointStep(
                    kind="cond",
                    name=var_name,
                    params={"label_true": label_true, "label_false": label_false},
                    enabled=enabled,
                    raw_line=line,
                )
            )
            continue

        coord_match = _COORD_RE.match(rest)
        if not coord_match:
            errors.append(f"L{idx}: coordenadas inválidas '{rest}'")
            continue
        x = int(coord_match.group("x"))
        y = int(coord_match.group("y"))
        z = int(coord_match.group("z"))
        steps.append(WaypointStep(kind=cmd, x=x, y=y, z=z, enabled=enabled, raw_line=line))

    return WaypointParseResult(steps=steps, errors=errors)


def serialize_waypoints(steps: Iterable[WaypointStep]) -> str:
    out_lines: List[str] = []
    for step in steps:
        prefix = "# " if not step.enabled else ""
        if step.kind in {"label", "action", "call", "load"}:
            token = step.name.strip()
            out_lines.append(f"{prefix}{step.kind} {token}")
        elif step.kind == "cond":
            var_name = step.name.strip()
            label_true = str(step.params.get("label_true", "")).strip()
            label_false = str(step.params.get("label_false", "")).strip()
            if not var_name or not label_true or not label_false:
                raise ValueError("cond missing var/labels")
            out_lines.append(f"{prefix}cond {var_name} {label_true} {label_false}")
        elif step.kind in {"node", "stand", "rope", "ladder"}:
            if step.x is None or step.y is None or step.z is None:
                raise ValueError(f"step {step.kind} missing coords")
            out_lines.append(f"{prefix}{step.kind} ({step.x}, {step.y}, {step.z})")
        elif step.kind == "custom":
            # Custom freeform; keep name as payload
            token = step.name.strip() or "custom"
            out_lines.append(f"{prefix}{token}")
        else:
            # MOVE and others should have been expanded before serialize
            raise ValueError(f"unsupported step kind {step.kind} during serialize")
    return "\n".join(out_lines)


def expand_move_macros(steps: List[WaypointStep]) -> Tuple[List[WaypointStep], List[str]]:
    """Expand MOVE macro steps into node() steps.

    MOVE params expected: {"dir": "N|S|E|W", "steps": int}
    Uses last known coordinates as cursor.
    """

    cursor = _Cursor()
    out: List[WaypointStep] = []
    errors: List[str] = []

    for step in steps:
        if step.kind.lower() != "move":
            out.append(step)
            cursor.update(step)
            continue

        dir_token = str(step.params.get("dir", "")).upper()
        steps_n = int(step.params.get("steps", 0) or 0)
        if steps_n <= 0 or dir_token not in {"N", "S", "E", "W"}:
            errors.append(f"MOVE invalido dir={dir_token} steps={steps_n}")
            continue
        if cursor.x is None or cursor.y is None or cursor.z is None:
            errors.append("MOVE sin coordenadas base")
            continue

        dx, dy = 0, 0
        if dir_token == "N":
            dy = -1
        elif dir_token == "S":
            dy = 1
        elif dir_token == "E":
            dx = 1
        elif dir_token == "W":
            dx = -1

        cx, cy, cz = cursor.x, cursor.y, cursor.z
        for _ in range(steps_n):
            cx += dx
            cy += dy
            new_step = WaypointStep(kind="node", x=cx, y=cy, z=cz, enabled=step.enabled, comment=step.comment)
            out.append(new_step)
            cursor.update(new_step)
    return out, errors


def validate_waypoints(steps: Iterable[WaypointStep]) -> List[str]:
    errors: List[str] = []
    cursor = _Cursor()
    for idx, s in enumerate(steps, start=1):
        k = s.kind.lower()
        if k in {"label", "action", "call", "load"}:
            if not s.name.strip():
                errors.append(f"step {idx}: {k} sin nombre")
        elif k == "cond":
            if not s.name.strip():
                errors.append(f"step {idx}: cond sin var")
            if not str(s.params.get("label_true", "")).strip():
                errors.append(f"step {idx}: cond sin label_true")
            if not str(s.params.get("label_false", "")).strip():
                errors.append(f"step {idx}: cond sin label_false")
        elif k in {"node", "stand", "rope", "ladder"}:
            if s.x is None or s.y is None or s.z is None:
                errors.append(f"step {idx}: {k} sin coords")
            else:
                cursor.update(s)
        elif k == "move":
            dir_token = str(s.params.get("dir", "")).upper()
            steps_n = int(s.params.get("steps", 0) or 0)
            if dir_token not in {"N", "S", "E", "W"}:
                errors.append(f"step {idx}: MOVE dir invalido")
            if steps_n <= 0:
                errors.append(f"step {idx}: MOVE steps debe ser >0")
            if cursor.x is None or cursor.y is None or cursor.z is None:
                errors.append(f"step {idx}: MOVE sin coordenada previa")
        else:
            errors.append(f"step {idx}: tipo desconocido {s.kind}")
    return errors
