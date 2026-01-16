from __future__ import annotations

from tools.tail_decision_trace import tail_lines


def test_tail_decision_trace_n_20(tmp_path) -> None:
    p = tmp_path / "decision_trace.jsonl"
    p.write_text("".join([f"{{\"i\":{i}}}\n" for i in range(30)]), encoding="utf-8")

    out = tail_lines(str(p), 20)
    assert len(out) == 20
    assert out[0] == '{"i":10}'
    assert out[-1] == '{"i":29}'
