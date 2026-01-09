from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from telemetry.overlay_export import OverlayConfig, OverlayExporter


def test_overlay_exporter_disabled_writes_nothing(tmp_path: Path) -> None:
    out_dir = tmp_path / "overlay"
    exporter = OverlayExporter(
        OverlayConfig(enabled=False, interval_s=0.0, out_dir=str(out_dir), draw_tile_grid=True, tile_px=32)
    )

    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    exporter.maybe_export(frame)

    assert list(out_dir.glob("*.png")) == []


def test_overlay_exporter_writes_png_when_enabled(tmp_path: Path) -> None:
    cv2 = pytest.importorskip("cv2")

    out_dir = tmp_path / "overlay"
    exporter = OverlayExporter(
        OverlayConfig(enabled=True, interval_s=0.0, out_dir=str(out_dir), draw_tile_grid=True, tile_px=32)
    )

    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    exporter.maybe_export(
        frame,
        viewport_rect=(5, 5, 20, 20),
        boxes=[{"x": 32, "y": 32, "width": 10, "height": 12, "class": "orc", "confidence": 0.9}],
        blocked_offsets=[(1, 0), (0, 1)],
        target_label="orc",
    )

    pngs = list(out_dir.glob("*.png"))
    assert len(pngs) == 1

    # Sanity: OpenCV can read it back.
    img = cv2.imread(str(pngs[0]))
    assert img is not None
    assert img.shape[0] > 0 and img.shape[1] > 0
