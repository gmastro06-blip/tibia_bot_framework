import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from runtime_config import BattlelistTargetingConfig
from vision import battlelist_targeting
from vision.battlelist_targeting import TargetingController


def _make_roi(*, rows: int = 4, width: int = 200, row_h: int = 20, alive: bool = True) -> np.ndarray:
    height = rows * row_h
    roi = np.zeros((height, width, 3), dtype=np.uint8)
    roi[:] = (10, 10, 10)

    if alive:
        y0 = 0
        y1 = row_h
        cv2.rectangle(roi, (0, y0), (int(width * 0.3), y1 - 1), (0, 255, 0), -1)

    return roi


def _patch_locator(monkeypatch: pytest.MonkeyPatch, roi: np.ndarray) -> None:
    def _no_init(self) -> None:
        return None

    def _locate(self, frame, rois, resolution):
        return roi, 1.0, (0, 0, roi.shape[1], roi.shape[0])

    monkeypatch.setattr(battlelist_targeting.BattlelistLocator, "__init__", _no_init)
    monkeypatch.setattr(battlelist_targeting.BattlelistLocator, "locate", _locate)


def test_targeting_select_and_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BATTLELIST_N_ROWS", "4")
    roi = _make_roi(alive=True)
    _patch_locator(monkeypatch, roi)

    cfg = BattlelistTargetingConfig(
        autotarget_enabled=True,
        battlelist_alive_threshold=0.3,
        dead_debounce_frames=2,
        select_debounce_frames=2,
        scroll_cooldown_ms=0,
        target_cooldown_ms=0,
        ocr_names=False,
    )

    ctrl = TargetingController()
    cmds, _dbg = ctrl.tick(roi, object(), cfg, rois={"battlelist_rows": {}}, resolution=(roi.shape[1], roi.shape[0]))
    assert cmds == []

    cmds, _dbg = ctrl.tick(roi, object(), cfg, rois={"battlelist_rows": {}}, resolution=(roi.shape[1], roi.shape[0]))
    assert len(cmds) == 1
    assert cmds[0].type == "select_row"
    assert cmds[0].payload.get("row_index") == 0

    roi_dead = _make_roi(alive=False)
    _patch_locator(monkeypatch, roi_dead)

    cmds, _dbg = ctrl.tick(roi_dead, object(), cfg, rois={"battlelist_rows": {}}, resolution=(roi_dead.shape[1], roi_dead.shape[0]))
    assert cmds == []

    cmds, _dbg = ctrl.tick(roi_dead, object(), cfg, rois={"battlelist_rows": {}}, resolution=(roi_dead.shape[1], roi_dead.shape[0]))
    assert any(c.type == "clear_target" for c in cmds)


def test_targeting_scroll_when_no_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BATTLELIST_N_ROWS", "4")
    roi = _make_roi(alive=False)
    _patch_locator(monkeypatch, roi)

    cfg = BattlelistTargetingConfig(
        autotarget_enabled=True,
        battlelist_alive_threshold=0.3,
        dead_debounce_frames=2,
        select_debounce_frames=2,
        scroll_cooldown_ms=0,
        target_cooldown_ms=0,
        ocr_names=False,
    )

    ctrl = TargetingController()
    cmds, _dbg = ctrl.tick(roi, object(), cfg, rois={"battlelist_rows": {}}, resolution=(roi.shape[1], roi.shape[0]))
    assert any(c.type == "scroll_next_page" for c in cmds)
