from __future__ import annotations

import numpy as np

from vision.bar_analysis import estimate_bar_fill_ratio


def test_bar_analysis_returns_none_when_no_color() -> None:
    frame = np.zeros((10, 100, 3), dtype=np.uint8)
    ratio = estimate_bar_fill_ratio(frame, (0, 0, 100, 10), "hp")
    assert ratio is None


def test_bar_analysis_detects_red_fill_ratio() -> None:
    frame = np.zeros((10, 100, 3), dtype=np.uint8)
    # Fill 60% with red (BGR: 0,0,255)
    frame[:, :60, 2] = 255
    ratio = estimate_bar_fill_ratio(frame, (0, 0, 100, 10), "hp")
    assert ratio is not None
    assert 0.5 <= ratio <= 0.7
