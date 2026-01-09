from __future__ import annotations

import json

from src.telemetry.jsonl_writer import JsonlWriter


def test_jsonl_writer_appends_line_and_injects_ts(tmp_path):
    w = JsonlWriter()
    out_file = tmp_path / "out.jsonl"

    w.append(out_file=str(out_file), event={"a": 1, "b": "x"}, ts=123.0)

    lines = out_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["a"] == 1
    assert obj["b"] == "x"
    assert obj["ts"] == 123.0
