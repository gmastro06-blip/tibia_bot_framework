from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from vision.battlelist import extract_rows, parse_row
from vision.ocr import OCRProcessor


@dataclass
class Row:
    index: int
    name_raw: Optional[str]
    alive_prob: float
    selected_prob: float
    row_bbox: Tuple[int, int, int, int]  # x0, y0, x1, y1 within ROI
    confidence: float


@dataclass
class TargetCommand:
    type: str  # "select_row" | "scroll_next_page" | "clear_target"
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TargetingDebug:
    state: str = "IDLE"
    current_row: Optional[int] = None
    alive_prob: Optional[float] = None
    selected_prob: Optional[float] = None
    reason: str = ""
    rows: List[Row] = field(default_factory=list)
    roi_rect: Optional[Tuple[int, int, int, int]] = None


class BattlelistLocator:
    def __init__(self) -> None:
        self._ocr = OCRProcessor()

    def locate(self, frame: np.ndarray, rois: Dict[str, Any], resolution: Tuple[int, int]) -> Tuple[Optional[np.ndarray], float, Tuple[int, int, int, int]]:
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return None, 0.0, (0, 0, 0, 0)
        if not isinstance(rois, dict):
            return None, 0.0, (0, 0, 0, 0)
        roi_norm = rois.get("battlelist_rows") or rois.get("battlelist_panel")
        if roi_norm is None:
            return None, 0.0, (0, 0, 0, 0)
        try:
            x, y, w, h = self._ocr._roi_to_px(frame, rois, resolution, roi_norm)
            x0 = int(x)
            y0 = int(y)
            x1 = int(x + w)
            y1 = int(y + h)
            crop = frame[y0:y1, x0:x1]
            conf = 1.0 if self.validate(crop) else 0.0
            return crop, conf, (x0, y0, x1, y1)
        except Exception:
            return None, 0.0, (0, 0, 0, 0)

    def validate(self, roi: Optional[np.ndarray]) -> bool:
        if roi is None or not isinstance(roi, np.ndarray) or roi.size == 0:
            return False
        try:
            if roi.shape[0] < 10 or roi.shape[1] < 20:
                return False
            gray = roi if roi.ndim == 2 else roi[:, :, 0]
            std = float(np.std(gray))
            return std >= 6.0
        except Exception:
            return False


class BattlelistParser:
    def __init__(self, *, ocr_enabled: bool = False) -> None:
        self.ocr_enabled = bool(ocr_enabled)

    @staticmethod
    def _alive_prob(row: np.ndarray) -> float:
        try:
            import cv2

            h, w = row.shape[:2]
            if h <= 2 or w <= 2:
                return 0.0
            # Focus on left strip where bars/icons usually appear.
            x1 = max(2, int(w * 0.35))
            crop = row[:, :x1]
            hsv = np.asarray(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV))
            # green
            hsv_mat: Any = hsv
            g_low: Any = np.array([35, 80, 60], dtype=np.uint8)
            g_high: Any = np.array([85, 255, 255], dtype=np.uint8)
            g_mask = cv2.inRange(hsv_mat, g_low, g_high)
            # red (two ranges)
            r1_low: Any = np.array([0, 80, 60], dtype=np.uint8)
            r1_high: Any = np.array([10, 255, 255], dtype=np.uint8)
            r2_low: Any = np.array([170, 80, 60], dtype=np.uint8)
            r2_high: Any = np.array([180, 255, 255], dtype=np.uint8)
            r1 = cv2.inRange(hsv_mat, r1_low, r1_high)
            r2 = cv2.inRange(hsv_mat, r2_low, r2_high)
            r_mask = cv2.bitwise_or(r1, r2)
            mask = cv2.bitwise_or(g_mask, r_mask)
            ratio = float(np.count_nonzero(mask)) / float(mask.size)
            return min(1.0, max(0.0, ratio * 6.0))
        except Exception:
            return 0.0

    @staticmethod
    def _selected_prob(row: np.ndarray, baseline_v: float) -> float:
        try:
            import cv2

            # Ignore the left strip (HP bar/icons) to avoid confusing "alive"
            # indicators with the selection highlight.
            h, w = row.shape[:2]
            x0 = max(0, int(w * 0.35))
            crop = row[:, x0:] if x0 < w else row

            hsv = np.asarray(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV))
            v_mean = float(hsv[:, :, 2].mean())
            s_mean = float(hsv[:, :, 1].mean())
            # highlight usually brighter + saturated
            score = (v_mean - baseline_v) / 30.0 + (s_mean / 255.0) * 0.5
            return max(0.0, min(1.0, score))
        except Exception:
            return 0.0

    def parse(self, roi: np.ndarray) -> List[Row]:
        rows = extract_rows(roi)
        if not rows:
            return []

        baseline_v = 0.0
        try:
            import cv2

            v_means = []
            for r in rows:
                hsv = np.asarray(cv2.cvtColor(r, cv2.COLOR_BGR2HSV))
                v_means.append(float(hsv[:, :, 2].mean()))
            if v_means:
                baseline_v = float(np.median(v_means))
        except Exception:
            baseline_v = 0.0

        out: List[Row] = []
        h_total = roi.shape[0]
        row_h = max(1, h_total // max(1, len(rows)))
        for i, r in enumerate(rows):
            alive = self._alive_prob(r)
            selected = self._selected_prob(r, baseline_v)
            name_raw: Optional[str] = None
            conf = max(alive, selected)
            if self.ocr_enabled:
                try:
                    parsed = parse_row(r, i)
                    name_raw = str(parsed.get("name_raw") or parsed.get("name_display") or "").strip() or None
                    conf = max(conf, float(parsed.get("conf", 0.0) or 0.0))
                except Exception:
                    pass
            y0 = i * row_h
            y1 = h_total if i == (len(rows) - 1) else min(h_total, (i + 1) * row_h)
            out.append(
                Row(
                    index=int(i),
                    name_raw=name_raw,
                    alive_prob=float(alive),
                    selected_prob=float(selected),
                    row_bbox=(0, int(y0), int(roi.shape[1]), int(y1)),
                    confidence=float(conf),
                )
            )
        return out


class TargetingController:
    def __init__(self) -> None:
        self.state = "IDLE"
        self.current_row: Optional[int] = None
        self._dead_frames = 0
        self._missing_frames = 0
        self._candidate_row: Optional[int] = None
        self._candidate_frames = 0
        self._last_target_row: Optional[int] = None
        self._last_target_ts: float = 0.0
        self._last_scroll_ts: float = 0.0

    def _choose_next(self, rows: List[Row], *, alive_thr: float, selected_thr: float) -> Optional[Row]:
        for r in rows:
            if r.alive_prob >= alive_thr and r.selected_prob < selected_thr:
                return r
        return None

    def tick(
        self,
        frame: np.ndarray,
        gamestate: object,
        config: Any,
        *,
        rois: Optional[Dict[str, Any]] = None,
        resolution: Optional[Tuple[int, int]] = None,
        roi_to_px: Optional[Any] = None,
    ) -> Tuple[List[TargetCommand], TargetingDebug]:
        dbg = TargetingDebug()
        cmds: List[TargetCommand] = []

        # Pull config values with defaults
        alive_thr = float(getattr(config, "battlelist_alive_threshold", 0.5))
        dead_frames = int(getattr(config, "dead_debounce_frames", 6))
        select_frames = int(getattr(config, "select_debounce_frames", 3))
        scroll_cd_ms = int(getattr(config, "scroll_cooldown_ms", 800))
        target_cd_ms = int(getattr(config, "target_cooldown_ms", 500))
        now = time.time()

        if rois is None or resolution is None:
            dbg.reason = "no_rois"
            return cmds, dbg

        locator = BattlelistLocator()
        roi, conf, roi_rect = locator.locate(frame, rois, resolution)
        if roi is None or conf <= 0.0:
            dbg.reason = "no_roi"
            return cmds, dbg
        dbg.roi_rect = roi_rect

        parser = BattlelistParser(ocr_enabled=bool(getattr(config, "ocr_names", False)))
        rows = parser.parse(roi)
        dbg.rows = rows

        # Detect selected row by highlight
        selected_thr = 0.6
        selected_row = next((r for r in rows if r.selected_prob >= selected_thr), None)
        if selected_row is not None:
            self.current_row = selected_row.index

        cur_row_data = None
        if self.current_row is not None:
            for r in rows:
                if r.index == self.current_row:
                    cur_row_data = r
                    break

        # Update dead/missing counters
        if cur_row_data is None and self.current_row is not None:
            self._missing_frames += 1
        else:
            self._missing_frames = 0

        if cur_row_data is not None:
            if cur_row_data.alive_prob < alive_thr:
                self._dead_frames += 1
            else:
                self._dead_frames = 0

        # Target lost condition (dead or missing for N frames)
        if (self._dead_frames >= dead_frames) or (self._missing_frames >= dead_frames):
            self.state = "DEAD"
            dbg.reason = "dead"
            if self.current_row is not None:
                cmds.append(TargetCommand(type="clear_target", payload={}))
            self.current_row = None
            self._dead_frames = 0
            self._missing_frames = 0

        # Acquire / Engage
        if self.current_row is None:
            self.state = "ACQUIRE"
            candidate = self._choose_next(rows, alive_thr=alive_thr, selected_thr=selected_thr)
            if candidate is None:
                # Scroll if no alive rows
                if (now - self._last_scroll_ts) * 1000.0 >= float(scroll_cd_ms):
                    self._last_scroll_ts = now
                    cmds.append(TargetCommand(type="scroll_next_page", payload={}))
                    dbg.reason = "scroll"
                    self.state = "SCROLL"
            else:
                if self._candidate_row != candidate.index:
                    self._candidate_row = candidate.index
                    self._candidate_frames = 1
                else:
                    self._candidate_frames += 1
                if self._candidate_frames >= select_frames:
                    if self._last_target_row == candidate.index and (now - self._last_target_ts) * 1000.0 < float(target_cd_ms):
                        dbg.reason = "cooldown"
                    else:
                        cmds.append(TargetCommand(type="select_row", payload={"row_index": candidate.index}))
                        self.current_row = candidate.index
                        self._last_target_row = candidate.index
                        self._last_target_ts = now
                        self.state = "ENGAGE"
                        dbg.reason = "select"
        else:
            self.state = "ENGAGE"

        # Debug summary
        if self.current_row is not None and cur_row_data is not None:
            dbg.current_row = int(self.current_row)
            dbg.alive_prob = float(cur_row_data.alive_prob)
            dbg.selected_prob = float(cur_row_data.selected_prob)
        dbg.state = self.state

        return cmds, dbg
