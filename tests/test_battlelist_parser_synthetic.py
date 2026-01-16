import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from vision.battlelist_targeting import BattlelistParser


def _make_roi(*, rows: int = 4, width: int = 200, row_h: int = 20) -> np.ndarray:
    height = rows * row_h
    roi = np.zeros((height, width, 3), dtype=np.uint8)
    roi[:] = (10, 10, 10)

    # Row 0: green bar (alive)
    y0 = 0
    y1 = row_h
    cv2.rectangle(roi, (0, y0), (int(width * 0.3), y1 - 1), (0, 255, 0), -1)

    # Row 1: red bar (alive)
    y0 = row_h
    y1 = row_h * 2
    cv2.rectangle(roi, (0, y0), (int(width * 0.3), y1 - 1), (0, 0, 255), -1)

    # Row 2: highlighted (selected)
    y0 = row_h * 2
    y1 = row_h * 3
    cv2.rectangle(roi, (0, y0), (width - 1, y1 - 1), (0, 255, 255), -1)

    # Row 3: empty
    return roi


def test_battlelist_parser_synthetic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BATTLELIST_N_ROWS", "4")

    roi = _make_roi()
    parser = BattlelistParser(ocr_enabled=False)
    rows = parser.parse(roi)

    assert len(rows) == 4
    assert rows[0].alive_prob > 0.6
    assert rows[1].alive_prob > 0.6
    assert rows[2].selected_prob > 0.6
    assert rows[3].alive_prob < 0.2

    assert rows[0].row_bbox == (0, 0, roi.shape[1], 20)
    assert rows[3].row_bbox == (0, 60, roi.shape[1], 80)
