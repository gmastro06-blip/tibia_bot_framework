from __future__ import annotations

import json

from src.telemetry.jsonl_logger import JsonlLogger


def test_jsonl_logger_throttle(monkeypatch):
    jl = JsonlLogger()

    t = 1000.0

    def fake_time():
        return t

    monkeypatch.setattr("time.time", fake_time)

    assert jl.should_log(enabled=True, interval_ms=500) is True
    assert jl.should_log(enabled=True, interval_ms=500) is False

    t = 1000.6
    assert jl.should_log(enabled=True, interval_ms=500) is True


def test_jsonl_logger_appends_line(tmp_path):
    jl = JsonlLogger()

    out_file = tmp_path / "telemetry.jsonl"
    jl.append(out_file=str(out_file), event={"a": 1, "b": "x"})

    text = out_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(text) == 1
    obj = json.loads(text[0])
    assert obj["a"] == 1
    assert obj["b"] == "x"
    assert "ts" in obj
