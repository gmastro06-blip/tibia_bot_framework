from __future__ import annotations

import numpy as np

from src.vision.minimap_motion import MinimapMotionConfig, MinimapMotionTracker


def _make_pattern(h: int = 112, w: int = 112) -> np.ndarray:
    """Deterministic textured BGR pattern for phase correlation."""
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    x = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    base = (np.sin(10 * x) + np.cos(8 * y) + np.sin(6 * (x + y)))
    base = (base - base.min()) / (base.max() - base.min() + 1e-6)
    img = (base * 255).astype(np.uint8)
    # BGR
    return np.stack([img, np.roll(img, 7, axis=1), np.roll(img, 11, axis=0)], axis=-1)


def _shift(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    out = np.roll(img, shift=(dy, dx), axis=(0, 1))
    return out


def test_scroll_mode_emits_one_tile_step() -> None:
    # If the minimap content scrolls right by tile_px, player moved left by 1 tile.
    cfg = MinimapMotionConfig(
        mode="scroll",
        tile_px=4,
        min_response=0.05,
        max_shift_px=32.0,
        max_step_per_frame=3,
        invert_x=False,
        invert_y=False,
        emit_threshold_tiles=0.40,
        deadband_tiles=0.0,
        smooth_n=1,
    )
    tr = MinimapMotionTracker(cfg)

    img0 = _make_pattern()
    img1 = _shift(img0, dx=+4, dy=0)  # map shifted right => player moved left

    assert tr.update(img0) is None
    step = tr.update(img1)
    assert step is not None

    dx, dy, resp = step
    assert dy == 0
    assert dx == -1
    assert resp >= 0.0


def test_marker_mode_emits_after_accumulation() -> None:
    cfg = MinimapMotionConfig(
        mode="marker",
        tile_px=4,
        min_response=0.15,
        max_shift_px=32.0,
        max_step_per_frame=3,
        invert_x=False,
        invert_y=False,
        emit_threshold_tiles=0.30,
        deadband_tiles=0.0,
        smooth_n=1,
        marker_v_min=220,
        marker_s_max=90,
        marker_area_min=1,
        marker_area_max=500,
        marker_local_std_min=0.0,  # synthetic image is clean
    )
    tr = MinimapMotionTracker(cfg)

    # Base minimap with texture.
    base = _make_pattern()

    def draw_marker(img: np.ndarray, cx: int, cy: int) -> np.ndarray:
        out = img.copy()
        # white-ish marker blob
        rr = 2
        y0 = max(0, cy - rr)
        y1 = min(out.shape[0], cy + rr + 1)
        x0 = max(0, cx - rr)
        x1 = min(out.shape[1], cx + rr + 1)
        out[y0:y1, x0:x1] = (255, 255, 255)
        return out

    img0 = draw_marker(base, 56, 56)
    img1 = draw_marker(base, 58, 56)  # +2px => 0.5 tiles

    assert tr.update(img0) is None
    step = tr.update(img1)
    assert step is not None

    dx, dy, resp = step
    assert dy == 0
    assert dx in (0, 1)
    assert resp >= 0.0


def test_deadband_kills_jitter() -> None:
    cfg = MinimapMotionConfig(
        mode="marker",
        tile_px=4,
        emit_threshold_tiles=0.30,
        deadband_tiles=0.20,
        smooth_n=1,
        marker_local_std_min=0.0,
        marker_area_min=1,
        marker_area_max=500,
    )
    tr = MinimapMotionTracker(cfg)

    base = _make_pattern()

    def draw_marker(img: np.ndarray, cx: int, cy: int) -> np.ndarray:
        out = img.copy()
        rr = 2
        out[max(0, cy - rr) : cy + rr + 1, max(0, cx - rr) : cx + rr + 1] = (255, 255, 255)
        return out

    img0 = draw_marker(base, 56, 56)
    # jitter: 0.5px equivalent in tiles would be 0.125 < deadband
    img1 = draw_marker(base, 57, 56)

    assert tr.update(img0) is None
    assert tr.update(img1) is None
