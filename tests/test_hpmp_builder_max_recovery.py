import numpy as np


def test_builder_reuses_last_max_when_ocr_misses_max(monkeypatch):
    # Avoid coords OCR side-effects.
    monkeypatch.setenv("COORDS_PROVIDER", "disabled")
    monkeypatch.setenv("VISION_GUARD_ENABLED", "0")
    monkeypatch.setenv("OCR_MIN_INTERVAL_S", "0")

    import gamestate.builder as bmod

    builder = bmod.GameStateBuilder()

    # Patch OCR paths to deterministic outputs.
    builder.ocr_processor.last_hp_ocr_source = "top_ocr"
    builder.ocr_processor.last_hp_ocr_reason = "ok"
    builder.ocr_processor.last_mp_ocr_source = "top_ocr"
    builder.ocr_processor.last_mp_ocr_reason = "ok"

    builder.ocr_processor.extract_hp_mp_full = lambda *_a, **_kw: (215, 215, 100, 100)
    builder.ocr_processor.extract_capacity = lambda *_a, **_kw: None
    builder.ocr_processor.extract_coords = lambda *_a, **_kw: (None, None, None)

    # Don't depend on bar analysis for this test.
    monkeypatch.setattr(bmod, "estimate_bar_fill_ratio_with_reason", lambda *_a, **_kw: (None, "no_bar"))

    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    rois = {
        "hp_top_ocr": {"x": 0.0, "y": 0.0, "w": 0.2, "h": 0.1},
        "mp_top_ocr": {"x": 0.2, "y": 0.0, "w": 0.2, "h": 0.1},
        "hp_low_bar": {"x": 0.0, "y": 0.9, "w": 0.5, "h": 0.08},
        "mp_low_bar": {"x": 0.5, "y": 0.9, "w": 0.5, "h": 0.08},
        "cap_ocr": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.05},
        "skills_panel": {"x": 0.8, "y": 0.0, "w": 0.2, "h": 0.4},
        "battlelist_rows": {"x": 0.8, "y": 0.4, "w": 0.2, "h": 0.25},
        "game_viewport": {"x": 0.0, "y": 0.05, "w": 0.8, "h": 0.8},
        "states_icons": {"x": 0.8, "y": 0.0, "w": 0.2, "h": 0.1},
    }
    resolution = (100, 100)

    gs1 = builder.update_from_frame(frame, rois, resolution)
    assert (gs1.hp_current, gs1.hp_max) == (215, 215)
    assert (gs1.mp_current, gs1.mp_max) == (100, 100)

    # Now simulate OCR returning only current values.
    def _extract_only_cur(*_a, **_kw):
        builder.ocr_processor.last_hp_ocr_source = "top_ocr"
        builder.ocr_processor.last_hp_ocr_reason = "single_number"
        builder.ocr_processor.last_mp_ocr_source = "top_ocr"
        builder.ocr_processor.last_mp_ocr_reason = "single_number"
        return (200, None, 80, None)

    builder.ocr_processor.extract_hp_mp_full = _extract_only_cur

    gs2 = builder.update_from_frame(frame, rois, resolution)

    # Must reuse last maxima, preserving classic cur/max behavior.
    assert (gs2.hp_current, gs2.hp_max) == (200, 215)
    assert (gs2.mp_current, gs2.mp_max) == (80, 100)


def test_builder_infers_max_equals_current_when_full_bar(monkeypatch):
    monkeypatch.setenv("COORDS_PROVIDER", "disabled")
    monkeypatch.setenv("VISION_GUARD_ENABLED", "0")
    monkeypatch.setenv("OCR_MIN_INTERVAL_S", "0")

    import gamestate.builder as bmod

    # Force bar ratio to full.
    def _full_bar(_frame, _roi, kind):
        return (1.0, "ok")

    monkeypatch.setattr(bmod, "estimate_bar_fill_ratio_with_reason", _full_bar)

    builder = bmod.GameStateBuilder()

    # OCR dropped the '/max' part.
    builder.ocr_processor.last_hp_ocr_source = "top_ocr"
    builder.ocr_processor.last_hp_ocr_reason = "single_number"
    builder.ocr_processor.last_mp_ocr_source = "top_ocr"
    builder.ocr_processor.last_mp_ocr_reason = "single_number"
    builder.ocr_processor.extract_hp_mp_full = lambda *_a, **_kw: (215, None, 100, None)
    builder.ocr_processor.extract_capacity = lambda *_a, **_kw: None
    builder.ocr_processor.extract_coords = lambda *_a, **_kw: (None, None, None)

    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    rois = {
        "hp_top_ocr": {"x": 0.0, "y": 0.0, "w": 0.2, "h": 0.1},
        "mp_top_ocr": {"x": 0.2, "y": 0.0, "w": 0.2, "h": 0.1},
        "hp_low_bar": {"x": 0.0, "y": 0.9, "w": 0.5, "h": 0.08},
        "mp_low_bar": {"x": 0.5, "y": 0.9, "w": 0.5, "h": 0.08},
        "cap_ocr": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.05},
        "skills_panel": {"x": 0.8, "y": 0.0, "w": 0.2, "h": 0.4},
        "battlelist_rows": {"x": 0.8, "y": 0.4, "w": 0.2, "h": 0.25},
        "game_viewport": {"x": 0.0, "y": 0.05, "w": 0.8, "h": 0.8},
        "states_icons": {"x": 0.8, "y": 0.0, "w": 0.2, "h": 0.1},
    }
    resolution = (100, 100)

    gs = builder.update_from_frame(frame, rois, resolution)

    assert (gs.hp_current, gs.hp_max) == (215, 215)
    assert (gs.mp_current, gs.mp_max) == (100, 100)
