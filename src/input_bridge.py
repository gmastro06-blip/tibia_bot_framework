from __future__ import annotations

import json
import os
import socket
import threading
import time
import sys
from pathlib import Path
from collections import deque
from dataclasses import dataclass
from queue import Queue, Empty
from typing import Any

import ctypes

_repo_root = Path(__file__).resolve().parent.parent
_src_dir = Path(__file__).resolve().parent
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from action.keyboard import SCANCODES, EXTENDED_KEYS

# ---- Protocol / Validation ----

ALLOWED_MOUSE_BUTTONS = {"left"}


def is_allowed_key(key: str) -> bool:
    try:
        k = str(key or "").strip().upper()
        return bool(k) and k in SCANCODES
    except Exception:
        return False


def validate_command(cmd: dict[str, Any], *, token: str) -> tuple[bool, str]:
    if not isinstance(cmd, dict):
        return False, "invalid_payload"

    if token:
        if str(cmd.get("token", "") or "") != str(token):
            return False, "bad_token"

    typ = str(cmd.get("type", "") or "").strip().lower()
    if typ not in {"key", "mouse", "control"}:
        return False, "invalid_type"

    if typ == "control":
        action = str(cmd.get("action", "") or "").strip().lower()
        if action not in {"disable", "enable", "ping"}:
            return False, "invalid_control"
        return True, "ok"

    if typ == "key":
        action = str(cmd.get("action", "") or "").strip().lower()
        key = str(cmd.get("key", "") or "").strip().upper()
        if action not in {"down", "up", "press"}:
            return False, "invalid_key_action"
        if not is_allowed_key(key):
            return False, "key_not_allowed"
        return True, "ok"

    if typ == "mouse":
        action = str(cmd.get("action", "") or "").strip().lower()
        if action not in {"move", "click"}:
            return False, "invalid_mouse_action"
        if action == "click":
            btn = str(cmd.get("button", "left") or "left").strip().lower()
            if btn not in ALLOWED_MOUSE_BUTTONS:
                return False, "invalid_mouse_button"
        try:
            x_raw = cmd.get("x")
            y_raw = cmd.get("y")
            if x_raw is None or y_raw is None:
                return False, "invalid_mouse_xy"
            x = int(x_raw)
            y = int(y_raw)
        except Exception:
            return False, "invalid_mouse_xy"
        if x < 0 or y < 0:
            return False, "invalid_mouse_xy"
        return True, "ok"

    return False, "invalid_payload"


# ---- OS Injection (SendInput) ----

KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
INPUT_MOUSE = 0


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_uint),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint), ("union", INPUT_UNION)]


class InputInjector:
    def __init__(self) -> None:
        self._send_input = ctypes.windll.user32.SendInput
        self._get_metrics = ctypes.windll.user32.GetSystemMetrics

    def _send_key(self, scan: int, flags: int, *, extended: bool = False) -> None:
        if extended:
            flags = int(flags) | KEYEVENTF_EXTENDEDKEY
        ki = KEYBDINPUT(0, scan, flags, 0, None)
        inp = INPUT(INPUT_KEYBOARD, INPUT_UNION(ki=ki))
        self._send_input(1, ctypes.byref(inp), ctypes.sizeof(inp))

    def key_down(self, key: str) -> None:
        scan = SCANCODES[key]
        self._send_key(scan, KEYEVENTF_SCANCODE, extended=(key in EXTENDED_KEYS))

    def key_up(self, key: str) -> None:
        scan = SCANCODES[key]
        self._send_key(scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, extended=(key in EXTENDED_KEYS))

    def key_press(self, key: str, hold_s: float = 0.03) -> None:
        self.key_down(key)
        time.sleep(max(0.0, float(hold_s)))
        self.key_up(key)

    def _normalize_xy(self, x: int, y: int) -> tuple[int, int]:
        try:
            w = int(self._get_metrics(0))
            h = int(self._get_metrics(1))
        except Exception:
            w, h = 1920, 1080
        w = max(1, w)
        h = max(1, h)
        x = max(0, min(w - 1, int(x)))
        y = max(0, min(h - 1, int(y)))
        nx = int(x * 65535 / (w - 1))
        ny = int(y * 65535 / (h - 1))
        return nx, ny

    def mouse_move(self, x: int, y: int) -> None:
        nx, ny = self._normalize_xy(x, y)
        mi = MOUSEINPUT(nx, ny, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, None)
        inp = INPUT(INPUT_MOUSE, INPUT_UNION(mi=mi))
        self._send_input(1, ctypes.byref(inp), ctypes.sizeof(inp))

    def mouse_click(self, x: int, y: int) -> None:
        self.mouse_move(x, y)
        mi_down = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)
        mi_up = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)
        inp1 = INPUT(INPUT_MOUSE, INPUT_UNION(mi=mi_down))
        inp2 = INPUT(INPUT_MOUSE, INPUT_UNION(mi=mi_up))
        self._send_input(1, ctypes.byref(inp1), ctypes.sizeof(inp1))
        self._send_input(1, ctypes.byref(inp2), ctypes.sizeof(inp2))


# ---- Server ----

@dataclass
class BridgeStats:
    accepted: int = 0
    rejected: int = 0
    last_error: str = ""


class _RateLimiter:
    def __init__(self, max_per_sec: int) -> None:
        self.max_per_sec = max(1, int(max_per_sec))
        self._events: deque[float] = deque()

    def allow(self) -> bool:
        now = time.monotonic()
        window = 1.0
        while self._events and (now - self._events[0]) > window:
            self._events.popleft()
        if len(self._events) >= self.max_per_sec:
            return False
        self._events.append(now)
        return True


class InputBridgeServer:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 7777,
        token: str = "",
        max_rate_per_sec: int = 30,
        move_debounce_ms: int = 30,
    ) -> None:
        self.host = str(host)
        self.port = int(port)
        self.token = str(token or "")
        self.enabled = True
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._queue: Queue[dict[str, Any]] = Queue(maxsize=500)
        self._worker: threading.Thread | None = None
        self._limiter = _RateLimiter(max_rate_per_sec)
        self._move_debounce_s = max(0.0, float(move_debounce_ms) / 1000.0)
        self._last_move: tuple[int, int] | None = None
        self._last_move_ts: float = 0.0
        self.stats = BridgeStats()
        self._injector = InputInjector()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass

    def _serve(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            sock.listen(5)
            self._sock = sock
        except Exception as e:
            try:
                self.stats.last_error = f"bind_failed: {e}"
                print(f"INPUT BRIDGE bind failed: {e}")
            except Exception:
                pass
            try:
                self._stop.set()
            except Exception:
                pass
            return

        while not self._stop.is_set():
            try:
                conn, _addr = sock.accept()
                conn.settimeout(1.0)
                t = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
                t.start()
            except Exception:
                time.sleep(0.1)

    def _handle_client(self, conn: socket.socket) -> None:
        with conn:
            buf = b""
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        self._handle_line(conn, line)
                except socket.timeout:
                    continue
                except Exception:
                    return

    def _handle_line(self, conn: socket.socket, line: bytes) -> None:
        try:
            data = json.loads(line.decode("utf-8", errors="ignore").strip())
        except Exception:
            self._send_resp(conn, ok=False, error="bad_json")
            return

        ok, reason = validate_command(data, token=self.token)
        if not ok:
            self.stats.rejected += 1
            self.stats.last_error = reason
            self._send_resp(conn, ok=False, error=reason)
            return

        if not self.enabled and str(data.get("type", "")) != "control":
            self.stats.rejected += 1
            self.stats.last_error = "disabled"
            self._send_resp(conn, ok=False, error="disabled")
            return

        # Rate limiting
        if not self._limiter.allow():
            self.stats.rejected += 1
            self.stats.last_error = "rate_limited"
            self._send_resp(conn, ok=False, error="rate_limited")
            return

        # Debounce mouse move
        if str(data.get("type", "")) == "mouse" and str(data.get("action", "")) == "move":
            try:
                x = int(data.get("x"))
                y = int(data.get("y"))
            except Exception:
                x, y = -1, -1
            now = time.monotonic()
            if self._last_move == (x, y) and (now - self._last_move_ts) < self._move_debounce_s:
                self.stats.rejected += 1
                self.stats.last_error = "debounced"
                self._send_resp(conn, ok=False, error="debounced")
                return
            self._last_move = (x, y)
            self._last_move_ts = now

        # Control actions
        if str(data.get("type", "")) == "control":
            action = str(data.get("action", ""))
            if action == "disable":
                self.enabled = False
            elif action == "enable":
                self.enabled = True
            self.stats.accepted += 1
            self._send_resp(conn, ok=True, status=action)
            return

        try:
            self._queue.put_nowait(data)
        except Exception:
            self.stats.rejected += 1
            self.stats.last_error = "queue_full"
            self._send_resp(conn, ok=False, error="queue_full")
            return

        self.stats.accepted += 1
        self._send_resp(conn, ok=True, status="accepted")

    def _send_resp(self, conn: socket.socket, *, ok: bool, status: str | None = None, error: str | None = None) -> None:
        try:
            payload: dict[str, object] = {"ok": bool(ok)}
            if status:
                payload["status"] = str(status)
            if error:
                payload["error"] = str(error)
            conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        except Exception:
            pass

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                cmd = self._queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                self._execute(cmd)
            except Exception:
                continue

    def _execute(self, cmd: dict[str, Any]) -> None:
        typ = str(cmd.get("type", "") or "")
        if typ == "key":
            key = str(cmd.get("key", "") or "").strip().upper()
            action = str(cmd.get("action", "") or "").strip().lower()
            if action == "down":
                self._injector.key_down(key)
            elif action == "up":
                self._injector.key_up(key)
            else:
                self._injector.key_press(key)
            return

        if typ == "mouse":
            x_raw = cmd.get("x")
            y_raw = cmd.get("y")
            if x_raw is None or y_raw is None:
                return
            x = int(x_raw)
            y = int(y_raw)
            action = str(cmd.get("action", "") or "").strip().lower()
            if action == "move":
                self._injector.mouse_move(x, y)
            else:
                self._injector.mouse_click(x, y)
            return


def run_bridge_from_env() -> None:
    host = (os.getenv("INPUT_BRIDGE_HOST", "127.0.0.1") or "127.0.0.1").strip()
    try:
        port = int(float(os.getenv("INPUT_BRIDGE_PORT", "7777") or 7777))
    except Exception:
        port = 7777
    token = os.getenv("INPUT_BRIDGE_TOKEN", "") or ""
    try:
        max_rate = int(float(os.getenv("INPUT_BRIDGE_MAX_RATE", "30") or 30))
    except Exception:
        max_rate = 30
    try:
        debounce_ms = int(float(os.getenv("INPUT_BRIDGE_MOVE_DEBOUNCE_MS", "30") or 30))
    except Exception:
        debounce_ms = 30

    srv = InputBridgeServer(
        host=host,
        port=port,
        token=token,
        max_rate_per_sec=max_rate,
        move_debounce_ms=debounce_ms,
    )
    try:
        print(f"INPUT BRIDGE listening on {host}:{port} (rate={max_rate}/s, debounce={debounce_ms}ms)")
    except Exception:
        pass
    try:
        srv.start()
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        srv.stop()
    except Exception as e:
        try:
            print(f"INPUT BRIDGE fatal: {e}")
        except Exception:
            pass
        srv.stop()


if __name__ == "__main__":
    try:
        run_bridge_from_env()
    except Exception as e:
        try:
            print(f"INPUT BRIDGE crash: {e}")
        except Exception:
            pass
