from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class WPLine:
    kind: str  # label|action|node|stand|rope|ladder|...
    raw: str
    label: Optional[str] = None
    action: Optional[str] = None
    pos: Optional[Tuple[int, int, int]] = None


_POS_RE = re.compile(r"\((\s*-?\d+\s*),(\s*-?\d+\s*),(\s*-?\d+\s*)\)")


def parse_waypoints_in(path: Path) -> List[WPLine]:
    out: List[WPLine] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("//"):
            continue

        # Normalize whitespace
        line = re.sub(r"\s+", " ", line)

        if line.lower().startswith("label "):
            name = line.split(" ", 1)[1].strip()
            out.append(WPLine(kind="label", raw=raw, label=name))
            continue

        if line.lower().startswith("action "):
            name = line.split(" ", 1)[1].strip()
            out.append(WPLine(kind="action", raw=raw, action=name))
            continue

        # Generic command with a position: node (x,y,z), stand (x,y,z), rope (x,y,z), ladder (x,y,z)...
        parts = line.split(" ", 1)
        cmd = parts[0].strip().lower()
        m = _POS_RE.search(line)
        if m:
            x = int(m.group(1))
            y = int(m.group(2))
            z = int(m.group(3))
            out.append(WPLine(kind=cmd, raw=raw, pos=(x, y, z)))
            continue

        # Fallback unknown
        out.append(WPLine(kind="unknown", raw=raw))

    return out
