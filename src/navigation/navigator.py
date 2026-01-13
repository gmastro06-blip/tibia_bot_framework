from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

from navigation.route import Waypoint
from navigation.pathfinding import astar, clamp_int, make_bounded_walkable


@dataclass
class NavDecision:
    direction: Optional[str] = None  # "north"|"south"|"east"|"west"|None
    reached_waypoint: bool = False
    waypoint: Optional[Waypoint] = None


class Navigator:
    """Cavebot básico: sigue waypoints por coordenadas de tile.

    Requiere posición actual (x,y). La extracción real de posición aún no está en visión;
    por ahora el bot puede leer `PLAYER_X`/`PLAYER_Y` para probar.

    Env vars:
    - CAVEBOT_LOOP (default 1): si llega al final, vuelve al inicio.
    - CAVEBOT_WAYPOINT_TOL (default 0): tolerancia en tiles para considerar waypoint alcanzado.
    """

    def __init__(self, route: List[Waypoint]):
        self.route = route
        self.idx = 0
        self.loop = os.getenv("CAVEBOT_LOOP", "1").strip().lower() in {"1", "true", "yes"}
        self.tol = int(os.getenv("CAVEBOT_WAYPOINT_TOL", "0"))
        # Pathfinding mode:
        # - axis: cheap greedy step towards waypoint (default)
        # - astar: local A* with optional dynamic blockers
        self.pathfind_mode = os.getenv("CAVEBOT_PATHFIND", "axis").strip().lower()
        try:
            self.astar_radius = max(5, int(os.getenv("CAVEBOT_ASTAR_RADIUS", "30")))
        except Exception:
            self.astar_radius = 30
        try:
            self.astar_max_nodes = max(100, int(os.getenv("CAVEBOT_ASTAR_MAX_NODES", "8000")))
        except Exception:
            self.astar_max_nodes = 8000

        # Debug/observability (read-only, best-effort)
        self.last_blockers_n: int = 0
        self.last_astar_found: bool | None = None
        self.last_astar_path_len: int | None = None
        self.last_astar_visited: int | None = None
        self.last_goal_local: tuple[int, int] | None = None

    def reset(self) -> None:
        self.idx = 0

    def current_waypoint(self) -> Optional[Waypoint]:
        if not self.route:
            return None
        if self.idx < 0:
            self.idx = 0
        if self.idx >= len(self.route):
            if self.loop:
                self.idx = 0
            else:
                return None
        return self.route[self.idx]

    def _at_waypoint(self, pos: Union[Tuple[int, int], Tuple[int, int, int]], wp: Waypoint) -> bool:
        if not bool(getattr(wp, "has_xy", True)):
            return False
        x, y = int(pos[0]), int(pos[1])
        if abs(x - wp.x) > self.tol or abs(y - wp.y) > self.tol:
            return False

        # If the waypoint specifies floor (z), require it to match.
        if wp.z is not None:
            if len(pos) < 3:
                return False
            try:
                return int(pos[2]) == int(wp.z)
            except Exception:
                return False

        return True

    def decide(
        self, pos: Union[Tuple[int, int], Tuple[int, int, int]], blocked: set[Tuple[int, int]] | None = None
    ) -> NavDecision:
        # Allow label-only / action-only steps in the route.
        # They should not be treated as real coordinates.
        while True:
            wp = self.current_waypoint()
            if wp is None:
                return NavDecision(direction=None, reached_waypoint=False, waypoint=None)

            if not bool(getattr(wp, "has_xy", True)):
                # Consume non-coordinate steps.
                self.idx += 1
                # Emit a reached event only for actionable steps.
                if getattr(wp, "action", None):
                    return NavDecision(direction=None, reached_waypoint=True, waypoint=wp)
                # Labels/comments/calls are skipped silently.
                continue

            if self._at_waypoint(pos, wp):
                # reached: advance
                self.idx += 1
                return NavDecision(direction=None, reached_waypoint=True, waypoint=wp)
            break

        x, y = int(pos[0]), int(pos[1])
        dx = wp.x - x
        dy = wp.y - y

        # Track blockers (even in non-A* mode) for UI/diagnostics.
        try:
            self.last_blockers_n = int(len(blocked) if blocked else 0)
        except Exception:
            self.last_blockers_n = 0

        # Local A* (dynamic obstacles) - only if enabled via env.
        if self.pathfind_mode in {"astar", "a*"}:
            r = int(self.astar_radius)

            # If goal is far away, plan towards a local goal inside the radius window.
            gx = x + clamp_int(dx, -r, r)
            gy = y + clamp_int(dy, -r, r)
            goal_local = (int(gx), int(gy))
            try:
                self.last_goal_local = (int(goal_local[0]), int(goal_local[1]))
            except Exception:
                self.last_goal_local = None

            is_walkable = make_bounded_walkable(
                min_x=int(x - r),
                max_x=int(x + r),
                min_y=int(y - r),
                max_y=int(y + r),
                blocked=blocked,
                base_is_walkable=None,
            )

            res = astar((int(x), int(y)), goal_local, is_walkable=is_walkable, max_nodes=int(self.astar_max_nodes))
            try:
                if res is None:
                    self.last_astar_found = False
                    self.last_astar_path_len = None
                    self.last_astar_visited = None
                else:
                    self.last_astar_found = True
                    self.last_astar_path_len = int(len(res.path))
                    self.last_astar_visited = int(res.visited)
            except Exception:
                self.last_astar_found = None
                self.last_astar_path_len = None
                self.last_astar_visited = None
            if res is not None and len(res.path) >= 2:
                nx, ny = res.path[1]
                if nx == x + 1 and ny == y:
                    return NavDecision(direction="east", reached_waypoint=False, waypoint=wp)
                if nx == x - 1 and ny == y:
                    return NavDecision(direction="west", reached_waypoint=False, waypoint=wp)
                if nx == x and ny == y + 1:
                    return NavDecision(direction="south", reached_waypoint=False, waypoint=wp)
                if nx == x and ny == y - 1:
                    return NavDecision(direction="north", reached_waypoint=False, waypoint=wp)
            # Fallback to axis-greedy if A* can't find a path.
        else:
            # Clear A* debug fields when not using A*.
            self.last_astar_found = None
            self.last_astar_path_len = None
            self.last_astar_visited = None
            self.last_goal_local = None

        # Move one tile step, prefer axis with larger absolute distance.
        if abs(dx) >= abs(dy):
            if dx > 0:
                return NavDecision(direction="east", reached_waypoint=False, waypoint=wp)
            return NavDecision(direction="west", reached_waypoint=False, waypoint=wp)
        else:
            if dy > 0:
                return NavDecision(direction="south", reached_waypoint=False, waypoint=wp)
            return NavDecision(direction="north", reached_waypoint=False, waypoint=wp)
