from __future__ import annotations

import numpy as np


def test_builder_exposes_pct_only_when_max_unknown(monkeypatch) -> None:
    monkeypatch.setenv("COORDS_PROVIDER", "disabled")
    monkeypatch.setenv("VISION_GUARD_ENABLED", "0")
    monkeypatch.setenv("OCR_MIN_INTERVAL_S", "0")

    import gamestate.builder as bmod

    # Force bar ratios without relying on image content.
    def _bar(_frame, _roi, kind):
        k = str(kind or "").lower()
        if "hp" in k:
            return (0.42, "ok")
        if "mp" in k:
            return (0.12, "ok")
        return (None, "no_bar")

    monkeypatch.setattr(bmod, "estimate_bar_fill_ratio_with_reason", _bar)

    builder = bmod.GameStateBuilder()

    # Simulate OCR failing to produce any absolute numbers.
    builder.ocr_processor.last_hp_ocr_source = "top_ocr"
    builder.ocr_processor.last_hp_ocr_reason = "parse_fail"
    builder.ocr_processor.last_mp_ocr_source = "top_ocr"
    builder.ocr_processor.last_mp_ocr_reason = "parse_fail"
    builder.ocr_processor.extract_hp_mp_full = lambda *_a, **_kw: (None, None, None, None)
    builder.ocr_processor.extract_capacity = lambda *_a, **_kw: None
    builder.ocr_processor.extract_coords = lambda *_a, **_kw: (None, None, None)

    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    rois = {
        "hp_top_ocr": {"x": 0.0, "y": 0.0, "w": 0.2, "h": 0.1},
        "mp_top_ocr": {"x": 0.2, "y": 0.0, "w": 0.2, "h": 0.1},
        "hp_low_bar": {"x": 0.0, "y": 0.9, "w": 0.5, "h": 0.08},
        "mp_low_bar": {"x": 0.5, "y": 0.9, "w": 0.5, "h": 0.08},
        "skills_panel": {"x": 0.8, "y": 0.0, "w": 0.2, "h": 0.4},
        "battlelist_rows": {"x": 0.8, "y": 0.4, "w": 0.2, "h": 0.25},
        "game_viewport": {"x": 0.0, "y": 0.05, "w": 0.8, "h": 0.8},
        "states_icons": {"x": 0.8, "y": 0.0, "w": 0.2, "h": 0.1},
    }
    resolution = (100, 100)

    gs = builder.update_from_frame(frame, rois, resolution)

    assert (gs.hp_current, gs.hp_max) == (None, None)
    assert (gs.mp_current, gs.mp_max) == (None, None)

    assert gs.hp_pct == 42.0
    assert gs.mp_pct == 12.0

    assert gs.hp_method == "bar_low_pct"
    assert gs.hp_reason == "pct_only"
    assert gs.mp_method == "bar_low_pct"
    assert gs.mp_reason == "pct_only"
