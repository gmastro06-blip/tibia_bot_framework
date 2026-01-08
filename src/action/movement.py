from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional


def _key_for_direction(direction: str) -> Optional[str]:
    # Defaults assume WASD.
    mapping = {
        "north": os.getenv("CAVEBOT_KEY_N", "w").strip() or "w",
        "south": os.getenv("CAVEBOT_KEY_S", "s").strip() or "s",
        "west": os.getenv("CAVEBOT_KEY_W", "a").strip() or "a",
        "east": os.getenv("CAVEBOT_KEY_E", "d").strip() or "d",
    }
    return mapping.get(direction)


@dataclass
class MoveExecutor:
    step_interval_s: float = 0.35
    _last_step_ts: float = 0.0

    def maybe_step(self, direction: Optional[str]) -> bool:
        """Hace un paso en la dirección si corresponde y respeta cooldown."""
        if not direction:
            return False

        now = time.time()
        if now - self._last_step_ts < float(self.step_interval_s):
            return False

        key = _key_for_direction(direction)
        if not key:
            return False

        # Import lazily so unit tests or headless environments don't break.
        try:
            import keyboard  # type: ignore

            keyboard.press_and_release(key)
            self._last_step_ts = now
            return True
        except Exception:
            return False
