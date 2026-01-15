from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from action.input_driver import ActionRequest, InputDriver, MockInputDriver, is_committed

try:
    from input_guard import get_foreground_window_title, is_target_window_active
except Exception:  # pragma: no cover
    get_foreground_window_title = None  # type: ignore[assignment]
    is_target_window_active = None  # type: ignore[assignment]


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

    # Live input policy (used only when injection_enabled is True).
    live_input_armed: bool = False
    allowed_window_titles: list[str] = field(default_factory=list)

    # Observability: last block reason (best-effort).
    last_block_reason: str = ""
    last_foreground_title: str = ""

    def set_driver(self, driver: Optional[InputDriver], *, injection_enabled: bool) -> None:
        self.driver = driver
        self.injection_enabled = bool(injection_enabled)

    def set_live_policy(self, *, live_input_armed: bool, allowed_window_titles: list[str] | None = None) -> None:
        self.live_input_armed = bool(live_input_armed)
        if allowed_window_titles is not None:
            out: list[str] = []
            for t in list(allowed_window_titles or []):
                s = str(t).strip()
                if not s:
                    continue
                out.append(s)
            self.allowed_window_titles = out

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

        # Reset per-call observability.
        self.last_block_reason = ""
        self.last_foreground_title = ""

        # Fail-closed always wins for OS injection, but we still allow safe
        # fallback drivers (mock/log) to record actions for observability.
        if str(self.disabled_reason or "").strip():
            self.last_block_reason = "fail_closed"
            if self.injection_enabled:
                return False

        # If we're injecting OS input, only committed actions are allowed.
        if self.injection_enabled:
            # Live input must be explicitly armed.
            if not bool(self.live_input_armed):
                self.last_block_reason = "not_armed"
                return False

            try:
                if not is_committed(action):
                    self.last_block_reason = "preview_only"
                    return False
            except Exception:
                self.last_block_reason = "preview_only"
                return False

            # Window-scoped injection: only when target window is active.
            try:
                if callable(get_foreground_window_title):
                    self.last_foreground_title = str(get_foreground_window_title() or "")
            except Exception:
                self.last_foreground_title = ""

            try:
                if callable(is_target_window_active):
                    ok = bool(is_target_window_active(list(self.allowed_window_titles or [])))
                else:
                    ok = False
            except Exception:
                ok = False

            if not ok:
                self.last_block_reason = "wrong_window"
                return False

        try:
            return bool(drv.send(action))
        except Exception:
            return False
