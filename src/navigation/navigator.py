from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

from navigation.route import Waypoint


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

    def _at_waypoint(self, pos: Tuple[int, int], wp: Waypoint) -> bool:
        x, y = pos
        return abs(x - wp.x) <= self.tol and abs(y - wp.y) <= self.tol

    def decide(self, pos: Tuple[int, int]) -> NavDecision:
        wp = self.current_waypoint()
        if wp is None:
            return NavDecision(direction=None, reached_waypoint=False, waypoint=None)

        if self._at_waypoint(pos, wp):
            # reached: advance
            self.idx += 1
            return NavDecision(direction=None, reached_waypoint=True, waypoint=wp)

        x, y = pos
        dx = wp.x - x
        dy = wp.y - y

        # Move one tile step, prefer axis with larger absolute distance.
        if abs(dx) >= abs(dy):
            if dx > 0:
                return NavDecision(direction="east", reached_waypoint=False, waypoint=wp)
            return NavDecision(direction="west", reached_waypoint=False, waypoint=wp)
        else:
            if dy > 0:
                return NavDecision(direction="south", reached_waypoint=False, waypoint=wp)
            return NavDecision(direction="north", reached_waypoint=False, waypoint=wp)
