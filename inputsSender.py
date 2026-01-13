import time
import ctypes

# WinAPI constants
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002

# Scan codes (US layout). Ajusta si usas otro layout.
SCANCODES = {
    "w": 0x11,
    "a": 0x1E,
    "s": 0x1F,
    "d": 0x20,
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

# WinAPI structures
class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint),
                ("ii", ctypes.c_ulonglong)]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_uint),
                ("time", ctypes.c_uint),
                ("dwExtraInfo", ctypes.c_ulonglong)]

def _send_key(scan, flags):
    ki = KEYBDINPUT(0, scan, flags, 0, 0)
    inp = INPUT(1, ctypes.cast(ctypes.pointer(ki), ctypes.c_ulonglong).value)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

class KeyboardSender:
    def press(self, key: str) -> None:
        scan = SCANCODES[key]
        _send_key(scan, KEYEVENTF_SCANCODE)

    def release(self, key: str) -> None:
        scan = SCANCODES[key]
        _send_key(scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)

    def tap(self, key: str, hold_s: float = 0.05) -> None:
        self.press(key)
        time.sleep(max(0.0, hold_s))
        self.release(key)

# Ejemplo de uso:
# ks = KeyboardSender()
# ks.tap("w")
# ks.tap("F1")