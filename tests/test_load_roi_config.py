from __future__ import annotations

from src.main import load_roi_config


def test_load_roi_config_known_resolution_1920x1080() -> None:
    rois, source_resolution = load_roi_config((1920, 1080))
    assert isinstance(rois, dict)
    assert source_resolution in ([1920, 1080], [2048, 1076])
    assert "hpmp_top_strip" in rois
    assert "game_viewport" in rois


def test_load_roi_config_fallback_resolution() -> None:
    # Unknown resolution should fall back to 1920x1080 config file per current logic.
    rois, source_resolution = load_roi_config((9999, 9999))
    assert isinstance(rois, dict)
    assert "hpmp_top_strip" in rois
    assert isinstance(source_resolution, list)
    assert len(source_resolution) == 2
