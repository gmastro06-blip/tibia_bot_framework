import numpy as np


def test_vision_guard_force_skips_expensive(monkeypatch):
    """When VISION_GUARD_FORCE=1, builder must not call expensive OCR/Roboflow paths."""

    monkeypatch.setenv("VISION_GUARD_FORCE", "1")

    from gamestate.builder import GameStateBuilder

    b = GameStateBuilder()

    # Patch expensive methods to explode if called.
    def _boom(*_a, **_kw):
        raise AssertionError("expensive path called")

    b.ocr_processor.extract_hp_mp_full = _boom
    b.ocr_processor.extract_capacity = _boom
    b.ocr_processor.extract_coords = _boom

    # Small dummy frame + minimal ROI map (normalized coords).
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    rois = {
        "hp_top_ocr": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.05},
        "mp_top_ocr": {"x": 0.1, "y": 0.0, "w": 0.1, "h": 0.05},
        "hp_low_bar": {"x": 0.0, "y": 0.9, "w": 0.5, "h": 0.05},
        "mp_low_bar": {"x": 0.5, "y": 0.9, "w": 0.5, "h": 0.05},
        "coords_ocr": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.05},
        "cap_ocr": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.05},
        "skills_panel": {"x": 0.8, "y": 0.0, "w": 0.2, "h": 0.4},
        "battlelist_rows": {"x": 0.8, "y": 0.4, "w": 0.2, "h": 0.25},
        "game_viewport": {"x": 0.0, "y": 0.05, "w": 0.8, "h": 0.8},
        "states_icons": {"x": 0.8, "y": 0.0, "w": 0.2, "h": 0.1},
    }
    resolution = (100, 100)

    gs = b.update_from_frame(frame, rois, resolution)

    assert gs is not None
    assert isinstance(getattr(gs, "hud_debug", {}), dict)
    assert bool(gs.hud_debug.get("vision_guard", {}).get("active")) is True
