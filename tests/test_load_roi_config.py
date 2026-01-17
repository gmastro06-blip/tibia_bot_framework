from __future__ import annotations

from pathlib import Path

import pytest

from src.main import load_roi_config


def test_load_roi_config_known_resolution_1920x1080() -> None:
    rois, source_resolution, config_path = load_roi_config((1920, 1080))
    assert isinstance(rois, dict)
    assert source_resolution in ([1920, 1080], [2048, 1076])
    assert isinstance(config_path, str)
    assert "hpmp_top_strip" in rois
    assert "game_viewport" in rois


def test_load_roi_config_fallback_resolution() -> None:
    # Unknown resolution should fall back to 1920x1080 config file per current logic.
    rois, source_resolution, config_path = load_roi_config((9999, 9999))
    assert isinstance(rois, dict)
    assert "hpmp_top_strip" in rois
    assert isinstance(source_resolution, list)
    assert len(source_resolution) == 2
    assert isinstance(config_path, str)


def test_load_roi_config_override_absolute_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = {
        "source_resolution": [111, 222],
        "rois_guess_norm": {
            "hpmp_top_strip": {"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1},
            "game_viewport": {"x": 0.0, "y": 0.1, "w": 0.5, "h": 0.5},
        },
    }
    p = tmp_path / "rois_override.json"
    p.write_text(__import__("json").dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("ROIS_CONFIG", str(p))
    rois, source_resolution, config_path = load_roi_config((9999, 9999))
    assert source_resolution == [111, 222]
    assert "hpmp_top_strip" in rois
    assert "game_viewport" in rois
    assert Path(config_path).resolve() == p.resolve()


def test_load_roi_config_is_cwd_independent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # This ensures load_roi_config resolves config paths relative to repo root,
    # not the current working directory.
    monkeypatch.chdir(tmp_path)
    rois, source_resolution, config_path = load_roi_config((2048, 1076))
    assert isinstance(rois, dict)
    assert "hpmp_top_strip" in rois
    assert isinstance(source_resolution, list)
    assert len(source_resolution) == 2
    assert isinstance(config_path, str)
    assert Path(config_path).is_absolute()
