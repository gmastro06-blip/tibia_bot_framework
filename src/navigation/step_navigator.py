from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import List, Optional

from navigation.route import Waypoint


@dataclass
class StepDecision:
    direction: Optional[str] = None  # "north"|"south"|"east"|"west"|None
    reached_waypoint: bool = False
    waypoint: Optional[Waypoint] = None
    note: str = ""
    stuck_reason: str = ""



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

        # Optional stand wait (seconds) after reaching a stand tile.
        try:
            self._stand_wait_s = float(os.getenv("CAVEBOT_STAND_WAIT_S", "0").strip() or "0")
        except Exception:
            self._stand_wait_s = 0.0
        self._stand_wait_s = max(0.0, float(self._stand_wait_s))
        # Default scripts-master semantics: a stand step typically implies "arrive and pause".
        # We model this as at least 1 tick wait (configurable).
        try:
            self._stand_wait_ticks = int(float(os.getenv("CAVEBOT_STAND_WAIT_TICKS", "1").strip() or "1"))
        except Exception:
            self._stand_wait_ticks = 1
        self._stand_wait_ticks = max(0, int(self._stand_wait_ticks))
        self._stand_wait_done: bool = False
        self._stand_arrival_ts: float | None = None

        # Rope/ladder: wait for z-change confirmation after triggering the action.
        try:
            self._z_change_timeout_s = float(os.getenv("CAVEBOT_Z_CHANGE_TIMEOUT_S", "3").strip() or "3")
        except Exception:
            self._z_change_timeout_s = 3.0
        self._z_change_timeout_s = max(0.5, float(self._z_change_timeout_s))
        self._pending_z: dict[str, float | int | str] | None = None

        # Progress detection (for segment-mode fallback):
        self._last_pos: tuple[int, int, int | None] | None = None

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
        - action/call/load: returns a reached event so decision layer can build ActionRequests
        - label/comment: skip silently
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

            call = getattr(nxt, "call", None)
            if call:
                call_wp = nxt
                nxt_i = advance(j)
                if nxt_i is None:
                    self._after_idx = None
                else:
                    self._after_idx = int(nxt_i)
                self._segment_initialized = False
                return StepDecision(direction=None, reached_waypoint=True, waypoint=call_wp, note="call")

            load = getattr(nxt, "load", None)
            if load:
                load_wp = nxt
                nxt_i = advance(j)
                if nxt_i is None:
                    self._after_idx = None
                else:
                    self._after_idx = int(nxt_i)
                self._segment_initialized = False
                return StepDecision(direction=None, reached_waypoint=True, waypoint=load_wp, note="load")

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
        self._stand_wait_done = False

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
            self._stand_wait_done = False
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

    def _pos_from_gamestate(self, gamestate) -> tuple[int, int, int | None] | None:
        try:
            if gamestate is None:
                return None
            x = getattr(gamestate, "pos_x", None)
            y = getattr(gamestate, "pos_y", None)
            z = getattr(gamestate, "pos_z", None)
            if x is None or y is None:
                return None
            return (int(x), int(y), int(z) if z is not None else None)
        except Exception:
            return None

    def _progressed(self, pos: tuple[int, int, int | None] | None, gamestate) -> bool:
        """Best-effort: True when we can infer movement happened since last tick."""

        try:
            if pos is not None:
                if self._last_pos is None:
                    self._last_pos = pos
                    return False
                moved = pos != self._last_pos
                self._last_pos = pos
                if moved:
                    return True

            # Minimap motion (experimental provider) can be used as a weak progress signal.
            dx = getattr(gamestate, "minimap_delta_dx", None) if gamestate is not None else None
            dy = getattr(gamestate, "minimap_delta_dy", None) if gamestate is not None else None
            if dx is None and dy is None:
                return False
            try:
                return (abs(float(dx or 0.0)) + abs(float(dy or 0.0))) >= 0.25
            except Exception:
                return False
        except Exception:
            return False

    def _at_tile(
        self,
        pos: tuple[int, int, int | None],
        *,
        x: int,
        y: int,
        z: int | None,
        require_z: bool,
    ) -> bool:
        try:
            if int(pos[0]) != int(x) or int(pos[1]) != int(y):
                return False
            if require_z and z is not None and pos[2] is not None:
                return int(pos[2]) == int(z)
            return True
        except Exception:
            return False

    def _direction_to(self, pos: tuple[int, int, int | None], *, x: int, y: int) -> str | None:
        """Direction using |delta|-priority. Tibia: +y is south."""

        try:
            dx = int(x) - int(pos[0])
            dy = int(y) - int(pos[1])
            if dx == 0 and dy == 0:
                return None
            if abs(dx) >= abs(dy):
                return "east" if dx > 0 else "west"
            return "south" if dy > 0 else "north"
        except Exception:
            return None


    def decide(self, gamestate=None, config=None) -> StepDecision:
        """Consume 1 tick de navegación (mutando estado interno).

        - If coords are available on the GameState, we navigate to absolute tiles.
        - For rope/ladder, we wait for z-change confirmation (z-1) with timeout.
        - If no coords are available, we fall back to legacy segment stepping.
        """

        now = time.time()
        pos = self._pos_from_gamestate(gamestate)

        progressed = self._progressed(pos, gamestate)

        # Segment stepping is a legacy fallback when we don't have coords.
        # In tests (and some headless uses) we assume each suggested move succeeds.
        # In *steps-mode* we must NOT invent progress: only coords/minimap-motion can confirm.
        #
        # IMPORTANT: Do not let ambient env vars (e.g. CAVEBOT_MODE=steps) change
        # pure unit-test/simulation behavior when no GameState is provided.
        strict_no_coords_progress = False
        if gamestate is not None:
            try:
                mode = str(getattr(config, "mode", "") or "").strip().lower()
                force_steps = bool(getattr(config, "force_steps", False))
                strict_no_coords_progress = force_steps or (mode in {"steps", "step"})
            except Exception:
                strict_no_coords_progress = False

            if not strict_no_coords_progress:
                try:
                    env_mode = str(os.getenv("CAVEBOT_MODE", "") or "").strip().lower()
                    if env_mode in {"steps", "step"}:
                        strict_no_coords_progress = True
                except Exception:
                    pass

        if pos is None and (not progressed) and (not strict_no_coords_progress):
            progressed = True

        while True:
            cur = self._current()
            if cur is None:
                return StepDecision(direction=None, reached_waypoint=False, waypoint=None)

            has_xy = bool(getattr(cur, "has_xy", True))
            if not has_xy:
                # Conditional jump
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
                    self._pending_z = None
                    self._stand_arrival_ts = None
                    return StepDecision(direction=None, reached_waypoint=True, waypoint=cur)

                # scripts-master: call/load are control/meta steps that should be surfaced.
                if getattr(cur, "call", None):
                    self.idx += 1
                    self._segment_initialized = False
                    self._pending_z = None
                    self._stand_arrival_ts = None
                    return StepDecision(direction=None, reached_waypoint=True, waypoint=cur, note="call")

                if getattr(cur, "load", None):
                    self.idx += 1
                    self._segment_initialized = False
                    self._pending_z = None
                    self._stand_arrival_ts = None
                    return StepDecision(direction=None, reached_waypoint=True, waypoint=cur, note="load")

                # Skip label/call/load/comment silently.
                self.idx += 1
                self._segment_initialized = False
                continue

            # Absolute navigation when we have coords.
            if pos is not None:
                step_type = str(getattr(cur, "type", "") or "node").strip().lower() or "node"
                tx = int(getattr(cur, "x", 0))
                ty = int(getattr(cur, "y", 0))
                tz_raw = getattr(cur, "z", None)
                tz = int(tz_raw) if tz_raw is not None else None
                require_z = tz is not None

                # If we were waiting for a z-change but moved away, cancel it.
                try:
                    if self._pending_z is not None:
                        v_idx = self._pending_z.get("idx", -1)
                        pend_idx = int(v_idx) if v_idx is not None else -1
                        # Pending z-change should not be cancelled just because z differs.
                        # Only cancel when x/y changes or the idx changes.
                        if pend_idx != int(self.idx) or not self._at_tile(pos, x=tx, y=ty, z=tz, require_z=False):
                            self._pending_z = None
                except Exception:
                    self._pending_z = None

                if step_type in {"node", "stand"}:
                    at = self._at_tile(pos, x=tx, y=ty, z=tz, require_z=require_z)
                    if at:
                        if step_type == "stand":
                            # Always wait at least one tick (unless explicitly disabled).
                            if not bool(self._stand_wait_done) and int(self._stand_wait_ticks) > 0:
                                self._stand_wait_done = True
                                self._stand_arrival_ts = float(now)
                                return StepDecision(direction=None, reached_waypoint=False, waypoint=cur, note="stand_wait")
                            # Optional additional time-based wait.
                            if self._stand_wait_s > 0.0:
                                if self._stand_arrival_ts is None:
                                    self._stand_arrival_ts = float(now)
                                    return StepDecision(direction=None, reached_waypoint=False, waypoint=cur, note="stand_wait")
                                if (now - float(self._stand_arrival_ts)) < float(self._stand_wait_s):
                                    return StepDecision(direction=None, reached_waypoint=False, waypoint=cur, note="stand_wait")

                        # Arrived: advance to next step (so actions/labels get processed).
                        self._stand_arrival_ts = None
                        self._stand_wait_done = False
                        self.idx += 1
                        self._segment_initialized = False
                        continue

                    # Not at target: produce movement direction.
                    self._stand_arrival_ts = None
                    self._stand_wait_done = False
                    d = self._direction_to(pos, x=tx, y=ty)
                    return StepDecision(direction=d, reached_waypoint=False, waypoint=cur)

                if step_type in {"rope", "ladder"}:
                    at_xy = (int(pos[0]) == int(tx)) and (int(pos[1]) == int(ty))
                    at_xyz = self._at_tile(pos, x=tx, y=ty, z=tz, require_z=require_z)

                    expected_z: int | None = None
                    try:
                        if tz is not None:
                            expected_z = int(tz) - 1
                        elif pos[2] is not None:
                            expected_z = int(pos[2]) - 1
                    except Exception:
                        expected_z = None

                    # If we are already waiting for a z-change on this step, we must NOT
                    # require staying on the original z; z is expected to change.
                    pending = self._pending_z
                    pending_dict: dict[str, float | int | str] | None = pending if isinstance(pending, dict) else None
                    pending_idx: int | None = None
                    if pending_dict is not None:
                        try:
                            v_idx2 = pending_dict.get("idx", -1)
                            pending_idx = int(v_idx2) if v_idx2 is not None else -1
                        except Exception:
                            pending_idx = -1

                    is_pending_here = (pending_idx is not None and int(pending_idx) == int(self.idx))
                    if is_pending_here:
                        if expected_z is not None and pos[2] is not None:
                            try:
                                if int(pos[2]) == int(expected_z):
                                    self._pending_z = None
                                    self.idx += 1
                                    self._segment_initialized = False
                                    continue
                            except Exception:
                                pass

                        # If we moved away in x/y, cancel pending and navigate back.
                        if not at_xy:
                            self._pending_z = None
                            d = self._direction_to(pos, x=tx, y=ty)
                            return StepDecision(direction=d, reached_waypoint=False, waypoint=cur)

                        # Still waiting on same x/y.
                        age = 0.0
                        try:
                            if pending_dict is not None:
                                age = float(now) - float(float(pending_dict.get("start_ts", now) or now))
                        except Exception:
                            age = 0.0

                        if age >= float(self._z_change_timeout_s):
                            return StepDecision(
                                direction=None,
                                reached_waypoint=False,
                                waypoint=cur,
                                note=f"waiting_z->{expected_z}",
                                stuck_reason=f"{step_type.upper()}_TIMEOUT",
                            )
                        return StepDecision(direction=None, reached_waypoint=False, waypoint=cur, note=f"waiting_z->{expected_z}")

                    # Not pending yet: we must reach the tool tile (including its z) first.
                    if not at_xyz:
                        self._stand_arrival_ts = None
                        d = self._direction_to(pos, x=tx, y=ty)
                        return StepDecision(direction=d, reached_waypoint=False, waypoint=cur)

                    if expected_z is None or pos[2] is None:
                        # Can't confirm z; emit action once and move on.
                        self.idx += 1
                        self._pending_z = None
                        self._segment_initialized = False
                        return StepDecision(direction=None, reached_waypoint=True, waypoint=cur, note=f"{step_type}_no_z")

                    self._pending_z = {
                        "idx": int(self.idx),
                        "type": str(step_type),
                        "start_ts": float(now),
                        "expected_z": int(expected_z),
                    }
                    # Surface as an action trigger exactly once; subsequent ticks will return waiting_z.
                    return StepDecision(direction=None, reached_waypoint=True, waypoint=cur, note=f"{step_type}_trigger")

                # Unknown coordinate type: treat as a normal node.
                at = self._at_tile(pos, x=tx, y=ty, z=tz, require_z=require_z)
                if at:
                    self.idx += 1
                    self._segment_initialized = False
                    continue
                d = self._direction_to(pos, x=tx, y=ty)
                return StepDecision(direction=d, reached_waypoint=False, waypoint=cur)

            # No coords: legacy segment stepping.
            # Before moving, consume non-pos steps that are meant to happen
            # immediately after the current coordinate anchor.
            idx_before = int(self.idx)
            extra = self._consume_nonpos_after_current()
            if extra is not None:
                return extra
            if int(self.idx) != idx_before:
                continue

            nxt = self._next()
            if nxt is None:
                return StepDecision(direction=None, reached_waypoint=False, waypoint=None)

            if not bool(getattr(nxt, "has_xy", True)):
                # If next is non-pos, just advance and retry.
                self.idx += 1
                self._segment_initialized = False
                continue

            if not self._ensure_segment():
                return StepDecision(direction=None, reached_waypoint=False, waypoint=None)

            if self._segment_dx == 0 and self._segment_dy == 0 and self._segment_initialized:
                reached = nxt
                next_idx = self._next_coord_index()
                if next_idx is None:
                    if self.loop and self.route:
                        self.idx = 0
                    else:
                        self.idx = len(self.route)
                else:
                    self.idx = int(next_idx)
                self._segment_initialized = False
                self._segment_dx = 0
                self._segment_dy = 0
                self._after_idx = None
                self._next_from_idx = None
                return StepDecision(direction=None, reached_waypoint=True, waypoint=reached)

            # Choose axis by remaining |delta| (tie -> dx).
            dx = int(self._segment_dx)
            dy = int(self._segment_dy)
            if abs(dx) >= abs(dy) and dx != 0:
                if dx > 0:
                    if progressed:
                        self._segment_dx -= 1
                    return StepDecision(direction="east", reached_waypoint=False, waypoint=nxt)
                if progressed:
                    self._segment_dx += 1
                return StepDecision(direction="west", reached_waypoint=False, waypoint=nxt)

            if dy != 0:
                if dy > 0:
                    if progressed:
                        self._segment_dy -= 1
                    return StepDecision(direction="south", reached_waypoint=False, waypoint=nxt)
                if progressed:
                    self._segment_dy += 1
                return StepDecision(direction="north", reached_waypoint=False, waypoint=nxt)

            return StepDecision(direction=None, reached_waypoint=False, waypoint=nxt)

    def preview(self, gamestate=None, config=None) -> StepDecision:
        """Devuelve la próxima decisión sin mutar estado interno.

        Útil para modo 'asistente' donde el usuario confirma cada paso.
        """

        # Snapshot mutable state and reuse decide() for correctness.
        snap = (
            int(self.idx),
            int(self._segment_dx),
            int(self._segment_dy),
            bool(self._segment_initialized),
            self._after_idx,
            self._next_from_idx,
            self._stand_arrival_ts,
            dict(self._pending_z) if isinstance(self._pending_z, dict) else None,
            self._last_pos,
        )
        try:
            return self.decide(gamestate=gamestate, config=config)
        finally:
            (
                self.idx,
                self._segment_dx,
                self._segment_dy,
                self._segment_initialized,
                self._after_idx,
                self._next_from_idx,
                self._stand_arrival_ts,
                pending_z,
                self._last_pos,
            ) = snap
            self._pending_z = pending_z
