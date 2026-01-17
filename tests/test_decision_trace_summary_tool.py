from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _run(argv: list[str], *, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    # Import lazily so tests don't share global argparse state.
    from tools.decision_trace_summary import main

    old = list(sys.argv)
    try:
        sys.argv = ["decision_trace_summary.py", *argv]
        code = int(main())
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    return code, out


def test_decision_trace_summary_handles_malformed_lines(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = tmp_path / "decision_trace.jsonl"
    p.write_text(
        "\n".join(
            [
                "{\"ts\": 1.0, \"action\": {\"sent_to_driver\": false, \"kind\": \"move\"}, \"injection\": {\"state\": \"DISABLED\", \"blocked_reason\": \"input_mode=log\"}, \"capture\": {\"backend\": \"dxgi\"}}",
                "not json at all",
                "[1,2,3]",  # valid json but not a dict
                "{\"ts\": 2.0, \"action\": {\"sent_to_driver\": true, \"kind\": \"heal\"}, \"injection\": {\"state\": \"ARMED\", \"blocked_reason\": \"ok\"}, \"capture\": {\"backend\": \"dxgi\"}}",
                "",  # blank
            ]
        ),
        encoding="utf-8",
    )

    code, out = _run([str(p), "--stats"], capsys=capsys)
    assert code == 0
    assert "events=" in out
    assert "lines_total=" in out
    assert "bad_json=" in out
    assert "non_dict=" in out


def test_decision_trace_summary_since_s_filters(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = tmp_path / "decision_trace.jsonl"
    p.write_text(
        "\n".join(
            [
                "{\"ts\": 100.0, \"action\": {\"sent_to_driver\": false}, \"injection\": {\"state\": \"DISABLED\", \"blocked_reason\": \"disabled\"}, \"capture\": {}}",
                "{\"ts\": 200.0, \"action\": {\"sent_to_driver\": false}, \"injection\": {\"state\": \"DISABLED\", \"blocked_reason\": \"disabled\"}, \"capture\": {}}",
            ]
        ),
        encoding="utf-8",
    )

    # since_s=50 means keep only events with ts >= (last_ts - 50) = 150.
    code, out = _run([str(p), "--since-s", "50"], capsys=capsys)
    assert code == 0
    assert "events=1" in out
