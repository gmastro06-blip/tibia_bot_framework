from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class PeriodicTrigger:
    """Simple periodic trigger.

    Returns True at most once per interval.
    """

    interval_s: float
    _last_ts: float = 0.0

    def should_fire(self, now: Optional[float] = None) -> bool:
        if self.interval_s <= 0:
            return False
        t = time.time() if now is None else float(now)
        if self._last_ts <= 0.0:
            self._last_ts = t
            return True
        if (t - self._last_ts) >= float(self.interval_s):
            self._last_ts = t
            return True
        return False
