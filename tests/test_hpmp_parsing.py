from __future__ import annotations

from vision.ocr import OCRProcessor


def _parser() -> OCRProcessor:
    ocr = OCRProcessor.__new__(OCRProcessor)
    ocr._debug = False  # type: ignore[attr-defined]
    return ocr


def test_parse_current_and_max_slash() -> None:
    ocr = _parser()
    cur, mx, reason = ocr._parse_current_and_max_with_reason("100/200", "HP")  # type: ignore[attr-defined]
    assert (cur, mx, reason) == (100, 200, "ok")


def test_parse_current_and_max_pipe() -> None:
    ocr = _parser()
    cur, mx, reason = ocr._parse_current_and_max_with_reason(" 100 | 200 ", "HP")  # type: ignore[attr-defined]
    assert (cur, mx, reason) == (100, 200, "ok")


def test_parse_single_number() -> None:
    ocr = _parser()
    cur, mx, reason = ocr._parse_current_and_max_with_reason(" 95 ", "MP")  # type: ignore[attr-defined]
    assert cur == 95
    assert mx is None
    assert reason in {"single_number", "ok"}


def test_parse_invalid_range_cur_gt_max() -> None:
    ocr = _parser()
    cur, mx, reason = ocr._parse_current_and_max_with_reason("250/200", "HP")  # type: ignore[attr-defined]
    assert cur is None
    assert mx is None
    assert reason == "invalid_range"


def test_parse_large_values_supported() -> None:
    ocr = _parser()
    cur, mx, reason = ocr._parse_current_and_max_with_reason("12000/50000", "HP")  # type: ignore[attr-defined]
    assert (cur, mx, reason) == (12000, 50000, "ok")


def test_parse_rejects_tiny_max_values() -> None:
    ocr = _parser()
    cur, mx, reason = ocr._parse_current_and_max_with_reason("0/1", "HP")  # type: ignore[attr-defined]
    assert cur is None
    assert mx is None
    assert reason == "invalid_range"
