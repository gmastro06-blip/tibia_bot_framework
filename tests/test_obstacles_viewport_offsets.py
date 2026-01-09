from __future__ import annotations

from vision.obstacles import compute_viewport_tile_offsets


def test_compute_viewport_tile_offsets_basic() -> None:
    # Viewport 320x320 centered at (160,160)
    vp = (0, 0, 320, 320)
    boxes = [
        {"x": 160 + 32, "y": 160, "width": 10, "height": 10, "class": "orc", "confidence": 0.9},
        {"x": 160, "y": 160 - 64, "width": 10, "height": 10, "class": "dragon", "confidence": 0.9},
    ]

    offs = compute_viewport_tile_offsets(boxes, viewport_rect=vp, tile_px=32, min_conf=0.25, ignore_classes=set())
    assert (1, 0) in offs
    assert (0, -2) in offs


def test_compute_viewport_tile_offsets_filters_player_tile_and_conf() -> None:
    vp = (10, 10, 200, 200)
    vcx = 10 + 100
    vcy = 10 + 100
    boxes = [
        {"x": vcx, "y": vcy, "width": 10, "height": 10, "class": "orc", "confidence": 0.99},
        {"x": vcx + 32, "y": vcy, "width": 10, "height": 10, "class": "orc", "confidence": 0.1},
    ]

    offs = compute_viewport_tile_offsets(boxes, viewport_rect=vp, tile_px=32, min_conf=0.25, ignore_classes=set())
    # (0,0) is dropped, and low confidence is dropped
    assert offs == []
