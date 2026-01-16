from __future__ import annotations

"""Win32 window helpers (ctypes-only).

Goals:
- Be dependency-free (no pywin32 requirement for these helpers).
- Be safe: best-effort behavior, never throw across module boundaries.
- Provide enough primitives for client detection, focus guard, and bounds.

All HWNDs are returned as Python ints.
Rects are tuples (left, top, right, bottom) in screen coordinates.
"""

from dataclasses import dataclass
import ctypes
from ctypes import wintypes


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    def is_valid(self) -> bool:
        try:
            return int(self.right) > int(self.left) and int(self.bottom) > int(self.top)
        except Exception:
            return False

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (int(self.left), int(self.top), int(self.right), int(self.bottom))


user32 = ctypes.windll.user32


def set_dpi_awareness() -> None:
    """Best-effort enable PER_MONITOR_AWARE_V2 to avoid coordinate scaling issues."""

    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = (HANDLE)-4
        ctx = wintypes.HANDLE(-4)  # type: ignore[arg-type]
        fn = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if fn is None:
            return
        fn.argtypes = [wintypes.HANDLE]
        fn.restype = wintypes.BOOL
        fn(ctx)
    except Exception:
        return


def _is_window(hwnd: int) -> bool:
    try:
        if not hwnd:
            return False
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        return bool(user32.IsWindow(wintypes.HWND(int(hwnd))))
    except Exception:
        return False


def enum_top_level_windows() -> list[int]:
    """Return a list of visible top-level window HWNDs."""

    hwnds: list[int] = []

    try:
        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL

        def _cb(hwnd: wintypes.HWND, lparam: wintypes.LPARAM) -> bool:
            try:
                if not bool(user32.IsWindowVisible(hwnd)):
                    return True
                hwnds.append(int(hwnd))
            except Exception:
                pass
            return True

        cb = EnumWindowsProc(_cb)
        user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.EnumWindows(cb, 0)
    except Exception:
        return hwnds

    return hwnds


def get_foreground_hwnd() -> int:
    try:
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = user32.GetForegroundWindow()
        return int(hwnd) if hwnd else 0
    except Exception:
        return 0


def get_window_title(hwnd: int) -> str:
    try:
        if not _is_window(hwnd):
            return ""
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        n = int(user32.GetWindowTextLengthW(wintypes.HWND(int(hwnd))))
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowTextW(wintypes.HWND(int(hwnd)), buf, n + 1)
        return str(buf.value or "")
    except Exception:
        return ""


def get_window_class(hwnd: int) -> str:
    try:
        if not _is_window(hwnd):
            return ""
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        n = int(user32.GetClassNameW(wintypes.HWND(int(hwnd)), buf, 256))
        if n <= 0:
            return ""
        return str(buf.value or "")
    except Exception:
        return ""


def get_window_pid(hwnd: int) -> int:
    try:
        if not _is_window(hwnd):
            return 0
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def is_minimized(hwnd: int) -> bool:
    try:
        if not _is_window(hwnd):
            return False
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        return bool(user32.IsIconic(wintypes.HWND(int(hwnd))))
    except Exception:
        return False


def is_maximized(hwnd: int) -> bool:
    try:
        if not _is_window(hwnd):
            return False
        user32.IsZoomed.argtypes = [wintypes.HWND]
        user32.IsZoomed.restype = wintypes.BOOL
        return bool(user32.IsZoomed(wintypes.HWND(int(hwnd))))
    except Exception:
        return False


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    try:
        if not _is_window(hwnd):
            return None
        rect = wintypes.RECT()
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        ok = bool(user32.GetWindowRect(wintypes.HWND(int(hwnd)), ctypes.byref(rect)))
        if not ok:
            return None
        r = Rect(rect.left, rect.top, rect.right, rect.bottom)
        return r.as_tuple() if r.is_valid() else None
    except Exception:
        return None


def get_client_rect_screen(hwnd: int) -> tuple[int, int, int, int] | None:
    """Return the client rect mapped to screen coordinates."""

    try:
        if not _is_window(hwnd):
            return None

        rc = wintypes.RECT()
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetClientRect.restype = wintypes.BOOL
        ok = bool(user32.GetClientRect(wintypes.HWND(int(hwnd)), ctypes.byref(rc)))
        if not ok:
            return None

        # Convert top-left and bottom-right from client to screen.
        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
        user32.ClientToScreen.restype = wintypes.BOOL

        p0 = POINT(int(rc.left), int(rc.top))
        p1 = POINT(int(rc.right), int(rc.bottom))
        if not bool(user32.ClientToScreen(wintypes.HWND(int(hwnd)), ctypes.byref(p0))):
            return None
        if not bool(user32.ClientToScreen(wintypes.HWND(int(hwnd)), ctypes.byref(p1))):
            return None

        r = Rect(int(p0.x), int(p0.y), int(p1.x), int(p1.y))
        return r.as_tuple() if r.is_valid() else None
    except Exception:
        return None


def get_extended_frame_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    """Return DWM extended frame bounds (includes shadow) when available."""

    try:
        if not _is_window(hwnd):
            return None

        try:
            dwmapi = ctypes.windll.dwmapi
        except Exception:
            return None

        DWMWA_EXTENDED_FRAME_BOUNDS = 9
        rect = wintypes.RECT()
        dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        # ctypes.wintypes.HRESULT exists at runtime, but typeshed may not expose it.
        # HRESULT is a signed 32-bit integer.
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

        hr = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(int(hwnd)),
            wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            wintypes.DWORD(ctypes.sizeof(rect)),
        )
        if int(hr) != 0:
            return None

        r = Rect(rect.left, rect.top, rect.right, rect.bottom)
        return r.as_tuple() if r.is_valid() else None
    except Exception:
        return None


def get_best_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    """Return best-effort bounds in screen coordinates.

    Preference order:
    1) DWM extended frame bounds (if valid)
    2) Client rect mapped to screen
    3) Window rect
    """

    try:
        r = get_extended_frame_bounds(hwnd)
        if r is not None:
            return r
    except Exception:
        pass

    try:
        r = get_client_rect_screen(hwnd)
        if r is not None:
            return r
    except Exception:
        pass

    try:
        r = get_window_rect(hwnd)
        if r is not None:
            return r
    except Exception:
        pass

    return None
