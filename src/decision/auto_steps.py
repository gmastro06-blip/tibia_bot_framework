from __future__ import annotations

import time
from dataclasses import dataclass


_LEVEL_RANK = {
    "green": 2,
    "amber": 1,
    "yellow": 1,
    "red": 0,
}


def _rank(level: str | None) -> int:
    return _LEVEL_RANK.get((level or "").strip().lower(), -1)


@dataclass
class AutoStepFallback:
    """Stateful helper to fall back to step mode when coords confidence is low.

    Assistant-only: returns a boolean to pick StepNavigator instead of Navigator.
    Uses a small hysteresis (activate vs recover) to avoid flapping.
    """

    enabled: bool = True
    activate_level: str = "red"
    activate_s: float = 1.5
    recover_level: str = "amber"
    recover_s: float = 2.0

    _active: bool = False
    _last_bad_ts: float = -1.0
    _last_good_ts: float = -1.0

    def update(
        self,
        level: str | None,
        *,
        has_coords: bool,
        base_steps: bool,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """Return (use_steps, reason).

        - When base_steps is True, always returns (True, "") and clears auto state.
        - Activates after `activate_s` seconds of level <= activate_level or no coords.
        - Recovers after `recover_s` seconds of level >= recover_level *and* coords present.
        """

        t = float(time.time() if now is None else now)
        lvl = (level or "").strip().lower()
        lvl_rank = _rank(lvl)
        act_rank = _rank(self.activate_level)
        rec_rank = _rank(self.recover_level)

        if base_steps:
            self._active = False
            self._last_bad_ts = -1.0
            self._last_good_ts = -1.0
            return True, ""

        if not self.enabled:
            self._active = False
            return False, ""

        bad = (lvl_rank >= 0 and lvl_rank <= act_rank) or not bool(has_coords)
        good = lvl_rank >= rec_rank >= 0 and bool(has_coords)

        if bad:
            if self._last_bad_ts < 0.0:
                self._last_bad_ts = t
        else:
            self._last_bad_ts = -1.0

        if self._active:
            # Track recovery window.
            if good:
                if self._last_good_ts < 0.0:
                    self._last_good_ts = t
                elif (t - self._last_good_ts) >= float(self.recover_s):
                    self._active = False
                    self._last_bad_ts = -1.0
            else:
                self._last_good_ts = -1.0
        else:
            if bad and self._last_bad_ts >= 0.0 and (t - self._last_bad_ts) >= float(self.activate_s):
                self._active = True
                self._last_good_ts = -1.0

        use_steps = bool(self._active)
        reason = f"auto_steps:{lvl or ('no_coords' if not has_coords else 'unknown')}" if use_steps else ""
        return use_steps, reason
