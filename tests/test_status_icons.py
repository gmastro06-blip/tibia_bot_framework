from __future__ import annotations

import numpy as np

from vision.status_icons import detect_status_icons


def _mk_template(seed: int) -> np.ndarray:
    # Non-constant grayscale template (avoids zero-norm in NCC)
    rng = np.random.default_rng(seed)
    t = (rng.integers(low=0, high=255, size=(10, 10), dtype=np.uint8) // 2) * 2
    # Add a strong distinctive block
    t[2:8, 2:8] = 220
    t[4:6, 4:6] = 30
    return t


def test_detect_status_icons_template_matching_npy(tmp_path, monkeypatch) -> None:
    # Create synthetic templates and a synthetic ROI that contains them.
    tpl_dir = tmp_path / "status_icons"
    tpl_dir.mkdir(parents=True, exist_ok=True)

    par = _mk_template(1)
    has = _mk_template(2)
    uta = _mk_template(3)

    np.save(str(tpl_dir / "paralyzed.npy"), par)
    np.save(str(tpl_dir / "haste_active.npy"), has)
    np.save(str(tpl_dir / "utamo_active.npy"), uta)

    monkeypatch.setenv("STATUS_ICONS_TEMPLATES_DIR", str(tpl_dir))
    monkeypatch.setenv("STATUS_ICON_STRIDE", "1")

    roi = np.zeros((60, 80, 3), dtype=np.uint8)
    # Paste templates at known locations
    roi[5 : 5 + par.shape[0], 5 : 5 + par.shape[1], :] = par[:, :, None]
    roi[5 : 5 + has.shape[0], 30 : 30 + has.shape[1], :] = has[:, :, None]
    roi[5 : 5 + uta.shape[0], 55 : 55 + uta.shape[1], :] = uta[:, :, None]

    conf = detect_status_icons(roi)
    assert set(conf.keys()) == {"paralyzed", "haste_active", "utamo_active"}

    assert conf["paralyzed"] > 0.95
    assert conf["haste_active"] > 0.95
    assert conf["utamo_active"] > 0.95


def test_detect_status_icons_returns_empty_when_no_templates(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("STATUS_ICONS_TEMPLATES_DIR", str(tmp_path / "does_not_exist"))
    roi = np.zeros((20, 20, 3), dtype=np.uint8)
    assert detect_status_icons(roi) == {}
