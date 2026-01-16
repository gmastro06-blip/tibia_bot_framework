import numpy as np

from gamestate.builder import GameStateBuilder


class _DummyMinimapTracker:
    last_mode_used = "marker"
    last_response = 0.0
    last_delta_tiles_f = (0.0, 0.0)
    last_acc_tiles_f = (0.0, 0.0)
    last_marker_dpx = (0.0, 0.0)

    def update(self, _crop):
        # Always returns a step with very low response.
        return (0, 0, 0.0)

    def reset(self):
        return None


def test_minimap_provider_confidence_fallback_steps(monkeypatch):
    # Configure minimap provider with a seed and a fast fallback policy.
    monkeypatch.setenv("COORDS_PROVIDER", "minimap")
    monkeypatch.setenv("COORDS_SEED_X", "100")
    monkeypatch.setenv("COORDS_SEED_Y", "200")

    # Avoid calling OCR-heavy paths in update_from_frame.
    monkeypatch.setenv("OCR_MIN_INTERVAL_S", "9999")

    # Fallback: after 3 consecutive low-confidence ticks, disable coords (steps-mode).
    monkeypatch.setenv("MINIMAP_FALLBACK_MODE", "steps")
    monkeypatch.setenv("MINIMAP_FALLBACK_CONF_THRESHOLD", "0.50")
    monkeypatch.setenv("MINIMAP_FALLBACK_N_TICKS", "3")

    # Make sure our dummy response is considered low.
    monkeypatch.setenv("MINIMAP_ACCEPT_MIN_RESPONSE", "0.80")

    b = GameStateBuilder()
    b._minimap_tracker = _DummyMinimapTracker()  # type: ignore[attr-defined]

    # Keep ROI conversion deterministic.
    monkeypatch.setattr(b.ocr_processor, "_roi_to_px", lambda frame, rois, res, roi: (0, 0, 10, 10))

    frame = np.zeros((60, 60, 3), dtype=np.uint8)
    rois = {"minimap_content": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}}
    resolution = (60, 60)

    b.update_from_frame(frame, rois, resolution)
    b.update_from_frame(frame, rois, resolution)
    gs3 = b.update_from_frame(frame, rois, resolution)

    # Fallback should have activated by the 3rd low-confidence tick.
    assert gs3.pos_x is None and gs3.pos_y is None

    # Reason should be visible for logging/UI.
    assert "fallback_steps" in str(getattr(gs3, "coords_provider_status", ""))

    st = getattr(gs3, "coords_provider_state", None)
    assert isinstance(st, dict)
    assert st.get("enabled") is True
    assert st.get("seed_ok") is True
    assert st.get("reason") == "fallback_steps"
