from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


def _norm_label(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"[^a-z0-9_]+", "", s)
    s = re.sub(r"_+", "_", s)
    return s


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def default_obstacle_ignore_classes() -> Set[str]:
    raw = os.getenv(
        "OBSTACLE_IGNORE_CLASSES",
        "hp_text,mp_text,hp_ocr,mp_ocr,hp_bar,mp_bar,health_bar,mana_bar",
    )
    return {_norm_label(x) for x in (p.strip() for p in raw.split(",")) if x}


def compute_viewport_tile_offsets(
    boxes: Optional[Sequence[Dict[str, Any]]],
    *,
    viewport_rect: Tuple[int, int, int, int],
    tile_px: int = 32,
    min_conf: float = 0.25,
    ignore_classes: Optional[Set[str]] = None,
    max_abs_offset: Optional[int] = None,
) -> List[Tuple[int, int]]:
    """Convert Roboflow boxes (x,y,w,h with center coords in px) into tile offsets.

    Assumptions (simple but practical for Tibia-like):
    - Player is at the center of the game viewport.
    - Tile size is `tile_px` pixels (default 32).

    Returns a list of (dx_tiles, dy_tiles) offsets, where (0,0) would be player's tile.
    """

    if not boxes:
        return []

    vx, vy, vw, vh = viewport_rect
    vcx = float(vx) + float(vw) / 2.0
    vcy = float(vy) + float(vh) / 2.0

    t = max(1, int(tile_px))
    ignore = ignore_classes if ignore_classes is not None else default_obstacle_ignore_classes()

    out: Set[Tuple[int, int]] = set()

    for b in boxes:
        if not isinstance(b, dict):
            continue

        cls = _norm_label(str(b.get("class", "")))
        if cls in ignore:
            continue

        conf = _safe_float(b.get("confidence", 0.0), 0.0)
        if conf < float(min_conf):
            continue

        bx = _safe_float(b.get("x", 0.0), 0.0)
        by = _safe_float(b.get("y", 0.0), 0.0)

        dx_px = bx - vcx
        dy_px = by - vcy

        dx = int(round(dx_px / float(t)))
        dy = int(round(dy_px / float(t)))

        if dx == 0 and dy == 0:
            # Don't block the player's own tile.
            continue

        if max_abs_offset is not None:
            m = int(max_abs_offset)
            if abs(dx) > m or abs(dy) > m:
                continue

        out.add((dx, dy))

    # Stable ordering (useful for debugging/tests)
    return sorted(out, key=lambda p: (p[1], p[0]))
