from __future__ import annotations

import os
import numpy as np

from gamestate.builder import GameStateBuilder


class _DummyTracker:
    def __init__(self):
        self.calls = 0
        self.last_mode_used = "scroll"
        self.last_response = 0.0
        self.last_delta_tiles_f = (0.0, 0.0)
        self.last_acc_tiles_f = (0.0, 0.0)
        self.last_marker_dpx = (0.0, 0.0)

    def update(self, crop):
        self.calls += 1
        if self.calls == 1:
            return (1, 0, 0.05)  # below min response
        if self.calls == 2:
            return (1, 0, 0.25)  # first accepted response, streak=1 (no move yet)
        return (1, 0, 0.30)  # streak=2 => apply move


def test_minimap_coords_requires_response_streak(monkeypatch):
    monkeypatch.setenv("COORDS_PROVIDER", "minimap")
    monkeypatch.setenv("COORDS_SEED_X", "100")
    monkeypatch.setenv("COORDS_SEED_Y", "200")
    monkeypatch.setenv("MINIMAP_ACCEPT_MIN_RESPONSE", "0.2")
    monkeypatch.setenv("MINIMAP_ACCEPT_MIN_STREAK", "2")
    monkeypatch.setenv("MINIMAP_ACCEPT_MAX_STEP_TILES", "4")

    builder = GameStateBuilder()
    builder._minimap_tracker = _DummyTracker()  # type: ignore[assignment]
    # Seed manually to avoid reliance on external files/env parsing.
    builder._minimap_seed = (100, 200, None)
    builder._minimap_coords = (100, 200, None)

    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    rois = {"minimap_content": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}}
    res = (20, 20)

    c1 = builder._coords_from_minimap(frame, rois, res)
    assert c1 == (100, 200, None)
    assert builder._minimap_status == "minimap_low_resp"
    assert 0.0 <= builder._minimap_confidence < 1.0

    c2 = builder._coords_from_minimap(frame, rois, res)
    assert c2 == (100, 200, None)
    assert builder._minimap_status == "minimap_warming"
    assert 0.0 < builder._minimap_confidence < 1.0

    c3 = builder._coords_from_minimap(frame, rois, res)
    assert c3 == (101, 200, None)
    assert builder._minimap_status == "ok"
    assert builder._minimap_confidence == 1.0
