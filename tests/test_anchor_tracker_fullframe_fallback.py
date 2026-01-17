import os
from pathlib import Path

import cv2
import numpy as np

from vision.anchor_tracker import AnchorTracker


def _roi_to_px(_frame: np.ndarray, _rois: dict, resolution: tuple[int, int], roi_norm: dict):
    w, h = resolution
    x = int(round(float(roi_norm.get("x", 0.0)) * float(w)))
    y = int(round(float(roi_norm.get("y", 0.0)) * float(h)))
    rw = int(round(float(roi_norm.get("w", 0.0)) * float(w)))
    rh = int(round(float(roi_norm.get("h", 0.0)) * float(h)))
    return x, y, max(1, rw), max(1, rh)


def _make_edge_rich_template(*, tw: int, th: int) -> np.ndarray:
    img = np.zeros((th, tw, 3), dtype=np.uint8)
    # Strong edges: border + diagonals + a few rectangles.
    cv2.rectangle(img, (2, 2), (tw - 3, th - 3), (255, 255, 255), 2)
    cv2.line(img, (0, 0), (tw - 1, th - 1), (255, 255, 255), 2)
    cv2.line(img, (0, th - 1), (tw - 1, 0), (255, 255, 255), 2)
    cv2.rectangle(img, (tw // 4, th // 4), (tw // 4 + 12, th // 4 + 12), (255, 255, 255), -1)
    cv2.rectangle(img, (tw // 2, th // 3), (tw // 2 + 18, th // 3 + 18), (255, 255, 255), 1)
    return img


def test_anchor_tracker_fullframe_fallback_when_template_does_not_fit_local_window(tmp_path, monkeypatch):
    """Ensures AnchorTracker can recover via full-frame search.

    This test specifically exercises the case where:
    - the expected ROI is far from the real anchor
    - the local search window is small
    - the template does not fit inside the local window

    In that case the tracker should fall back to full-frame matching and still
    compute a (dx, dy) offset.
    """

    frame_w, frame_h = 420, 360
    frame = np.zeros((frame_h, frame_w, 3), dtype=np.uint8)

    # Create a template that is intentionally larger than the local search window.
    tmpl = _make_edge_rich_template(tw=180, th=140)
    tmpl_path = Path(tmp_path) / "tmpl.png"
    assert cv2.imwrite(str(tmpl_path), tmpl)

    # Place the template at a known location in the frame.
    anchor_x, anchor_y = 40, 55
    frame[anchor_y : anchor_y + tmpl.shape[0], anchor_x : anchor_x + tmpl.shape[1]] = tmpl

    # ROIs config: put the expected ROI in the opposite corner, and make it small.
    # This makes the local window around expected too small to contain the template.
    rois = {
        # Provide ONLY this ROI so auto-selection is deterministic.
        # Make it large enough that its (expected) local window near bottom-right
        # still does not include the real anchor, but small enough that the
        # local window stays smaller than the template (forcing full-frame retry).
        "hpmp_top_strip": {"x": 0.86, "y": 0.86, "w": 0.30, "h": 0.12},
        "_source_resolution": [frame_w, frame_h],
    }

    # Force a small local search window.
    monkeypatch.setenv("ANCHOR_SEARCH_RADIUS_PX", "35")
    monkeypatch.setenv("ANCHOR_TEMPLATE_PATH", str(tmpl_path))
    # AnchorTracker clamps smoothing to <= 0.95, so set it explicitly to make
    # the expected first-step offset deterministic.
    monkeypatch.setenv("ANCHOR_SMOOTHING", "0.95")
    monkeypatch.delenv("ANCHOR_MIN_SCORE", raising=False)

    tr = AnchorTracker()
    tr.maybe_update(frame=frame, rois=rois, resolution=(frame_w, frame_h), roi_to_px=_roi_to_px)

    off = rois.get("_roi_offset_px")
    score = rois.get("_roi_offset_score")

    assert isinstance(off, list) and len(off) == 2
    assert isinstance(score, float)

    # Expected anchor match top-left should be at (anchor_x, anchor_y), while expected
    # ROI top-left is near bottom-right. So dx/dy should be strongly negative.
    exp_x, exp_y, _w, _h = _roi_to_px(frame, rois, (frame_w, frame_h), rois["hpmp_top_strip"])
    # First update is smoothed (EMA). With smoothing=0.95 and initial offset=0,
    # the stored offset becomes 0.95 * true_offset.
    s = 0.95
    assert abs(off[0] - float(anchor_x - exp_x) * s) <= 6.0
    assert abs(off[1] - float(anchor_y - exp_y) * s) <= 6.0
