from __future__ import annotations

"""Client window discovery + focus guard.

This module has two responsibilities:

1) Discover a likely game client HWND even if it's not foreground.
2) Enforce a strict focus guard for OS input injection:
   - Only inject when foreground_hwnd == client_hwnd
   - Block when minimized

Defaults are designed to work out-of-the-box (no manual config).
"""

from dataclasses import dataclass
import time
from typing import Iterable, Optional

import win_window


DEFAULT_ALLOWED_WINDOW_TITLES: list[str] = ["Tibia"]
DEFAULT_DENIED_TITLES_CONTAINS: list[str] = [
    "Chrome",
    "Discord",
    "Visual Studio Code",
    "Steam",
    "Terminal",
    "Explorer",
]

# Heuristic hints only (soft): these are common for game clients.
DEFAULT_CLASS_HINTS: list[str] = ["SDL", "Qt", "Unity", "Unreal", "GLFW", "DirectX"]


@dataclass
class ClientWindowState:
    hwnd: int = 0
    title: str = ""
    class_name: str = ""
    pid: int = 0
    is_foreground: bool = False
    is_minimized: bool = False
    is_maximized: bool = False
    bounds: tuple[int, int, int, int] | None = None
    ts: float = 0.0


_client_state = ClientWindowState()


def set_client_hwnd(hwnd: int) -> None:
    """Set the current client HWND snapshot (best-effort)."""

    try:
        _client_state.hwnd = int(hwnd or 0)
    except Exception:
        _client_state.hwnd = 0


def get_client_hwnd() -> int:
    try:
        return int(_client_state.hwnd or 0)
    except Exception:
        return 0


def _norm_list(xs: Iterable[str] | None) -> list[str]:
    out: list[str] = []
    for x in list(xs or []):
        s = str(x).strip()
        if not s:
            continue
        out.append(s)
    return out


def _score_window(
    *,
    title: str,
    class_name: str,
    allowed_titles: list[str],
    denied_titles_contains: list[str],
    class_hints: list[str],
) -> int:
    t = (title or "").strip()
    c = (class_name or "").strip()
    if not t and not c:
        return 0

    t_low = t.lower()
    c_low = c.lower()

    for bad in denied_titles_contains:
        if bad and str(bad).lower() in t_low:
            return -999

    score = 0
    for good in allowed_titles:
        if good and str(good).lower() in t_low:
            score += 5

    for hint in class_hints:
        if hint and str(hint).lower() in c_low:
            score += 2

    return int(score)


def find_client_hwnd_anywhere(
    *,
    allowed_window_titles: list[str] | None = None,
    denied_titles_contains: list[str] | None = None,
) -> int | None:
    """Find the most likely client HWND among visible windows."""

    win_window.set_dpi_awareness()

    allowed = _norm_list(allowed_window_titles) or list(DEFAULT_ALLOWED_WINDOW_TITLES)
    denied = _norm_list(denied_titles_contains) or list(DEFAULT_DENIED_TITLES_CONTAINS)
    hints = list(DEFAULT_CLASS_HINTS)

    best_hwnd: int | None = None
    best_score = 0

    for hwnd in win_window.enum_top_level_windows():
        try:
            title = win_window.get_window_title(hwnd)
            cls = win_window.get_window_class(hwnd)
            s = _score_window(
                title=title,
                class_name=cls,
                allowed_titles=allowed,
                denied_titles_contains=denied,
                class_hints=hints,
            )
            if s > best_score:
                best_score = int(s)
                best_hwnd = int(hwnd)
        except Exception:
            continue

    return best_hwnd if (best_hwnd is not None and best_score > 0) else None


def refresh_client_hwnd(
    prev_hwnd: int | None,
    *,
    allowed_window_titles: list[str] | None = None,
    denied_titles_contains: list[str] | None = None,
) -> int:
    """Refresh/keep client HWND across recreation and background states."""

    allowed = _norm_list(allowed_window_titles) or list(DEFAULT_ALLOWED_WINDOW_TITLES)

    try:
        if prev_hwnd:
            title = win_window.get_window_title(int(prev_hwnd))
            if title:
                t_low = title.lower()
                if any(str(a).lower() in t_low for a in allowed if str(a).strip()):
                    return int(prev_hwnd)
    except Exception:
        pass

    found = find_client_hwnd_anywhere(
        allowed_window_titles=allowed_window_titles,
        denied_titles_contains=denied_titles_contains,
    )
    return int(found or 0)


def update_client_state(
    *,
    allowed_window_titles: list[str] | None = None,
    denied_titles_contains: list[str] | None = None,
) -> ClientWindowState:
    """Recompute and store a full client snapshot (best-effort)."""

    prev = get_client_hwnd()
    hwnd = refresh_client_hwnd(
        prev,
        allowed_window_titles=allowed_window_titles,
        denied_titles_contains=denied_titles_contains,
    )

    fg = win_window.get_foreground_hwnd()

    st = ClientWindowState()
    st.hwnd = int(hwnd or 0)
    st.title = win_window.get_window_title(st.hwnd) if st.hwnd else ""
    st.class_name = win_window.get_window_class(st.hwnd) if st.hwnd else ""
    st.pid = win_window.get_window_pid(st.hwnd) if st.hwnd else 0
    st.is_foreground = bool(st.hwnd and fg and int(fg) == int(st.hwnd))
    st.is_minimized = bool(st.hwnd and win_window.is_minimized(st.hwnd))
    st.is_maximized = bool(st.hwnd and win_window.is_maximized(st.hwnd))
    st.bounds = win_window.get_best_bounds(st.hwnd) if st.hwnd else None
    st.ts = time.time()

    # Store
    try:
        _client_state.hwnd = st.hwnd
        _client_state.title = st.title
        _client_state.class_name = st.class_name
        _client_state.pid = st.pid
        _client_state.is_foreground = st.is_foreground
        _client_state.is_minimized = st.is_minimized
        _client_state.is_maximized = st.is_maximized
        _client_state.bounds = st.bounds
        _client_state.ts = st.ts
    except Exception:
        pass

    return st


def get_client_state_snapshot() -> ClientWindowState:
    """Return the last stored client snapshot."""

    try:
        return ClientWindowState(**_client_state.__dict__)
    except Exception:
        # Fallback: return the shared instance (read-only intent).
        return _client_state


def is_allowed_to_inject(client_hwnd: int | None) -> tuple[bool, str]:
    """Strict focus guard for OS input injection."""

    try:
        hwnd = int(client_hwnd or 0)
    except Exception:
        hwnd = 0

    if not hwnd:
        return False, "no_client_hwnd"

    try:
        if win_window.is_minimized(hwnd):
            return False, "client_minimized"
    except Exception:
        # If unsure, fail closed.
        return False, "client_minimized"

    try:
        fg = int(win_window.get_foreground_hwnd() or 0)
    except Exception:
        fg = 0

    if fg != int(hwnd):
        return False, "not_foreground"

    return True, "ok"


def format_client_overlay_line(*, injection_state: str = "", injection_reason: str = "") -> str:
    st = get_client_state_snapshot()
    title = (st.title or "").strip() or "<not found>"
    fg = 1 if bool(st.is_foreground) else 0
    mn = 1 if bool(st.is_minimized) else 0
    mx = 1 if bool(st.is_maximized) else 0

    inj = (injection_state or "").strip()
    rsn = (injection_reason or "").strip()
    inj_s = ""
    if inj or rsn:
        inj_s = f" | INJECT={inj}({rsn})"

    return f"CLIENT: {title} | FG={fg} MIN={mn} MAX={mx}{inj_s}"
