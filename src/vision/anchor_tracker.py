from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

import cv2
import numpy as np


@dataclass
class AnchorConfig:
    roi_norm: Mapping[str, Any]
    template_path: str
    search_radius_px: int = 220
    min_score: float = 0.55
    update_interval_s: float = 0.5
    smoothing: float = 0.35
    canny_low: int = 60
    canny_high: int = 140
    max_shift_src_px: float = 800.0


class AnchorTracker:
    """Tracks a HUD anchor via template matching and applies a global ROI offset.

    If the user moves the in-game HUD panels, the configured ROIs stop lining up.
    This tracker re-finds a small anchor region and shifts all ROIs by a (dx,dy)
    expressed in *source pixel* coordinates.

    Runtime contract:
    - Reads config from `rois.get('_anchor')`.
    - Writes offset to `rois['_roi_offset_px'] = [dx_src, dy_src]`.
    """

    def __init__(self) -> None:
        self._tmpl_path: str | None = None
        self._tmpl_edges: Optional[np.ndarray] = None
        self._last_update_ts: float = 0.0
        self._offset_src: Tuple[float, float] = (0.0, 0.0)
        self._last_score: float = 0.0

    @staticmethod
    def _enabled() -> bool:
        return os.getenv("ANCHOR_ENABLED", "1").strip().lower() not in {"0", "false", "no"}

    @staticmethod
    def _load_cfg(rois: Mapping[str, Any]) -> Optional[AnchorConfig]:
        if not hasattr(rois, "get"):
            return None
        raw = rois.get("_anchor")
        if not isinstance(raw, Mapping):
            return None
        roi_norm = raw.get("roi_norm")
        template_path = str(raw.get("template_path") or "").strip()
        if not isinstance(roi_norm, Mapping) or not template_path:
            return None

        def _i(key: str, default: int) -> int:
            try:
                return int(raw.get(key, default))
            except Exception:
                return default

        def _f(key: str, default: float) -> float:
            try:
                return float(raw.get(key, default))
            except Exception:
                return default

        return AnchorConfig(
            roi_norm=roi_norm,
            template_path=template_path,
            search_radius_px=max(40, _i("search_radius_px", 220)),
            min_score=max(0.0, min(1.0, _f("min_score", 0.55))),
            update_interval_s=max(0.05, _f("update_interval_s", 0.5)),
            smoothing=max(0.0, min(0.95, _f("smoothing", 0.35))),
            canny_low=max(0, _i("canny_low", 60)),
            canny_high=max(0, _i("canny_high", 140)),
            max_shift_src_px=max(50.0, _f("max_shift_src_px", 800.0)),
        )

    @staticmethod
    def _repo_root() -> Path:
        # src/vision/anchor_tracker.py -> repo root
        return Path(__file__).resolve().parents[2]

    @staticmethod
    def _to_gray(img: np.ndarray) -> np.ndarray:
        if img is None or getattr(img, "size", 0) == 0:
            return np.zeros((1, 1), dtype=np.uint8)
        if len(img.shape) == 2:
            return img
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    def _ensure_template(self, cfg: AnchorConfig) -> bool:
        if self._tmpl_edges is not None and self._tmpl_path == cfg.template_path:
            return True

        path = Path(cfg.template_path)
        if not path.is_absolute():
            path = self._repo_root() / path

        try:
            img = cv2.imread(str(path))
        except Exception:
            img = None

        if img is None or getattr(img, "size", 0) == 0:
            self._tmpl_edges = None
            self._tmpl_path = None
            return False

        g = self._to_gray(img)
        try:
            edges = cv2.Canny(g, cfg.canny_low, cfg.canny_high)
        except Exception:
            edges = g

        if edges is None or edges.size == 0:
            self._tmpl_edges = None
            self._tmpl_path = None
            return False

        self._tmpl_edges = edges
        self._tmpl_path = cfg.template_path
        return True

    @staticmethod
    def _compute_scale(frame_w: int, frame_h: int, source_w: int, source_h: int) -> float:
        try:
            return float(min(frame_w / source_w, frame_h / source_h)) if source_w and source_h else 1.0
        except Exception:
            return 1.0

    def maybe_update(self, *, frame: np.ndarray, rois: Mapping[str, Any], resolution: Tuple[int, int], roi_to_px) -> None:
        if not self._enabled():
            return

        cfg = self._load_cfg(rois)
        if cfg is None:
            return

        now = time.time()
        if (now - self._last_update_ts) < cfg.update_interval_s:
            return

        if not self._ensure_template(cfg):
            return

        # Compute expected anchor location WITHOUT applying current offset.
        rois_base: dict[str, Any] = dict(rois) if isinstance(rois, Mapping) else {}
        rois_base.pop("_roi_offset_px", None)
        rois_base.pop("_roi_offset_score", None)

        try:
            x_exp, y_exp, w_exp, h_exp = roi_to_px(frame, rois_base, resolution, cfg.roi_norm)
        except Exception:
            return

        if w_exp <= 2 or h_exp <= 2:
            return

        frame_h, frame_w = int(frame.shape[0]), int(frame.shape[1])

        # Search window around expected.
        r = int(cfg.search_radius_px)
        sx0 = max(0, int(x_exp - r))
        sy0 = max(0, int(y_exp - r))
        sx1 = min(frame_w, int(x_exp + w_exp + r))
        sy1 = min(frame_h, int(y_exp + h_exp + r))
        if sx1 - sx0 < 10 or sy1 - sy0 < 10:
            return

        win = frame[sy0:sy1, sx0:sx1]
        win_g = self._to_gray(win)
        try:
            win_edges = cv2.Canny(win_g, cfg.canny_low, cfg.canny_high)
        except Exception:
            win_edges = win_g

        tmpl = self._tmpl_edges
        if tmpl is None:
            return

        # Template must fit inside window.
        th, tw = int(tmpl.shape[0]), int(tmpl.shape[1])
        if th <= 0 or tw <= 0 or th >= win_edges.shape[0] or tw >= win_edges.shape[1]:
            return

        try:
            win_arr = np.asarray(win_edges, dtype=np.uint8)
            tmpl_arr = np.asarray(tmpl, dtype=np.uint8)
            res = cv2.matchTemplate(win_arr, tmpl_arr, cv2.TM_CCOEFF_NORMED)  # type: ignore[arg-type]
            _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(res)
        except Exception:
            return

        score = float(max_val)
        if score < cfg.min_score:
            # Don't update offset if match is weak; keep last known offset.
            self._last_update_ts = now
            self._last_score = score
            return

        found_x = int(sx0 + int(max_loc[0]))
        found_y = int(sy0 + int(max_loc[1]))

        dx_frame = float(found_x - int(x_exp))
        dy_frame = float(found_y - int(y_exp))

        # Convert to source px.
        source_resolution = rois.get("_source_resolution") if hasattr(rois, "get") else None
        if isinstance(source_resolution, (list, tuple)) and len(source_resolution) == 2:
            source_w, source_h = int(source_resolution[0] or 0), int(source_resolution[1] or 0)
        else:
            source_w, source_h = int(resolution[0]), int(resolution[1])

        scale = self._compute_scale(frame_w, frame_h, source_w, source_h)
        if scale <= 0:
            scale = 1.0

        dx_src = dx_frame / scale
        dy_src = dy_frame / scale

        # Clamp to avoid wild jumps.
        try:
            dx_src = max(-cfg.max_shift_src_px, min(cfg.max_shift_src_px, dx_src))
            dy_src = max(-cfg.max_shift_src_px, min(cfg.max_shift_src_px, dy_src))
        except Exception:
            pass

        # EMA smoothing.
        a = float(cfg.smoothing)
        ox, oy = self._offset_src
        new_ox = (1.0 - a) * ox + a * dx_src
        new_oy = (1.0 - a) * oy + a * dy_src
        self._offset_src = (float(new_ox), float(new_oy))
        self._last_update_ts = now
        self._last_score = score

        # Write into the shared rois mapping if it's mutable.
        try:
            if isinstance(rois, dict):
                rois["_roi_offset_px"] = [float(self._offset_src[0]), float(self._offset_src[1])]
                rois["_roi_offset_score"] = float(score)
        except Exception:
            pass
