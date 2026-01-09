from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping, Optional


def _safe_mkdir(p: Path) -> None:
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _json_default(o: object) -> object:
    try:
        if hasattr(o, "__dict__"):
            return dict(getattr(o, "__dict__"))
    except Exception:
        pass
    return str(o)


class JsonlLogger:
    """Logger simple que appendea JSON Lines (1 evento por línea)."""

    def __init__(self) -> None:
        self._last_ts = 0.0

    def should_log(self, *, enabled: bool, interval_ms: int) -> bool:
        if not enabled:
            return False
        now = time.time()
        interval_s = max(0.05, float(interval_ms) / 1000.0)
        ok = (now - self._last_ts) >= interval_s
        if ok:
            self._last_ts = now
        return ok

    def append(self, *, out_file: str, event: Mapping[str, Any], ts: Optional[float] = None) -> None:
        t = time.time() if ts is None else float(ts)
        path = Path(out_file)
        _safe_mkdir(path.parent)
        payload = dict(event)
        payload.setdefault("ts", t)
        line = json.dumps(payload, ensure_ascii=False, default=_json_default)
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        self._last_ts = t
