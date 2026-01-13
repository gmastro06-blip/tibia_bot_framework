import time
import ctypes

# WinAPI constants
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

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
}

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint),
        ("ki", KEYBDINPUT),
    ]


class KeyboardSender:
    """Send keyboard input via WinAPI SendInput."""

    def __init__(self) -> None:
        self._send_input = ctypes.windll.user32.SendInput

    def _send_key(self, scan: int, flags: int) -> None:
        ki = KEYBDINPUT(0, scan, flags, 0, None)
        inp = INPUT(INPUT_KEYBOARD, ki)
        self._send_input(1, ctypes.byref(inp), ctypes.sizeof(inp))

    def press(self, key: str) -> None:
        scan = SCANCODES[key]
        self._send_key(scan, KEYEVENTF_SCANCODE)

    def release(self, key: str) -> None:
        scan = SCANCODES[key]
        self._send_key(scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)

    def tap(self, key: str, hold_s: float = 0.05) -> None:
        self.press(key)
        time.sleep(max(0.0, hold_s))
        self.release(key)

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
                self._send_key(sc, KEYEVENTF_SCANCODE)

            self._send_key(scans[-1], KEYEVENTF_SCANCODE)
            time.sleep(max(0.0, hold_s))
            self._send_key(scans[-1], KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)

            for sc in reversed(scans[:-1]):
                self._send_key(sc, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)
            return True
        except Exception:
            return False
