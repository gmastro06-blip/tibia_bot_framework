from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

from input_bridge import is_allowed_key


@dataclass
class BridgeStatsSnapshot:
    connected: bool
    rate_sent: int
    rate_accepted: int
    rate_rejected: int
    last_error: str


class InputBridgeClient:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 7777,
        token: str = "",
        timeout_s: float = 0.5,
    ) -> None:
        self.host = str(host)
        self.port = int(port)
        self.token = str(token or "")
        self.timeout_s = max(0.1, float(timeout_s))
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._last_error: str = ""

        # per-second counters
        self._bucket_ts = int(time.time())
        self._sent = 0
        self._accepted = 0
        self._rejected = 0

    def _tick_bucket(self) -> None:
        now = int(time.time())
        if now != self._bucket_ts:
            self._bucket_ts = now
            self._sent = 0
            self._accepted = 0
            self._rejected = 0

    def _inc(self, kind: str) -> None:
        self._tick_bucket()
        if kind == "sent":
            self._sent += 1
        elif kind == "accepted":
            self._accepted += 1
        elif kind == "rejected":
            self._rejected += 1

    def snapshot(self) -> BridgeStatsSnapshot:
        self._tick_bucket()
        return BridgeStatsSnapshot(
            connected=self.is_connected(),
            rate_sent=int(self._sent),
            rate_accepted=int(self._accepted),
            rate_rejected=int(self._rejected),
            last_error=str(self._last_error or ""),
        )

    def is_connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> bool:
        with self._lock:
            if self._sock is not None:
                return True
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(self.timeout_s)
                sock.connect((self.host, self.port))
                self._sock = sock
                return True
            except Exception as e:
                self._last_error = str(e)
                try:
                    if self._sock:
                        self._sock.close()
                except Exception:
                    pass
                self._sock = None
                return False

    def close(self) -> None:
        with self._lock:
            try:
                if self._sock:
                    self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _send(self, payload: dict[str, Any]) -> bool:
        self._inc("sent")
        if not self.connect():
            self._inc("rejected")
            return False

        line = (json.dumps(payload) + "\n").encode("utf-8")
        with self._lock:
            try:
                assert self._sock is not None
                self._sock.sendall(line)
                data = b""
                while b"\n" not in data:
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        raise OSError("disconnected")
                    data += chunk
                raw = data.split(b"\n", 1)[0]
                resp = json.loads(raw.decode("utf-8", errors="ignore"))
                ok = bool(resp.get("ok"))
                if ok:
                    self._inc("accepted")
                    return True
                self._inc("rejected")
                self._last_error = str(resp.get("error") or "rejected")
                return False
            except Exception as e:
                self._inc("rejected")
                self._last_error = str(e)
                try:
                    if self._sock:
                        self._sock.close()
                except Exception:
                    pass
                self._sock = None
                return False

    # ---- Public API ----

    def send_key_press(self, key: str) -> bool:
        k = str(key or "").strip().upper()
        if not is_allowed_key(k):
            self._last_error = "key_not_allowed"
            self._inc("rejected")
            return False
        payload = {"token": self.token, "type": "key", "action": "press", "key": k}
        return self._send(payload)

    def send_key_down(self, key: str) -> bool:
        k = str(key or "").strip().upper()
        if not is_allowed_key(k):
            self._last_error = "key_not_allowed"
            self._inc("rejected")
            return False
        payload = {"token": self.token, "type": "key", "action": "down", "key": k}
        return self._send(payload)

    def send_key_up(self, key: str) -> bool:
        k = str(key or "").strip().upper()
        if not is_allowed_key(k):
            self._last_error = "key_not_allowed"
            self._inc("rejected")
            return False
        payload = {"token": self.token, "type": "key", "action": "up", "key": k}
        return self._send(payload)

    def send_mouse_move(self, x: int, y: int) -> bool:
        payload = {"token": self.token, "type": "mouse", "action": "move", "x": int(x), "y": int(y)}
        return self._send(payload)

    def send_mouse_click(self, x: int, y: int) -> bool:
        payload = {
            "token": self.token,
            "type": "mouse",
            "action": "click",
            "button": "left",
            "x": int(x),
            "y": int(y),
        }
        return self._send(payload)

    def send_control(self, action: str) -> bool:
        payload = {"token": self.token, "type": "control", "action": str(action)}
        return self._send(payload)
