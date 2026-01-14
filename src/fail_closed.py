from __future__ import annotations

import threading
from typing import Optional

from action.input_manager import InputManager


def fail_closed(
    *,
    stop_event: threading.Event,
    input_manager: Optional[InputManager],
    reason: str,
    set_stop: bool = True,
) -> None:
    """Disable OS input and optionally stop the bot.

    This is a safety function meant to be called from watchdogs or when critical
    vision/decision signals are missing.
    """

    try:
        if input_manager is not None:
            input_manager.disable(reason)
    except Exception:
        pass

    if set_stop:
        try:
            stop_event.set()
        except Exception:
            pass
