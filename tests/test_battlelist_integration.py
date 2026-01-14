from __future__ import annotations

import numpy as np

from gamestate.builder import GameStateBuilder
from vision import battlelist


def test_battlelist_builder_populates_fields(monkeypatch) -> None:
    # Keep this test lightweight: avoid OCR by stubbing row parsing.
    monkeypatch.setenv("BATTLELIST_N_ROWS", "3")

    def _fake_parse_row(_row_img, row_index: int):
        if row_index == 0:
            return {"row_index": 0, "name_raw": "0rc", "name_norm": "orc", "name_display": "Orc", "conf": 0.9}
        if row_index == 1:
            return {"row_index": 1, "name_raw": "", "name_norm": "", "name_display": "", "conf": 0.0}
        return {"row_index": 2, "name_raw": "dr4g0n", "name_norm": "dragon", "name_display": "Dragon", "conf": 0.8}

    monkeypatch.setattr(battlelist, "parse_row", _fake_parse_row)

    builder = GameStateBuilder()
    frame = np.zeros((90, 60, 3), dtype=np.uint8)
    rois = {"battlelist_rows": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}}
    res = (frame.shape[1], frame.shape[0])

    gs = builder.update_from_frame(frame, rois, res)

    assert isinstance(gs.battlelist_entries, list)
    assert gs.battlelist_n_rows == 3
    assert gs.battlelist_n_valid == 2
    assert gs.battlelist_top_names[:2] == ["Orc", "Dragon"]

    # With single observation per row in history, stabilized per-row conf is 1.0.
    # Global conf = avg_conf_valid(=1.0) * pct_valid(=2/3).
    assert gs.battlelist_confidence is not None
    assert abs(float(gs.battlelist_confidence) - (2.0 / 3.0)) < 1e-6


def test_battlelist_builder_sets_defaults_when_roi_missing() -> None:
    builder = GameStateBuilder()
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    gs = builder.update_from_frame(frame, rois={}, resolution=(20, 20))

    assert gs.battlelist_entries == []
    assert gs.battlelist_n_rows == 0
    assert gs.battlelist_n_valid == 0
    assert gs.battlelist_top_names == []
    assert gs.battlelist_confidence == 0.0
