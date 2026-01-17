from __future__ import annotations

import os

from vision.hud_parsing import parse_current_and_max_with_reason


def test_parse_normalizes_common_ocr_ambiguities(monkeypatch) -> None:
    # Keep bounds stable for the test.
    monkeypatch.setenv("HPMP_MAX_OCR", "100000")
    monkeypatch.delenv("TIBIA_STAT_MAX", raising=False)

    cur, mx, reason = parse_current_and_max_with_reason("I00|200", label="HP")
    assert (cur, mx, reason) == (100, 200, "ok")

    cur2, mx2, reason2 = parse_current_and_max_with_reason("!5l/250", label="MP")
    assert (cur2, mx2, reason2) == (151, 250, "ok")


def test_parse_accepts_single_number_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("HPMP_MAX_OCR", "100000")

    cur, mx, reason = parse_current_and_max_with_reason(" 95 ", label="HP", allow_single_number=True)
    assert cur == 95
    assert mx is None
    assert reason == "single_number"

    cur2, mx2, reason2 = parse_current_and_max_with_reason(" 95 ", label="HP", allow_single_number=False)
    assert (cur2, mx2, reason2) == (None, None, "parse_fail")
