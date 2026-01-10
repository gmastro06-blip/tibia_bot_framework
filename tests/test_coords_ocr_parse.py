from __future__ import annotations

from vision.ocr import OCRProcessor


def test_parse_coords_from_text_variants() -> None:
    p = OCRProcessor._parse_coords_from_text

    assert p("X: 32561 Y: 32496 Z: 7") == (32561, 32496, 7)
    assert p("x=32561 y=32496 z=7") == (32561, 32496, 7)
    assert p("32561 32496 7") == (32561, 32496, 7)
    assert p("32561,32496,7") == (32561, 32496, 7)

    # z optional
    assert p("X: 111 Y: 222") == (111, 222, None)
    assert p("111 222") == (111, 222, None)

    # garbage
    assert p("") is None
    assert p("hello") is None
