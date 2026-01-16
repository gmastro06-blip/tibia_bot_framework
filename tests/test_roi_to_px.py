from __future__ import annotations

from vision.roi import roi_to_px_result


def test_roi_to_px_result_normalized_ok() -> None:
    frame_shape = (100, 200)  # h,w
    resolution = (200, 100)
    rois = {"_source_resolution": [200, 100]}
    roi_def = {"x": 0.25, "y": 0.10, "w": 0.50, "h": 0.20}

    res = roi_to_px_result(frame_shape=frame_shape, rois=rois, resolution=resolution, roi_def=roi_def)
    assert res.ok is True
    assert res.roi is not None
    x, y, w, h = res.roi
    assert w > 0 and h > 0
    assert 0 <= x < frame_shape[1]
    assert 0 <= y < frame_shape[0]


def test_roi_to_px_result_out_of_bounds_invalid() -> None:
    frame_shape = (100, 100)
    resolution = (100, 100)
    rois = {"_source_resolution": [100, 100]}

    # ROI starts far outside
    roi_def = {"unit": "px", "x": 500, "y": 500, "w": 50, "h": 50}
    res = roi_to_px_result(frame_shape=frame_shape, rois=rois, resolution=resolution, roi_def=roi_def)
    assert res.ok is False
    assert res.reason.startswith("invalid_roi")


def test_roi_to_px_result_global_offset_applied() -> None:
    frame_shape = (100, 200)
    resolution = (200, 100)

    rois = {
        "_source_resolution": [200, 100],
        "_roi_offset_px": [10, 0],
    }
    roi_def = {"unit": "px", "x": 0, "y": 0, "w": 20, "h": 10}

    res = roi_to_px_result(frame_shape=frame_shape, rois=rois, resolution=resolution, roi_def=roi_def)
    assert res.ok is True
    assert res.roi is not None
    x, y, w, h = res.roi
    assert x >= 8  # roughly +10 after rounding
    assert y == 0
    assert w > 0 and h > 0
