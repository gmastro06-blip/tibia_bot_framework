from __future__ import annotations

from src.calibrate_rois_wizard import run
from src.roi_wizard_english import run as run_en
from src.create_panel_refs import run as run_panels


def test_calibrate_rois_wizard_help_exits_zero() -> None:
    # Must be non-interactive and safe in CI.
    code = run(["--help"])
    assert code == 0


def test_roi_wizard_english_help_exits_zero() -> None:
    # Must be non-interactive and safe in CI.
    code = run_en(["--help"])
    assert code == 0


def test_create_panel_refs_help_exits_zero() -> None:
    # Must be non-interactive and safe in CI.
    code = run_panels(["--help"])
    assert code == 0
