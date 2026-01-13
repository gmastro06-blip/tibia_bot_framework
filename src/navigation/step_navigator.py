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

        # When a coordinate waypoint is current, we may have non-coordinate steps
        # (labels/actions/conditional jumps) that should be processed *after* that
        # coordinate but before starting the next movement segment.
        self._after_idx: int | None = None

        # Conditional jumps should not "teleport" the simulated coordinate anchor.
        # Instead they select where we search for the next coordinate waypoint.
        self._next_from_idx: int | None = None

        self._segment_dx = 0
        self._segment_dy = 0
        self._segment_initialized = False

        # Indexar labels para saltos rápidos
        self._label_map = {}
        for i, wp in enumerate(self.route):
            if getattr(wp, "label", None):
                self._label_map[str(wp.label)] = i

    def _label_index(self, label: str) -> int | None:
        try:
            return self._label_map.get(str(label))
        except Exception:
            return None

    def _goto_label(self, label: str) -> bool:
        idx = self._label_index(label)
        if idx is not None:
            self.idx = idx
            self._segment_initialized = False
            self._after_idx = None
            self._next_from_idx = None
            return True
        return False

    def _env_truthy(self, name: str) -> bool | None:
        """Return True/False if env var exists, else None."""
        key = str(name or "").strip()
        if not key:
            return None
        raw = os.getenv(key)
        if raw is None:
            return None
        v = str(raw).strip().lower()
        return v in {"1", "true", "yes", "y", "on"}

    def _cond_value(self, var_name: str) -> bool:
        """Evaluate a conditional_jump var_name via env.

        Supported env keys (first match wins):
        - ROUTE_VAR_<VAR_NAME_UPPER>
        - CAVEBOT_VAR_<VAR_NAME_UPPER>
        - <var_name> (exact)
        Default: False
        """

        vn = str(var_name or "").strip()
        if not vn:
            return False
        up = vn.upper()
        for key in (f"ROUTE_VAR_{up}", f"CAVEBOT_VAR_{up}", vn):
            res = self._env_truthy(key)
            if res is not None:
                return bool(res)
        return False

    def _consume_nonpos_after_current(self) -> StepDecision | None:
        """Consume non-coordinate steps immediately after current idx.

        Semantics:
        - conditional_jump: rewires idx and continues consuming
        - action-only: returns a reached event so decision layer can build ActionRequests
        - label/comment/call/load: skip silently
        """

        if not self.route:
            return None

        def advance(i: int) -> int | None:
            j = int(i) + 1
            if j >= len(self.route):
                if self.loop:
                    return 0
                return None
            return j

        # Initialize the scanner for "after current coordinate" steps.
        if self._after_idx is None:
            if self._next_from_idx is not None:
                j0 = int(self._next_from_idx)
                if j0 < 0:
                    j0 = 0
                if j0 >= len(self.route):
                    j0 = 0 if self.loop else len(self.route)
                self._after_idx = j0
            else:
                j0 = self.idx + 1
                if j0 >= len(self.route):
                    if self.loop:
                        j0 = 0
                    else:
                        return None
                self._after_idx = j0

        while True:
            if self._after_idx is None:
                return None

            j = int(self._after_idx)
            if j < 0:
                j = 0
            if j >= len(self.route):
                if self.loop:
                    j = 0
                else:
                    return None
                self._after_idx = j

            nxt = self.route[j]
            has_xy = bool(getattr(nxt, "has_xy", True))
            if has_xy:
                # Done consuming post-steps; movement segment can start.
                return None

            cj = getattr(nxt, "conditional_jump", None)
            if isinstance(cj, dict) and cj:
                vn = str(cj.get("var_name", "") or "").strip()
                label_jump = str(cj.get("label_jump", "") or "").strip()
                label_skip = str(cj.get("label_skip", "") or "").strip()
                take = self._cond_value(vn)
                target = label_jump if take else label_skip
                if target:
                    li = self._label_index(target)
                    if li is not None:
                        # Select a new branch search origin, but keep the coordinate anchor.
                        self._next_from_idx = int(li)
                        self._after_idx = int(li)
                        self._segment_initialized = False
                        continue

                # If label missing/invalid, just skip this conditional step.
                nxt_i = advance(j)
                if nxt_i is None:
                    self._after_idx = None
                    return None
                self._after_idx = int(nxt_i)
                continue

            act = getattr(nxt, "action", None)
            if act:
                # Consume the action step (advance scanner) and surface it as a reached event.
                action_wp = nxt
                nxt_i = advance(j)
                if nxt_i is None:
                    self._after_idx = None
                else:
                    self._after_idx = int(nxt_i)
                self._segment_initialized = False
                return StepDecision(direction=None, reached_waypoint=True, waypoint=action_wp)

            # Skip label/call/load/comment-only steps.
            nxt_i = advance(j)
            if nxt_i is None:
                self._after_idx = None
                return None
            self._after_idx = int(nxt_i)
            self._segment_initialized = False
            continue

    def reset(self) -> None:
        self.idx = 0
        self._segment_dx = 0
        self._segment_dy = 0
        self._segment_initialized = False
        self._after_idx = None
        self._next_from_idx = None

    def jump_to(self, idx: int) -> bool:
        """Jump to a specific route index.

        This is UI/operator driven. It does not execute any inputs; it only
        rewinds/advances the internal pointer used by `preview()` / `decide()`.
        """

        try:
            if not self.route:
                return False
            j = int(idx)
            if j < 0:
                j = 0
            if j >= len(self.route):
                j = len(self.route) - 1
            self.idx = j
            self._segment_dx = 0
            self._segment_dy = 0
            self._segment_initialized = False
            self._after_idx = None
            self._next_from_idx = None
            return True
        except Exception:
            return False

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
        j = self._next_coord_index()
        return self.route[j] if (j is not None and self.route) else None

    def _next_coord_index(self) -> int | None:
        if not self.route:
            return None

        start = int(self._next_from_idx) if self._next_from_idx is not None else int(self.idx)
        j = start + 1
        visited = 0
        while visited < len(self.route):
            visited += 1
            if j >= len(self.route):
                if self.loop:
                    j = 0
                else:
                    return None
            wp = self.route[j]
            if bool(getattr(wp, "has_xy", True)):
                return int(j)
            j += 1
        return None

    def _ensure_segment(self) -> bool:
        """Initialize segment deltas once per waypoint-to-waypoint segment."""
        if self._segment_initialized:
            return True
        cur = self._current()
        nxt = self._next()
        if cur is None or nxt is None:
            return False
        # Only segments between coordinate waypoints.
        if not bool(getattr(cur, "has_xy", True)) or not bool(getattr(nxt, "has_xy", True)):
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

            # In step mode, keep `idx` anchored on a coordinate waypoint.
            # Skip any non-coordinate items we may land on (e.g. route starts with labels).
            if not bool(getattr(cur, "has_xy", True)):
                self.idx += 1
                self._segment_initialized = False
                self._after_idx = None
                self._next_from_idx = None
                continue

            # Skip non-coordinate steps at the current pointer.
            if not bool(getattr(cur, "has_xy", True)):
                # Conditional jump step at current pointer.
                cj = getattr(cur, "conditional_jump", None)
                if isinstance(cj, dict) and cj:
                    vn = str(cj.get("var_name", "") or "").strip()
                    label_jump = str(cj.get("label_jump", "") or "").strip()
                    label_skip = str(cj.get("label_skip", "") or "").strip()
                    take = self._cond_value(vn)
                    target = label_jump if take else label_skip
                    if target and self._goto_label(target):
                        continue

                # Action-only step: emit reached event.
                if getattr(cur, "action", None):
                    self.idx += 1
                    self._segment_initialized = False
                    return StepDecision(direction=None, reached_waypoint=True, waypoint=cur)

                # Everything else: skip silently.
                self.idx += 1
                self._segment_initialized = False
                continue

            # Before moving to the next coordinate, consume action/jump steps that
            # logically happen *after* arriving at the current coordinate.
            idx_before = int(self.idx)
            extra = self._consume_nonpos_after_current()
            if extra is not None:
                return extra
            # conditional_jump may have rewired idx; restart to re-load `cur`.
            if int(self.idx) != idx_before:
                continue

            # Ejecutar saltos condicionales si existen
            if getattr(cur, "conditional_jump", None):
                cj = cur.conditional_jump
                if isinstance(cj, dict) and cj:
                    vn = str(cj.get("var_name", "") or "").strip()
                    label_jump = str(cj.get("label_jump", "") or "").strip()
                    label_skip = str(cj.get("label_skip", "") or "").strip()
                    take = self._cond_value(vn)
                    target = label_jump if take else label_skip
                    if target and self._goto_label(target):
                        continue
                # Si no hay label válido, avanza normal
                self.idx += 1
                self._segment_initialized = False
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
                and not bool(getattr(cur, "has_xy", True))
                and not getattr(cur, "type", None)
            ) or (
                getattr(cur, "action", None) and not bool(getattr(cur, "has_xy", True))
            ) or getattr(cur, "call", None) or getattr(cur, "load", None) or getattr(cur, "comment", None):
                self.idx += 1
                self._segment_initialized = False
                continue

            # Si es un waypoint real, navega como antes
            nxt = self._next()
            if nxt is None:
                return StepDecision(direction=None, reached_waypoint=False, waypoint=None)

            # If the immediate next step is non-coordinate, consume it first (action/jump).
            # (This should usually be handled by _consume_nonpos_after_current(), but keep
            # this as a safety net.)
            if not bool(getattr(nxt, "has_xy", True)):
                extra2 = self._consume_nonpos_after_current()
                if extra2 is not None:
                    return extra2
                # If it's non-pos but not consumable, advance and retry.
                self.idx += 1
                self._segment_initialized = False
                continue

            if not self._ensure_segment():
                return StepDecision(direction=None, reached_waypoint=False, waypoint=None)

            if self._segment_dx == 0 and self._segment_dy == 0 and self._segment_initialized:
                reached = nxt
                # Advance idx to the next coordinate waypoint.
                next_idx = self._next_coord_index()
                if next_idx is None:
                    if self.loop and self.route:
                        self.idx = 0
                    else:
                        self.idx = len(self.route)
                else:
                    self.idx = int(next_idx)

                # Reset segment state for the next segment.
                self._segment_initialized = False
                self._segment_dx = 0
                self._segment_dy = 0
                self._after_idx = None
                self._next_from_idx = None
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

        # Skip non-coordinate steps at current pointer.
        guard = 0
        while guard < max(1, len(self.route)):
            guard += 1
            cur = self.route[idx]
            if bool(getattr(cur, "has_xy", True)):
                break
            # conditional jump at current pointer
            cj = getattr(cur, "conditional_jump", None)
            if isinstance(cj, dict) and cj:
                vn = str(cj.get("var_name", "") or "").strip()
                label_jump = str(cj.get("label_jump", "") or "").strip()
                label_skip = str(cj.get("label_skip", "") or "").strip()
                take = self._cond_value(vn)
                target = label_jump if take else label_skip
                if target and target in self._label_map:
                    idx = int(self._label_map[target])
                    seg_init = False
                    continue
            # action-only at current pointer => surface as reached
            if getattr(cur, "action", None):
                return StepDecision(direction=None, reached_waypoint=True, waypoint=cur)
            # skip
            idx += 1
            seg_init = False
            if idx >= len(self.route):
                if self.loop:
                    idx = 0
                else:
                    return StepDecision(direction=None, reached_waypoint=False, waypoint=None)

        cur = self.route[idx]

        # Consume non-coordinate steps immediately after current coordinate.
        j = idx + 1
        if j >= len(self.route):
            if self.loop:
                j = 0
            else:
                return StepDecision(direction=None, reached_waypoint=False, waypoint=None)
        nxt = self.route[j]
        if not bool(getattr(nxt, "has_xy", True)):
            # conditional jump step
            cj = getattr(nxt, "conditional_jump", None)
            if isinstance(cj, dict) and cj:
                vn = str(cj.get("var_name", "") or "").strip()
                label_jump = str(cj.get("label_jump", "") or "").strip()
                label_skip = str(cj.get("label_skip", "") or "").strip()
                take = self._cond_value(vn)
                target = label_jump if take else label_skip
                if target and target in self._label_map:
                    # Jump: next decision is based on new idx
                    idx = int(self._label_map[target])
                    seg_init = False
                    # Recurse by restarting preview with updated idx snapshot
                    # (simple: call preview logic again by temporarily setting self.idx)
                    # We avoid mutating real state by just continuing the loop.
                    # NOTE: This only handles one jump depth per preview; that's OK.
                    if idx < 0:
                        idx = 0
                    cur = self.route[idx]
                    # fall through to compute segment from the jump location
                    j = idx + 1
                    if j >= len(self.route):
                        if self.loop:
                            j = 0
                        else:
                            return StepDecision(direction=None, reached_waypoint=False, waypoint=None)
                    nxt = self.route[j]

            # action-only step
            if getattr(nxt, "action", None):
                return StepDecision(direction=None, reached_waypoint=True, waypoint=nxt)

            # otherwise skip (label/comment/call) by previewing the next after it
            j += 1
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
