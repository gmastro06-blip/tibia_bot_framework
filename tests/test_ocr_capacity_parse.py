from __future__ import annotations

from src.vision.ocr import OCRProcessor


def test_parse_capacity_from_text_prefers_cap_labeled_number() -> None:
    # Contains several numbers; only the CAP-labeled value should be used.
    s = "Lvl 105 Skill 80 Cap: 2800 Soul 100"
    assert OCRProcessor._parse_capacity_from_text(s) == 2800


def test_parse_capacity_from_text_returns_none_without_cap_label() -> None:
    # Previously we used max(number) which would return 2800 here.
    s = "Lvl 105 Skill 80 2800 Soul 100"
    assert OCRProcessor._parse_capacity_from_text(s) is None
