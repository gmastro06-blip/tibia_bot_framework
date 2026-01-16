from __future__ import annotations

from src.layout_tracker import LayoutTracker


class _FakeTracker:
    def __init__(self, *, dx: float, dy: float) -> None:
        self.dx = dx
        self.dy = dy

    def maybe_update(self, *, frame, rois, resolution, roi_to_px) -> None:
        # Pretend we detected a stable anchor offset.
        rois["_roi_offset_px"] = [float(self.dx), float(self.dy)]
        rois["_roi_offset_score"] = 0.9


def test_layout_tracker_apply_layout_applies_offset() -> None:
    rois = {
        "_source_resolution": [100, 100],
        "_roi_offset_px": [10, 0],
        "hp_top_ocr": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1},
    }

    px = LayoutTracker.apply_layout(frame_shape=(100, 100), rois=rois, resolution=(100, 100), names=["hp_top_ocr"])
    assert px["hp_top_ocr"]["ok"] is True
    # Without offset, x would be 0. With +10 src px offset, x becomes 10.
    assert px["hp_top_ocr"]["roi"][0] == 10


def test_layout_tracker_update_sets_changed_flag() -> None:
    rois = {
        "_source_resolution": [100, 100],
        "hp_top_ocr": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1},
    }

    lt = LayoutTracker(anchor_tracker=_FakeTracker(dx=5, dy=6), viewport_tracker=None, enabled=True)

    # roi_to_px is unused by FakeTracker, but LayoutTracker requires it.
    def roi_to_px(_frame, _rois, _res, _roi_def):
        return (0, 0, 1, 1)

    st = lt.update(frame=None, rois=rois, resolution=(100, 100), roi_to_px=roi_to_px)
    assert st.changed is True
    assert rois.get("_layout_changed") is True
    layout_state = rois.get("_layout_state")
    assert isinstance(layout_state, dict)
    assert layout_state["roi_offset_px"] == [5.0, 6.0]
