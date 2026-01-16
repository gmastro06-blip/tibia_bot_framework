import importlib.util
from pathlib import Path
import sys

import numpy as np


def _load_roi_sanity_module():
    repo_root = Path(__file__).resolve().parent.parent
    mod_path = repo_root / "tools" / "roi_sanity_check.py"
    spec = importlib.util.spec_from_file_location("roi_sanity_check", mod_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Required for Python 3.12+ dataclasses when __future__.annotations is used.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_uniform_optional_icon_is_ok_or_warn():
    m = _load_roi_sanity_module()

    frame = np.full((64, 64, 3), 50, dtype=np.uint8)  # uniform, not black
    rect = (0, 0, 32, 32)

    chk = m._check_roi(
        name="hungry_icon",
        frame=frame,
        rect=rect,
        min_w=8,
        min_h=8,
        std_fail=1.0,
        std_warn=3.0,
        black_fail_pct=0.98,
        black_warn_pct=0.90,
    )

    assert chk.status in {"OK", "WARN"}
    assert "uniform" in chk.reason


def test_uniform_battlelist_is_warn_not_fail():
    m = _load_roi_sanity_module()

    frame = np.full((128, 128, 3), 80, dtype=np.uint8)
    rect = (10, 10, 64, 64)

    chk = m._check_roi(
        name="battlelist_rows",
        frame=frame,
        rect=rect,
        min_w=16,
        min_h=16,
        std_fail=1.0,
        std_warn=3.0,
        black_fail_pct=0.98,
        black_warn_pct=0.90,
    )

    assert chk.status == "WARN"
    assert "uniform" in chk.reason


def test_uniform_viewport_still_fails():
    m = _load_roi_sanity_module()

    frame = np.full((128, 128, 3), 80, dtype=np.uint8)
    rect = (0, 0, 128, 128)

    chk = m._check_roi(
        name="game_viewport",
        frame=frame,
        rect=rect,
        min_w=16,
        min_h=16,
        std_fail=1.0,
        std_warn=3.0,
        black_fail_pct=0.98,
        black_warn_pct=0.90,
    )

    assert chk.status == "FAIL"
    assert "uniform" in chk.reason


def test_states_icons_min_size_relaxed():
    m = _load_roi_sanity_module()

    min_w, min_h = m._default_min_size("states_icons")
    assert min_w <= 16
    assert min_h <= 15
