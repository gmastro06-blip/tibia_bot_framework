from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from navigation.route import Waypoint


@dataclass
class StepDecision:
    direction: Optional[str] = None  # "north"|"south"|"east"|"west"|None
    reached_waypoint: bool = False
    waypoint: Optional[Waypoint] = None



class StepNavigator:
    """Cavebot avanzado: soporta labels, saltos condicionales y acciones especiales."""

    def __init__(self, route: List[Waypoint]):
        self.route = route
        self.idx = 0
        self.loop = os.getenv("CAVEBOT_LOOP", "1").strip().lower() in {"1", "true", "yes"}

        self._segment_dx = 0
        self._segment_dy = 0
        self._segment_initialized = False

        # Indexar labels para saltos rápidos
        self._label_map = {}
        for i, wp in enumerate(self.route):
            if getattr(wp, "label", None):
                self._label_map[str(wp.label)] = i

    def _goto_label(self, label: str) -> bool:
        idx = self._label_map.get(str(label))
        if idx is not None:
            self.idx = idx
            self._segment_initialized = False
            return True
        return False

    def reset(self) -> None:
        self.idx = 0
        self._segment_dx = 0
        self._segment_dy = 0
        self._segment_initialized = False

    def _current(self) -> Optional[Waypoint]:
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

    def _next(self) -> Optional[Waypoint]:
        if not self.route:
            return None
        j = self.idx + 1
        if j >= len(self.route):
            if self.loop:
                j = 0
            else:
                return None
        return self.route[j]

    def _ensure_segment(self) -> bool:
        """Initialize segment deltas once per waypoint-to-waypoint segment."""
        if self._segment_initialized:
            return True
        cur = self._current()
        nxt = self._next()
        if cur is None or nxt is None:
            return False
        self._segment_dx = int(nxt.x) - int(cur.x)
        self._segment_dy = int(nxt.y) - int(cur.y)
        self._segment_initialized = True
        return True


    def decide(self, gamestate=None, config=None) -> StepDecision:
        """Consume 1 'tick' de navegación (mutando estado interno). Soporta labels y saltos condicionales."""
        while True:
            cur = self._current()
            if cur is None:
                return StepDecision(direction=None, reached_waypoint=False, waypoint=None)

            # Ejecutar saltos condicionales si existen
            if getattr(cur, "conditional_jump", None):
                cj = cur.conditional_jump
                # Lógica de condición: por ahora, simula True siempre (puedes adaptar aquí)
                # Ejemplo: if config and cj.get("var_name") and config.get(cj["var_name"]): ...
                label_jump = cj.get("label_jump")
                label_skip = cj.get("label_skip")
                # Simulación: siempre salta a label_jump si existe
                if label_jump and self._goto_label(label_jump):
                    continue
                elif label_skip and self._goto_label(label_skip):
                    continue
                else:
                    # Si no hay label válido, avanza normal
                    self.idx += 1
                    continue

            # Ejecutar call/load como hooks (puedes extender aquí)
            if getattr(cur, "call", None):
                # Aquí podrías ejecutar un sub-script, función, etc.
                print(f"[StepNavigator] call: {cur.call} (raw: {cur.raw_line})")
            if getattr(cur, "load", None):
                print(f"[StepNavigator] load: {cur.load} (raw: {cur.raw_line})")

            # Ejecutar acción especial
            if getattr(cur, "action", None):
                print(f"[StepNavigator] action: {cur.action} (raw: {cur.raw_line})")

            # Loggear comentarios
            if getattr(cur, "comment", None):
                print(f"[StepNavigator] comment: {cur.comment} (raw: {cur.raw_line})")

            # Si el paso es solo label, acción, call, load, comentario, avanza
            if (
                getattr(cur, "label", None)
                and not (hasattr(cur, "x") and hasattr(cur, "y"))
                and not getattr(cur, "type", None)
            ) or (
                getattr(cur, "action", None) and not (hasattr(cur, "x") and hasattr(cur, "y"))
            ) or getattr(cur, "call", None) or getattr(cur, "load", None) or getattr(cur, "comment", None):
                self.idx += 1
                continue

            # Si es un waypoint real, navega como antes
            nxt = self._next()
            if nxt is None:
                return StepDecision(direction=None, reached_waypoint=False, waypoint=None)

            if not self._ensure_segment():
                return StepDecision(direction=None, reached_waypoint=False, waypoint=None)

            if self._segment_dx == 0 and self._segment_dy == 0 and self._segment_initialized:
                reached = nxt
                # Advance idx
                if self.idx + 1 < len(self.route):
                    self.idx += 1
                else:
                    if self.loop:
                        self.idx = 0
                    else:
                        self.idx = len(self.route)

                # Reset segment state for the next segment.
                self._segment_initialized = False
                self._segment_dx = 0
                self._segment_dy = 0
                return StepDecision(direction=None, reached_waypoint=True, waypoint=reached)

            # Execute dx first, then dy. (Deterministic)
            if self._segment_dx != 0:
                if self._segment_dx > 0:
                    self._segment_dx -= 1
                    return StepDecision(direction="east", reached_waypoint=False, waypoint=nxt)
                self._segment_dx += 1
                return StepDecision(direction="west", reached_waypoint=False, waypoint=nxt)

            if self._segment_dy != 0:
                if self._segment_dy > 0:
                    self._segment_dy -= 1
                    return StepDecision(direction="south", reached_waypoint=False, waypoint=nxt)
                self._segment_dy += 1
                return StepDecision(direction="north", reached_waypoint=False, waypoint=nxt)

            # If we get here, the segment is complete but we haven't emitted the reached event yet.
            # Next call will emit it.
            return StepDecision(direction=None, reached_waypoint=False, waypoint=nxt)

    def preview(self) -> StepDecision:
        """Devuelve la próxima decisión sin mutar estado interno.

        Útil para modo 'asistente' donde el usuario confirma cada paso.
        """

        # Snapshot state
        idx = int(self.idx)
        seg_dx = int(self._segment_dx)
        seg_dy = int(self._segment_dy)
        seg_init = bool(self._segment_initialized)

        # Helpers equivalent to _current/_next but using local idx.
        if not self.route:
            return StepDecision(direction=None, reached_waypoint=False, waypoint=None)

        if idx < 0:
            idx = 0
        if idx >= len(self.route):
            if self.loop:
                idx = 0
            else:
                return StepDecision(direction=None, reached_waypoint=False, waypoint=None)

        cur = self.route[idx]
        j = idx + 1
        if j >= len(self.route):
            if self.loop:
                j = 0
            else:
                return StepDecision(direction=None, reached_waypoint=False, waypoint=None)
        nxt = self.route[j]

        if not seg_init:
            seg_dx = int(nxt.x) - int(cur.x)
            seg_dy = int(nxt.y) - int(cur.y)
            seg_init = True

        if seg_dx == 0 and seg_dy == 0 and seg_init:
            return StepDecision(direction=None, reached_waypoint=True, waypoint=nxt)

        if seg_dx != 0:
            if seg_dx > 0:
                return StepDecision(direction="east", reached_waypoint=False, waypoint=nxt)
            return StepDecision(direction="west", reached_waypoint=False, waypoint=nxt)

        if seg_dy != 0:
            if seg_dy > 0:
                return StepDecision(direction="south", reached_waypoint=False, waypoint=nxt)
            return StepDecision(direction="north", reached_waypoint=False, waypoint=nxt)

        return StepDecision(direction=None, reached_waypoint=False, waypoint=nxt)
