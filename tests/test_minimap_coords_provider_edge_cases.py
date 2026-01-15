from __future__ import annotations

import numpy as np

from gamestate.builder import GameStateBuilder


class _NoStepTracker:
    last_mode_used = "scroll"
    last_response = 0.0
    last_delta_tiles_f = (0.0, 0.0)
    last_acc_tiles_f = (0.0, 0.0)
    last_marker_dpx = (0.0, 0.0)

    def update(self, _crop):
        return None


class _ExplodeTracker:
    def update(self, _crop):
        raise AssertionError("tracker.update should not be called")


def test_minimap_coords_missing_roi_returns_none(monkeypatch):
    monkeypatch.setenv("COORDS_PROVIDER", "minimap")
    monkeypatch.setenv("COORDS_SEED_X", "100")
    monkeypatch.setenv("COORDS_SEED_Y", "200")

    b = GameStateBuilder()
    b._minimap_tracker = _ExplodeTracker()  # type: ignore[assignment]

    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    res = (20, 20)

    c = b._coords_from_minimap(frame, rois={}, resolution=res)
    assert c is None
    assert b._minimap_status == "minimap_no_roi"


def test_minimap_coords_missing_seed_returns_none(monkeypatch):
    monkeypatch.setenv("COORDS_PROVIDER", "minimap")
    monkeypatch.delenv("COORDS_SEED_X", raising=False)
    monkeypatch.delenv("COORDS_SEED_Y", raising=False)
    monkeypatch.delenv("COORDS_SEED_FILE", raising=False)
    monkeypatch.delenv("COORDS_FILE", raising=False)

    b = GameStateBuilder()
    b._minimap_tracker = _ExplodeTracker()  # type: ignore[assignment]

    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    rois = {"minimap_content": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}}
    res = (20, 20)

    # Make ROI conversion deterministic (and avoid relying on OCRProcessor internals).
    monkeypatch.setattr(b.ocr_processor, "_roi_to_px", lambda frame, rois, res, roi: (0, 0, 10, 10))

    c = b._coords_from_minimap(frame, rois=rois, resolution=res)
    assert c is None
    assert b._minimap_status == "minimap_no_seed"


def test_minimap_no_step_can_trigger_fallback_steps(monkeypatch):
    monkeypatch.setenv("COORDS_PROVIDER", "minimap")
    monkeypatch.setenv("COORDS_SEED_X", "100")
    monkeypatch.setenv("COORDS_SEED_Y", "200")

    # Fallback quickly to steps-mode after 2 low-confidence ticks.
    monkeypatch.setenv("MINIMAP_FALLBACK_MODE", "steps")
    monkeypatch.setenv("MINIMAP_FALLBACK_CONF_THRESHOLD", "0.50")
    monkeypatch.setenv("MINIMAP_FALLBACK_N_TICKS", "2")

    b = GameStateBuilder()
    b._minimap_tracker = _NoStepTracker()  # type: ignore[assignment]

    # Seed manually and keep ROI conversion deterministic.
    b._minimap_seed = (100, 200, None)
    b._minimap_coords = (100, 200, None)
    monkeypatch.setattr(b.ocr_processor, "_roi_to_px", lambda frame, rois, res, roi: (0, 0, 10, 10))

    frame = np.zeros((60, 60, 3), dtype=np.uint8)
    rois = {"minimap_content": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}}
    res = (60, 60)

    c1 = b._coords_from_minimap(frame, rois=rois, resolution=res)
    assert c1 == (100, 200, None)
    assert b._minimap_status == "minimap_no_step"

    c2 = b._coords_from_minimap(frame, rois=rois, resolution=res)
    assert c2 is None
    assert b._minimap_status == "fallback_steps"
    assert bool(getattr(b, "_minimap_force_disable_coords", False)) is True
