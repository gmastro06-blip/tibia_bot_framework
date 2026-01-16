from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass
class DecisionTraceWriter:
    """Append-only DecisionTrace JSONL writer with simple rotation.

    Keeps at most 2 files:
      - <path> (active)
      - <stem>.prev<suffix> (previous)

    Rotation rule: if active size exceeds `max_bytes`, rotate before appending.
    """

    path: str = "logs/decision_trace.jsonl"
    max_bytes: int = 10 * 1024 * 1024

    def _paths(self) -> tuple[Path, Path]:
        p = Path(self.path)
        prev = p.with_name(f"{p.stem}.prev{p.suffix}")
        return p, prev

    def _ensure_parent(self, p: Path) -> None:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def rotate_if_needed(self) -> None:
        p, prev = self._paths()
        self._ensure_parent(p)

        try:
            if not p.exists():
                return
            if p.stat().st_size <= int(self.max_bytes):
                return
        except Exception:
            return

        try:
            if prev.exists():
                prev.unlink(missing_ok=True)
        except Exception:
            pass

        try:
            os.replace(str(p), str(prev))
        except Exception:
            # Best-effort: if rename fails (file locked), skip rotation.
            pass

    def write(self, event: Mapping[str, Any]) -> None:
        p, _prev = self._paths()
        self._ensure_parent(p)

        self.rotate_if_needed()

        try:
            line = json.dumps(dict(event), ensure_ascii=False, separators=(",", ":"))
        except Exception:
            try:
                line = json.dumps({"ts": time.time(), "exception": "decision_trace_serialize_failed"})
            except Exception:
                return

        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")
        except Exception:
            # Never break the bot due to trace logging.
            pass


def ensure_blocked_reason(event: dict[str, Any]) -> dict[str, Any]:
    """Defense-in-depth: if no input was sent, guarantee blocked_reason exists."""

    try:
        raw_action = event.get("action")
        action: dict[str, Any]
        if isinstance(raw_action, dict):
            action = raw_action
        else:
            action = {}

        raw_inj = event.get("injection")
        injection: dict[str, Any]
        if isinstance(raw_inj, dict):
            injection = raw_inj
        else:
            injection = {}

        sent = bool(action.get("sent_to_driver", False))
        br = str(injection.get("blocked_reason", "") or "").strip()

        if (not sent) and (not br):
            injection["blocked_reason"] = "unknown"
            event["injection"] = injection
    except Exception:
        pass
    return event
