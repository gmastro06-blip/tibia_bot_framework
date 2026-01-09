from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

Point = Tuple[int, int]


@dataclass(frozen=True)
class AStarResult:
    path: List[Point]
    visited: int


def manhattan(a: Point, b: Point) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def reconstruct_path(came_from: Dict[Point, Point], current: Point) -> List[Point]:
    out = [current]
    while current in came_from:
        current = came_from[current]
        out.append(current)
    out.reverse()
    return out


def astar(
    start: Point,
    goal: Point,
    *,
    is_walkable: Callable[[int, int], bool],
    max_nodes: int = 50_000,
) -> Optional[AStarResult]:
    """A* on a 4-neighborhood grid.

    - `is_walkable(x, y)` decides if a tile can be traversed.
    - Returns None if no path found within `max_nodes` expansions.
    """

    if start == goal:
        return AStarResult(path=[start], visited=0)

    if not is_walkable(start[0], start[1]) or not is_walkable(goal[0], goal[1]):
        return None

    # (f, g, (x,y))
    open_heap: List[Tuple[int, int, Point]] = []
    heapq.heappush(open_heap, (manhattan(start, goal), 0, start))

    came_from: Dict[Point, Point] = {}
    g_score: Dict[Point, int] = {start: 0}
    closed: set[Point] = set()

    visited = 0

    # Deterministic neighbor order (helps tests and reproducibility).
    neighbors = ((0, -1), (0, 1), (1, 0), (-1, 0))  # N, S, E, W

    while open_heap and visited < max_nodes:
        _f, g, current = heapq.heappop(open_heap)
        if current in closed:
            continue
        if current == goal:
            return AStarResult(path=reconstruct_path(came_from, current), visited=visited)

        closed.add(current)
        visited += 1

        cx, cy = current
        for dx, dy in neighbors:
            nx, ny = cx + dx, cy + dy
            nxt = (nx, ny)
            if nxt in closed:
                continue
            if not is_walkable(nx, ny):
                continue

            tentative_g = g + 1
            best_g = g_score.get(nxt)
            if best_g is not None and tentative_g >= best_g:
                continue

            came_from[nxt] = current
            g_score[nxt] = tentative_g
            f = tentative_g + manhattan(nxt, goal)
            heapq.heappush(open_heap, (f, tentative_g, nxt))

    return None


def clamp_int(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def make_bounded_walkable(
    *,
    min_x: int,
    max_x: int,
    min_y: int,
    max_y: int,
    blocked: Optional[set[Point]] = None,
    base_is_walkable: Optional[Callable[[int, int], bool]] = None,
) -> Callable[[int, int], bool]:
    """Build an `is_walkable` predicate with bounds + dynamic blockers.

    If `base_is_walkable` is None, everything inside bounds is walkable.
    """

    b = blocked or set()

    def _is_walkable(x: int, y: int) -> bool:
        if x < min_x or x > max_x or y < min_y or y > max_y:
            return False
        if (x, y) in b:
            return False
        if base_is_walkable is not None:
            return bool(base_is_walkable(x, y))
        return True

    return _is_walkable
