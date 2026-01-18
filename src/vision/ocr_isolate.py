from __future__ import annotations

import os
import time
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any, Optional


@dataclass
class OcrIsolateConfig:
    languages: tuple[str, ...] = ("en",)
    gpu: bool = False
    init_log: bool = False


def _safe_bool_env(name: str, default: bool) -> bool:
    try:
        raw = (os.getenv(name, "") if default is None else os.getenv(name, ""))
        if raw is None:
            return bool(default)
        s = str(raw).strip().lower()
        if s == "":
            return bool(default)
        return s in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _safe_float_env(name: str, default: float) -> float:
    try:
        raw = (os.getenv(name, "") or "").strip()
        if not raw:
            return float(default)
        return float(raw)
    except Exception:
        return float(default)


def ocr_worker_main(conn: Connection, cfg: OcrIsolateConfig) -> None:
    """OCR worker process.

    Loads EasyOCR in-process and serves read requests over a Pipe.

    Protocol:
      - parent -> worker: {"type":"readtext", "id": int, "detail": 0|1, "allowlist": str|None, "shape": [...], "dtype": "uint8", "bytes": b"..."}
      - worker -> parent: {"type":"ready"}
      - worker -> parent: {"type":"result", "id": int, "strings": [..]}  (detail=0)
      - worker -> parent: {"type":"result", "id": int, "items": [...] }    (detail=1, best-effort)
      - worker -> parent: {"type":"error", "id": int, "error": "..."}
    """

    try:
        import numpy as np

        import easyocr  # type: ignore

        if cfg.init_log:
            try:
                print(f"[ocr_worker] init gpu={int(bool(cfg.gpu))} langs={cfg.languages}")
            except Exception:
                pass

        reader = easyocr.Reader(list(cfg.languages), gpu=bool(cfg.gpu))

        # Signal readiness.
        try:
            conn.send({"type": "ready"})
        except Exception:
            return

        while True:
            try:
                msg = conn.recv()
            except EOFError:
                return
            except Exception:
                continue

            if not isinstance(msg, dict):
                continue
            mtype = str(msg.get("type", "") or "")
            if mtype == "shutdown":
                return
            if mtype != "readtext":
                continue

            req_id = msg.get("id", None)
            try:
                detail = int(msg.get("detail", 0) or 0)
            except Exception:
                detail = 0
            allowlist = msg.get("allowlist", None)

            try:
                shape = msg.get("shape")
                dtype = str(msg.get("dtype", "uint8") or "uint8")
                b = msg.get("bytes")
                if not isinstance(shape, (list, tuple)) or not b:
                    raise ValueError("missing shape/bytes")
                if dtype != "uint8":
                    raise ValueError(f"unsupported dtype {dtype}")

                arr = np.frombuffer(b, dtype=np.uint8)
                img = arr.reshape(tuple(int(x) for x in shape))
            except Exception as e:
                try:
                    conn.send({"type": "error", "id": req_id, "error": f"decode: {e}"})
                except Exception:
                    pass
                continue

            try:
                if detail == 0:
                    res = reader.readtext(img, detail=0, allowlist=allowlist)
                    strings = []
                    try:
                        for r in res or []:
                            s = str(r).strip()
                            if s:
                                strings.append(s)
                    except Exception:
                        strings = []
                    try:
                        conn.send({"type": "result", "id": req_id, "strings": strings})
                    except Exception:
                        pass
                else:
                    # detail=1 is supported but we only forward raw items.
                    # Parent may ignore this in safety mode.
                    items = reader.readtext(img, detail=1, allowlist=allowlist)
                    try:
                        conn.send({"type": "result", "id": req_id, "items": items})
                    except Exception:
                        pass
            except Exception as e:
                try:
                    conn.send({"type": "error", "id": req_id, "error": f"readtext: {e}"})
                except Exception:
                    pass
    except Exception:
        # If we can't even import/init, still try to signal ready so parent won't
        # spin restarting aggressively.
        try:
            conn.send({"type": "ready"})
        except Exception:
            pass
        # Then exit; parent will see the process die.
        return


class OcrIsolateClient:
    """Client-side controller for the OCR worker.

    Guarantees: `readtext_*` never blocks longer than `timeout_s`.
    """

    def __init__(self) -> None:
        import multiprocessing as mp

        self._ctx = mp.get_context("spawn")
        # NOTE: `multiprocessing.Pipe()` returns a `PipeConnection` which mypy
        # doesn't always consider assignable to `Connection`. We keep this as
        # `Any` because we only use `.poll()/.recv()/.send()/.close()`.
        self._conn: Any | None = None
        self._proc: Any = None
        self._ready: bool = False
        self._last_start_ts: float = 0.0
        self._next_id: int = 1
        self._backoff_until_ts: float = 0.0

        self._cfg = OcrIsolateConfig(
            gpu=_safe_bool_env("OCR_GPU", True),
            init_log=_safe_bool_env("OCR_INIT_LOG", False),
        )

        # If OCR_GPU wasn't explicitly set, default to CPU for stability.
        try:
            if (os.getenv("OCR_GPU", "") or "").strip() == "":
                self._cfg.gpu = False
        except Exception:
            self._cfg.gpu = False

    def _ensure_started(self) -> None:
        now = time.time()
        if now < float(self._backoff_until_ts or 0.0):
            return

        if self._proc is not None:
            try:
                if getattr(self._proc, "is_alive", lambda: False)():
                    return
            except Exception:
                pass

        try:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
        except Exception:
            pass

        parent_conn, child_conn = self._ctx.Pipe(duplex=True)
        self._conn = parent_conn
        self._ready = False
        self._last_start_ts = float(time.time())

        from multiprocessing import Process

        self._proc = Process(target=ocr_worker_main, args=(child_conn, self._cfg), daemon=True)
        try:
            self._proc.start()
        except Exception:
            self._proc = None
            self._conn = None
            self._ready = False
            # backoff a bit
            self._backoff_until_ts = float(time.time()) + 1.0
            return

        # Best-effort: don't block here; readiness will be polled during calls.

    def _drain_ready(self) -> None:
        if self._conn is None:
            return
        try:
            while self._conn.poll(0.0):
                msg = self._conn.recv()
                if isinstance(msg, dict) and str(msg.get("type", "")) == "ready":
                    self._ready = True
        except Exception:
            pass

    def _kill_worker(self) -> None:
        try:
            if self._proc is not None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                try:
                    self._proc.join(timeout=0.5)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
        except Exception:
            pass
        self._proc = None
        self._conn = None
        self._ready = False
        self._backoff_until_ts = float(time.time()) + 0.5

    def is_ready(self) -> bool:
        self._ensure_started()
        self._drain_ready()
        return bool(self._ready)

    def readtext_strings(self, image, *, allowlist: Optional[str], timeout_s: float) -> list[str]:
        import numpy as np

        self._ensure_started()
        if self._conn is None:
            return []
        self._drain_ready()
        if not self._ready:
            return []

        try:
            img = image
            if not isinstance(img, np.ndarray):
                return []
            if img.size == 0:
                return []
            if img.dtype != np.uint8:
                img = img.astype(np.uint8, copy=False)
            if not img.flags["C_CONTIGUOUS"]:
                img = np.ascontiguousarray(img)

            payload = {
                "type": "readtext",
                "id": int(self._next_id),
                "detail": 0,
                "allowlist": allowlist,
                "shape": list(img.shape),
                "dtype": "uint8",
                "bytes": img.tobytes(order="C"),
            }
            req_id = int(self._next_id)
            self._next_id += 1
        except Exception:
            return []

        try:
            self._conn.send(payload)
        except Exception:
            # likely dead worker
            self._kill_worker()
            return []

        try:
            if not self._conn.poll(max(0.0, float(timeout_s))):
                # hung readtext -> restart worker
                self._kill_worker()
                return []
            msg = self._conn.recv()
        except Exception:
            self._kill_worker()
            return []

        if not isinstance(msg, dict):
            return []
        if int(msg.get("id", -1) or -1) != int(req_id):
            return []
        mtype = str(msg.get("type", "") or "")
        if mtype == "result":
            strings = msg.get("strings", [])
            if isinstance(strings, list):
                out = []
                for s in strings:
                    try:
                        st = str(s).strip()
                    except Exception:
                        continue
                    if st:
                        out.append(st)
                return out
            return []
        if mtype == "error":
            return []
        return []


_singleton: OcrIsolateClient | None = None


def get_ocr_isolate_client() -> OcrIsolateClient:
    global _singleton
    if _singleton is None:
        _singleton = OcrIsolateClient()
    return _singleton
