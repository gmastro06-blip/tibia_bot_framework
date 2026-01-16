from __future__ import annotations

"""Win32 guardrails for OS input injection.

Assistant-first design: even when OS injection is enabled, we must ensure inputs
only go to the intended client window.

This module avoids external dependencies by using WinAPI via ctypes.
"""

from typing import List


def get_foreground_window_title() -> str:
    """Return the current foreground window title (best-effort).

    Returns an empty string if unavailable.
    """

    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""

        # GetWindowTextLengthW returns length *excluding* the null terminator.
        length = int(user32.GetWindowTextLengthW(hwnd))
        if length <= 0:
            return ""

        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return str(buf.value or "")
    except Exception:
        return ""


def is_target_window_active(allowed_titles: List[str]) -> bool:
    """Return True if the active window title matches one of allowed_titles.

    Matching is case-insensitive substring match. Empty/blank allowed titles are
    ignored. If allowed_titles is empty after normalization, returns False.
    """

    try:
        title = get_foreground_window_title().strip()
        if not title:
            return False

        allowed = [str(t).strip() for t in (allowed_titles or []) if str(t).strip()]
        if not allowed:
            return False

        t_low = title.lower()
        for cand in allowed:
            if cand.lower() in t_low:
                return True
        return False
    except Exception:
        return False
