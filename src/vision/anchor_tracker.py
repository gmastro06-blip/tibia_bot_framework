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
    template_path: str = ""
    template_mode: str = "file"  # file|self
    search_radius_px: int = 560
    min_score: float = 0.45
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
        if isinstance(raw, Mapping):
            roi_norm = raw.get("roi_norm")
            template_path = str(raw.get("template_path") or "").strip()
            template_mode = str(raw.get("template_mode") or "file").strip().lower() or "file"
            if not isinstance(roi_norm, Mapping):
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
                template_mode=template_mode,
                search_radius_px=max(40, _i("search_radius_px", 560)),
                min_score=max(0.0, min(1.0, _f("min_score", 0.45))),
                update_interval_s=max(0.05, _f("update_interval_s", 0.5)),
                smoothing=max(0.0, min(0.95, _f("smoothing", 0.35))),
                canny_low=max(0, _i("canny_low", 60)),
                canny_high=max(0, _i("canny_high", 140)),
                max_shift_src_px=max(50.0, _f("max_shift_src_px", 800.0)),
            )

        # No explicit _anchor config: try an automatic anchor based on a *real* template file.
        # We intentionally do NOT use template_mode=self here: learning a template from the
        # (possibly wrong) expected ROI can lock onto the wrong place and report dx=0.
        auto_roi = None
        for key in ("equipment_slots", "hpmp_low_panel", "skills_panel", "right_hud_panel"):
            try:
                v = rois.get(key)
                if isinstance(v, Mapping):
                    auto_roi = v
                    break
            except Exception:
                continue
        if auto_roi is None:
            return None

        # Template path: env override or default.
        tmpl = os.getenv("ANCHOR_TEMPLATE_PATH", "").strip()
        if not tmpl:
            tmpl = str(Path("data") / "anchors" / "hud_anchor.png")
        try:
            p = Path(tmpl)
            if not p.is_absolute():
                # repo_root/src/vision/... -> repo root is 3 parents up
                repo_root = Path(__file__).resolve().parents[2]
                p = (repo_root / p).resolve()
            if not p.exists() or not p.is_file():
                return None
            tmpl = str(p)
        except Exception:
            return None

        # Tunables via env for aggressive recovery.
        try:
            sr = int(float(os.getenv("ANCHOR_SEARCH_RADIUS_PX", "560").strip() or "560"))
        except Exception:
            sr = 560
        try:
            ms = float(os.getenv("ANCHOR_MIN_SCORE", "0.45").strip() or "0.45")
        except Exception:
            ms = 0.45
        try:
            interval = float(os.getenv("ANCHOR_UPDATE_INTERVAL_S", "0.5").strip() or "0.5")
        except Exception:
            interval = 0.5
        try:
            smoothing = float(os.getenv("ANCHOR_SMOOTHING", "0.35").strip() or "0.35")
        except Exception:
            smoothing = 0.35

        return AnchorConfig(
            roi_norm=auto_roi,
            template_path=tmpl,
            template_mode="file",
            search_radius_px=max(80, sr),
            min_score=max(0.0, min(1.0, ms)),
            update_interval_s=max(0.05, interval),
            smoothing=max(0.0, min(0.95, smoothing)),
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
        if self._tmpl_edges is not None:
            if cfg.template_mode == "self":
                return True
            if self._tmpl_path == cfg.template_path:
                return True
            # Template changed
            self._tmpl_edges = None

        if cfg.template_mode == "self":
            # Will be learned from the first good expected crop.
            return False

        if not cfg.template_path:
            return False

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

    def _maybe_learn_template_from_expected(
        self,
        *,
        frame: np.ndarray,
        rect: tuple[int, int, int, int],
        cfg: AnchorConfig,
    ) -> bool:
        if cfg.template_mode != "self":
            return False
        if self._tmpl_edges is not None:
            return True

        x, y, w, h = rect
        if w <= 8 or h <= 8:
            return False
        try:
            crop = frame[int(y) : int(y + h), int(x) : int(x + w)]
        except Exception:
            return False
        if crop is None or getattr(crop, "size", 0) == 0:
            return False

        g = self._to_gray(crop)
        try:
            edges = cv2.Canny(g, cfg.canny_low, cfg.canny_high)
        except Exception:
            edges = g

        if edges is None or getattr(edges, "size", 0) == 0:
            return False

        # Keep template reasonably small for performance.
        try:
            th, tw = int(edges.shape[0]), int(edges.shape[1])
            max_dim = int(float(os.getenv("ANCHOR_TEMPLATE_MAX_PX", "140").strip() or "140"))
            if max(th, tw) > max_dim and max_dim >= 20:
                scale = float(max_dim) / float(max(th, tw))
                new_w = max(20, int(round(tw * scale)))
                new_h = max(20, int(round(th * scale)))
                edges = cv2.resize(edges, (new_w, new_h), interpolation=cv2.INTER_AREA)
        except Exception:
            pass

        self._tmpl_edges = np.asarray(edges, dtype=np.uint8)
        self._tmpl_path = "<self>"
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

        # We'll attempt to learn a self-template later if needed.
        _ = self._ensure_template(cfg)

        # Compute expected anchor location WITHOUT applying current offset.
        rois_base: dict[str, Any] = dict(rois) if isinstance(rois, Mapping) else {}
        rois_base.pop("_roi_offset_px", None)
        rois_base.pop("_roi_offset_score", None)

        try:
            x_exp, y_exp, w_exp, h_exp = roi_to_px(frame, rois_base, resolution, cfg.roi_norm)
        except Exception:
            return
        # If we run in self-template mode, learn it from the expected crop.
        try:
            self._maybe_learn_template_from_expected(frame=frame, rect=(int(x_exp), int(y_exp), int(w_exp), int(h_exp)), cfg=cfg)
        except Exception:
            pass

        if self._tmpl_edges is None:
            # Still no template, can't track.
            self._last_update_ts = now
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
