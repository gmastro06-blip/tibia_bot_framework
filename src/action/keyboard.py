import time
import ctypes
from ctypes import wintypes

# WinAPI constants
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1


def _ulong_ptr_type():
    # ULONG_PTR is pointer-sized (32-bit on x86, 64-bit on x64).
    return ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


ULONG_PTR = _ulong_ptr_type()

# Scan codes (US layout). Adjust if you use another layout.
# Keep common WASD/letters, digits, modifiers, and F-keys used by the bot.
SCANCODES = {
    # Modifiers
    "CTRL": 0x1D,
    "CONTROL": 0x1D,
    "SHIFT": 0x2A,
    "ALT": 0x38,
    # Letters
    "A": 0x1E,
    "B": 0x30,
    "C": 0x2E,
    "D": 0x20,
    "E": 0x12,
    "F": 0x21,
    "G": 0x22,
    "H": 0x23,
    "I": 0x17,
    "J": 0x24,
    "K": 0x25,
    "L": 0x26,
    "M": 0x32,
    "N": 0x31,
    "O": 0x18,
    "P": 0x19,
    "Q": 0x10,
    "R": 0x13,
    "S": 0x1F,
    "T": 0x14,
    "U": 0x16,
    "V": 0x2F,
    "W": 0x11,
    "X": 0x2D,
    "Y": 0x15,
    "Z": 0x2C,
    # Digits (row)
    "1": 0x02,
    "2": 0x03,
    "3": 0x04,
    "4": 0x05,
    "5": 0x06,
    "6": 0x07,
    "7": 0x08,
    "8": 0x09,
    "9": 0x0A,
    "0": 0x0B,
    # Function keys
    "F1": 0x3B,
    "F2": 0x3C,
    "F3": 0x3D,
    "F4": 0x3E,
    "F5": 0x3F,
    "F6": 0x40,
    "F7": 0x41,
    "F8": 0x42,
    "F9": 0x43,
    "F10": 0x44,
    "F11": 0x57,
    "F12": 0x58,

    # Arrow keys (extended)
    "UP": 0x48,
    "DOWN": 0x50,
    "LEFT": 0x4B,
    "RIGHT": 0x4D,

    # Navigation/edit keys (extended)
    "PGUP": 0x49,
    "PAGEUP": 0x49,
    "PGDN": 0x51,
    "PAGEDOWN": 0x51,
    "HOME": 0x47,
    "END": 0x4F,
    "INS": 0x52,
    "INSERT": 0x52,
    "DEL": 0x53,
    "DELETE": 0x53,
}


EXTENDED_KEYS = {
    "UP",
    "DOWN",
    "LEFT",
    "RIGHT",
    "PGUP",
    "PAGEUP",
    "PGDN",
    "PAGEDOWN",
    "HOME",
    "END",
    "INS",
    "INSERT",
    "DEL",
    "DELETE",
}

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_I(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("ii", INPUT_I),
    ]


class KeyboardSender:
    """Send keyboard input via WinAPI SendInput."""

    def __init__(self) -> None:
        self._send_input = ctypes.windll.user32.SendInput
        try:
            self._send_input.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
            self._send_input.restype = wintypes.UINT
        except Exception:
            pass

    def _send_key(self, scan: int, flags: int, *, extended: bool = False) -> bool:
        if extended:
            flags = int(flags) | KEYEVENTF_EXTENDEDKEY
        ii = INPUT_I()
        ii.ki = KEYBDINPUT(0, int(scan), int(flags), 0, 0)
        inp = INPUT(int(INPUT_KEYBOARD), ii)
        try:
            sent = int(self._send_input(1, ctypes.byref(inp), ctypes.sizeof(inp)) or 0)
            return sent == 1
        except Exception:
            return False

    def press(self, key: str) -> bool:
        scan = SCANCODES[key]
        return self._send_key(scan, KEYEVENTF_SCANCODE, extended=(key in EXTENDED_KEYS))

    def release(self, key: str) -> bool:
        scan = SCANCODES[key]
        return self._send_key(scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, extended=(key in EXTENDED_KEYS))

    def tap(self, key: str, hold_s: float = 0.05) -> bool:
        ok_down = self.press(key)
        time.sleep(max(0.0, hold_s))
        ok_up = self.release(key)
        return bool(ok_down and ok_up)

    def tap_combo(self, keys: list[str], hold_s: float = 0.05) -> bool:
        # Press modifiers first, then base, release in reverse.
        if not keys:
            return False
        scans: list[int] = []
        try:
            for k in keys:
                scan = SCANCODES[k]
                scans.append(scan)
        except Exception:
            return False

        try:
            # All but last are treated as modifiers.
            for sc in scans[:-1]:
                if not self._send_key(sc, KEYEVENTF_SCANCODE):
                    return False

            if not self._send_key(scans[-1], KEYEVENTF_SCANCODE):
                return False
            time.sleep(max(0.0, hold_s))
            if not self._send_key(scans[-1], KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP):
                return False

            for sc in reversed(scans[:-1]):
                if not self._send_key(sc, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP):
                    return False
            return True
        except Exception:
            return False
