from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from action.input_driver import ActionRequest, InputDriver, MockInputDriver, is_committed


@dataclass
class InputManager:
    """Centralizes input safety policy.

    - Default behavior remains assistant-first (preview actions are allowed to be
      recorded by mock drivers).
    - When an OS-injecting driver is active, only committed actions may be sent.
    - When disabled, the manager swaps to a safe fallback driver and blocks any
      injection.
    """

    driver: Optional[InputDriver]
    fallback: Optional[MockInputDriver]
    injection_enabled: bool = False
    disabled_reason: str = ""

    def set_driver(self, driver: Optional[InputDriver], *, injection_enabled: bool) -> None:
        self.driver = driver
        self.injection_enabled = bool(injection_enabled)

    def disable(self, reason: str) -> None:
        self.disabled_reason = str(reason or "disabled")
        # Fail-closed: switch to mock driver if available, and mark injection off.
        if self.fallback is not None:
            self.driver = self.fallback
        self.injection_enabled = False

    def send(self, action: ActionRequest) -> bool:
        drv = self.driver
        if drv is None:
            return False

        # If we're injecting OS input, only committed actions are allowed.
        if self.injection_enabled:
            try:
                if not is_committed(action):
                    return False
            except Exception:
                return False

        try:
            return bool(drv.send(action))
        except Exception:
            return False
