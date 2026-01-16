from __future__ import annotations

import json

from src.telemetry.decision_trace import DecisionTraceWriter, ensure_blocked_reason


def test_decision_trace_written_has_blocked_reason(tmp_path) -> None:
    out = tmp_path / "decision_trace.jsonl"
    w = DecisionTraceWriter(path=str(out), max_bytes=10 * 1024 * 1024)

    e1 = ensure_blocked_reason(
        {
            "ts": 1.0,
            "capture": {},
            "healing": {},
            "targeting": {},
            "cavebot": {},
            "action": {"note": None, "kind": None, "value": None, "sent_to_driver": False},
            "injection": {"state": "DISABLED", "blocked_reason": "input_mode=log", "input_mode": "log", "driver": "Mock", "advance_pulse": False},
        }
    )
    e2 = ensure_blocked_reason(
        {
            "ts": 2.0,
            "capture": {},
            "healing": {},
            "targeting": {},
            "cavebot": {},
            "action": {"note": None, "kind": None, "value": None, "sent_to_driver": False},
            "injection": {"state": "WAITING_CONFIRM", "blocked_reason": "no_committed_pulse", "input_mode": "keyboard", "driver": "Windows", "advance_pulse": False},
        }
    )

    w.write(e1)
    w.write(e2)

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    for ln in lines:
        obj = json.loads(ln)
        assert "injection" in obj
        assert "blocked_reason" in obj["injection"]
        assert str(obj["injection"]["blocked_reason"] or "").strip() != ""
