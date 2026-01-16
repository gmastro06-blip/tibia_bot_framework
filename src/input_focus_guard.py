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
import os
import time
from typing import Iterable

import win_window


# Prefer the common Tibia client title prefix.
# Keeping both makes it work for variants (e.g., OTClient) while prioritizing the exact pattern.
DEFAULT_ALLOWED_WINDOW_TITLES: list[str] = ["Tibia -", "Tibia"]
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


@dataclass
class CaptureTargetState:
    capture_backend: str = ""  # dxgi|obs_websocket|virtualcam|...
    capture_target: str = "auto"  # auto|obs_projector|client
    target_hwnd: int = 0
    target_title: str = ""
    target_class_name: str = ""
    target_is_minimized: bool = False
    target_is_maximized: bool = False
    target_bounds: tuple[int, int, int, int] | None = None
    target_found: bool = False
    reason: str = ""
    ts: float = 0.0


_capture_target_state = CaptureTargetState()


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


def _env_capture_target() -> str:
    try:
        v = (os.getenv("CAPTURE_TARGET", "auto") or "auto").strip().lower() or "auto"
    except Exception:
        v = "auto"
    if v in {"projector", "obs", "obs_projector"}:
        return "obs_projector"
    if v in {"client", "game", "tibia"}:
        return "client"
    return "auto"


def _env_capture_title_hints() -> list[str]:
    raw = (os.getenv("CAPTURE_TITLE_HINTS", "") or "").strip()
    if not raw:
        return []
    # Accept comma or pipe separated lists.
    parts: list[str] = []
    for sep in [",", "|"]:
        if sep in raw:
            parts = [p.strip() for p in raw.split(sep)]
            break
    if not parts:
        parts = [raw]
    return [p for p in parts if p]


def _bounds_area(bounds: tuple[int, int, int, int] | None) -> int:
    try:
        if bounds is None:
            return 0
        l, t, r, b = bounds
        return max(0, int(r) - int(l)) * max(0, int(b) - int(t))
    except Exception:
        return 0


def _is_obs_projector_title(title: str, *, extra_hints: list[str]) -> bool:
    t = (title or "").strip().lower()
    if not t:
        return False

    # Primary patterns (case-insensitive):
    # - "OBS" + "Projector" / "Proyector"
    # - "Fullscreen Projector" / "Windowed Projector"
    # - "Projector ("
    if ("obs" in t) and ("projector" in t or "proyector" in t):
        return True

    # OBS uses projector titles that sometimes omit the word "OBS".
    # Spanish examples:
    #   "Proyector en ventana (Fuente) - Tibia_Fuente"
    #   "Proyector a pantalla completa (Escena) - ..."
    if ("projector" in t or "proyector" in t) and (
        "fullscreen" in t
        or "windowed" in t
        or "en ventana" in t
        or "a pantalla completa" in t
        or "pantalla completa" in t
        or "projector (" in t
    ):
        return True

    if "fullscreen projector" in t or "windowed projector" in t:
        return True
    if "projector (" in t:
        return True

    # Optional extra hints
    for h in extra_hints:
        try:
            if str(h).strip() and str(h).strip().lower() in t:
                return True
        except Exception:
            continue

    return False


def _choose_largest_hwnd(cands: list[int]) -> int | None:
    best_hwnd: int | None = None
    best_area = 0
    for hwnd in cands:
        try:
            b = win_window.get_best_bounds(int(hwnd))
            a = _bounds_area(b)
            if a > best_area:
                best_area = a
                best_hwnd = int(hwnd)
        except Exception:
            continue
    return best_hwnd


def find_obs_projector_hwnd_anywhere(*, extra_hints: list[str] | None = None) -> int | None:
    """Find an OBS projector window HWND among visible windows."""

    win_window.set_dpi_awareness()
    hints = _norm_list(extra_hints)

    cands: list[int] = []
    for hwnd in win_window.enum_top_level_windows():
        try:
            title = win_window.get_window_title(hwnd)
            if _is_obs_projector_title(title, extra_hints=hints):
                cands.append(int(hwnd))
        except Exception:
            continue
    best = _choose_largest_hwnd(cands)
    return int(best) if best else None


def choose_capture_target_hwnd(*, capture_target: str, extra_title_hints: list[str] | None = None) -> tuple[int, str]:
    """Choose the capture target window.

    Returns: (hwnd, reason)
    """

    target = (capture_target or "auto").strip().lower() or "auto"
    hints = _norm_list(extra_title_hints)

    if target in {"auto", "obs_projector"}:
        hwnd = find_obs_projector_hwnd_anywhere(extra_hints=hints)
        if hwnd:
            return int(hwnd), "obs_projector"
        if target == "obs_projector":
            return 0, "obs_projector_not_found"

    # Fallback to client (Tibia)
    hwnd = find_client_hwnd_anywhere()
    if hwnd:
        return int(hwnd), "client"
    return 0, "client_not_found"


def update_capture_target_state() -> CaptureTargetState:
    """Recompute and store the current capture target window snapshot."""

    capture_backend = (os.getenv("CAPTURE_BACKEND", "dxgi") or "dxgi").strip().lower() or "dxgi"
    target = _env_capture_target()
    hints = _env_capture_title_hints()

    hwnd, reason = choose_capture_target_hwnd(capture_target=target, extra_title_hints=hints)

    st = CaptureTargetState()
    st.capture_backend = str(capture_backend)
    st.capture_target = str(target)
    st.target_hwnd = int(hwnd or 0)
    st.reason = str(reason or "")
    st.target_found = bool(st.target_hwnd)
    st.ts = time.time()

    if st.target_hwnd:
        try:
            st.target_title = win_window.get_window_title(st.target_hwnd)
        except Exception:
            st.target_title = ""
        try:
            st.target_class_name = win_window.get_window_class(st.target_hwnd)
        except Exception:
            st.target_class_name = ""
        try:
            st.target_is_minimized = bool(win_window.is_minimized(st.target_hwnd))
        except Exception:
            st.target_is_minimized = False
        try:
            st.target_is_maximized = bool(win_window.is_maximized(st.target_hwnd))
        except Exception:
            st.target_is_maximized = False
        try:
            st.target_bounds = win_window.get_best_bounds(st.target_hwnd)
        except Exception:
            st.target_bounds = None

    try:
        _capture_target_state.capture_backend = st.capture_backend
        _capture_target_state.capture_target = st.capture_target
        _capture_target_state.target_hwnd = st.target_hwnd
        _capture_target_state.target_title = st.target_title
        _capture_target_state.target_class_name = st.target_class_name
        _capture_target_state.target_is_minimized = st.target_is_minimized
        _capture_target_state.target_is_maximized = st.target_is_maximized
        _capture_target_state.target_bounds = st.target_bounds
        _capture_target_state.target_found = st.target_found
        _capture_target_state.reason = st.reason
        _capture_target_state.ts = st.ts
    except Exception:
        pass

    return st


def get_capture_target_snapshot() -> CaptureTargetState:
    try:
        return CaptureTargetState(**_capture_target_state.__dict__)
    except Exception:
        return _capture_target_state


def format_capture_target_overlay_line() -> str:
    st = get_capture_target_snapshot()
    tgt = str(st.capture_target or "auto")
    hwnd = int(st.target_hwnd or 0)
    found = int(bool(hwnd))
    title = (st.target_title or "").strip() or "<not found>"
    b = st.target_bounds
    b_s = ""
    try:
        if b is not None:
            b_s = f" bounds=({int(b[0])},{int(b[1])},{int(b[2])},{int(b[3])})"
    except Exception:
        b_s = ""
    reason = (st.reason or "").strip()
    r_s = f" reason={reason}" if reason else ""
    return f"capture_target={tgt} found={found} hwnd={hwnd} title={title}{b_s}{r_s}"


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

    # Strong preference for the canonical title prefix used by Tibia clients.
    # Example: "Tibia - Nombre del usuario"
    if t_low.startswith("tibia -"):
        score += 12
    elif "tibia -" in t_low:
        score += 8

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
        if (os.getenv("ALLOW_BACKGROUND_INPUT", "") or "").strip().lower() in {"1", "true", "yes"}:
            return True, "bg_allowed"
    except Exception:
        pass

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
