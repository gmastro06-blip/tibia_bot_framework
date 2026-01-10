from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Mapping, Tuple

import cv2
import numpy as np


@dataclass
class ViewportTrackConfig:
    update_interval_s: float = 0.5
    smoothing: float = 0.35
    min_w_px: int = 200
    min_h_px: int = 200
    pad_px: int = 2
    search_radius_px: int = 220
    # Tuned defaults: react quickly when a side panel opens/closes,
    # but stay stable when nothing changes.
    snap_threshold_px: int = 30
    smoothing_small: float = 0.25
    smoothing_large: float = 0.85


class ViewportTracker:
    """Auto-adjusts the `game_viewport` ROI when side panels/bars change.

    Heuristic approach:
    - Uses known UI ROIs (top strip + chat + right-side panels) to define a search band.
    - Computes per-column texture/variance in that band.
    - Finds a contiguous high-variance span and treats it as the game viewport.

    It writes a *source-px* ROI override into `rois['game_viewport']` with `unit='px'`.
    This keeps all downstream consumers working through OCRProcessor._roi_to_px.
    """

    def __init__(self) -> None:
        self._last_update_ts: float = 0.0
        self._last_src_rect: Tuple[float, float, float, float] | None = None

    @staticmethod
    def _enabled() -> bool:
        return os.getenv("VIEWPORT_AUTO_ENABLED", "1").strip().lower() not in {"0", "false", "no"}

    @staticmethod
    def _cfg() -> ViewportTrackConfig:
        def _f(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, str(default)).strip() or str(default))
            except Exception:
                return float(default)

        def _i(name: str, default: int) -> int:
            try:
                return int(float(os.getenv(name, str(default)).strip() or str(default)))
            except Exception:
                return int(default)

        return ViewportTrackConfig(
            update_interval_s=max(0.05, _f("VIEWPORT_UPDATE_INTERVAL_S", 0.5)),
            smoothing=max(0.0, min(0.95, _f("VIEWPORT_SMOOTHING", 0.35))),
            min_w_px=max(50, _i("VIEWPORT_MIN_W_PX", 200)),
            min_h_px=max(50, _i("VIEWPORT_MIN_H_PX", 200)),
            pad_px=max(0, _i("VIEWPORT_PAD_PX", 2)),
            search_radius_px=max(40, _i("VIEWPORT_SEARCH_RADIUS_PX", 220)),
            snap_threshold_px=max(10, _i("VIEWPORT_SNAP_THRESHOLD_PX", 30)),
            smoothing_small=max(0.0, min(0.95, _f("VIEWPORT_SMOOTHING_SMALL", 0.25))),
            smoothing_large=max(0.0, min(0.95, _f("VIEWPORT_SMOOTHING_LARGE", 0.85))),
        )

    @staticmethod
    def _compute_scale_and_offsets(
        *, frame_w: int, frame_h: int, source_w: int, source_h: int
    ) -> tuple[float, float, float]:
        scale = float(min(frame_w / source_w, frame_h / source_h)) if source_w and source_h else 1.0
        content_w = float(source_w) * scale
        content_h = float(source_h) * scale
        offset_x = (float(frame_w) - content_w) / 2.0
        offset_y = (float(frame_h) - content_h) / 2.0
        return scale, offset_x, offset_y

    @staticmethod
    def _frame_to_src_rect(
        *,
        fx: int,
        fy: int,
        fw: int,
        fh: int,
        frame_w: int,
        frame_h: int,
        source_w: int,
        source_h: int,
    ) -> tuple[float, float, float, float] | None:
        scale, off_x, off_y = ViewportTracker._compute_scale_and_offsets(
            frame_w=frame_w, frame_h=frame_h, source_w=source_w, source_h=source_h
        )
        if scale <= 0:
            return None

        x_src = (float(fx) - off_x) / scale
        y_src = (float(fy) - off_y) / scale
        w_src = float(fw) / scale
        h_src = float(fh) / scale

        # Clamp to source bounds.
        x_src = max(0.0, min(float(source_w - 1), x_src))
        y_src = max(0.0, min(float(source_h - 1), y_src))
        w_src = max(1.0, min(float(source_w) - x_src, w_src))
        h_src = max(1.0, min(float(source_h) - y_src, h_src))
        return x_src, y_src, w_src, h_src

    def maybe_update(self, *, frame: np.ndarray, rois: Mapping[str, Any], resolution: Tuple[int, int], roi_to_px) -> None:
        if not self._enabled():
            return
        if not isinstance(rois, dict):
            return

        cfg = self._cfg()
        now = time.time()
        if (now - self._last_update_ts) < cfg.update_interval_s:
            return

        try:
            source_resolution = rois.get("_source_resolution")
            if isinstance(source_resolution, (list, tuple)) and len(source_resolution) == 2:
                source_w, source_h = int(source_resolution[0] or 0), int(source_resolution[1] or 0)
            else:
                source_w, source_h = int(resolution[0]), int(resolution[1])
        except Exception:
            source_w, source_h = int(resolution[0]), int(resolution[1])

        frame_h, frame_w = int(frame.shape[0]), int(frame.shape[1])

        # Establish vertical band: below top strip, above chat.
        y0 = 0
        y1 = frame_h
        try:
            if rois.get("hpmp_top_strip") is not None:
                tx, ty, tw, th = roi_to_px(frame, rois, resolution, rois["hpmp_top_strip"])
                y0 = max(y0, int(ty + th + 1))
        except Exception:
            pass
        try:
            if rois.get("chat_panel") is not None:
                cx, cy, cw, ch = roi_to_px(frame, rois, resolution, rois["chat_panel"])
                y1 = min(y1, int(cy - 1))
        except Exception:
            pass

        if (y1 - y0) < cfg.min_h_px:
            self._last_update_ts = now
            return

        # Establish horizontal bounds using right-side panels as hard stop.
        x0 = 0
        x1 = frame_w
        right_candidates: list[int] = []
        for roi_key in ("skills_panel", "battlelist_panel", "right_hud_panel"):
            try:
                if rois.get(roi_key) is not None:
                    px, py, pw, ph = roi_to_px(frame, rois, resolution, rois[roi_key])
                    right_candidates.append(int(px))
            except Exception:
                continue
        if right_candidates:
            x1 = min(x1, max(0, min(right_candidates) - 1))

        if (x1 - x0) < cfg.min_w_px:
            self._last_update_ts = now
            return

        band = frame[y0:y1, x0:x1]
        if band is None or getattr(band, "size", 0) == 0:
            self._last_update_ts = now
            return

        try:
            g = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
        except Exception:
            try:
                g = np.asarray(band[:, :, 0], dtype=np.uint8)
            except Exception:
                self._last_update_ts = now
                return

        # (1) Prefer border detection: strong vertical UI borders pop in Sobel-x.
        vx0: int | None = None
        vx1: int | None = None
        method = "sobel_peaks"
        try:
            gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
            col_grad = np.mean(np.abs(gx), axis=0)

            win = max(7, int(col_grad.size * 0.01))
            if win % 2 == 0:
                win += 1
            kernel = np.ones((win,), dtype=np.float32) / float(win)
            grad_s = np.convolve(col_grad.astype(np.float32), kernel, mode="same")

            # Use current game_viewport as a prior: search for borders near expected edges.
            exp_left = int(col_grad.size * 0.10)
            exp_right = int(col_grad.size * 0.90)
            try:
                if rois.get("game_viewport") is not None:
                    ex, ey, ew, eh = roi_to_px(frame, rois, resolution, rois["game_viewport"])
                    exp_left = max(0, min(int(col_grad.size - 1), int(ex - x0)))
                    exp_right = max(0, min(int(col_grad.size - 1), int((ex + ew) - x0)))
            except Exception:
                pass

            r = int(cfg.search_radius_px)

            def _argmax_in(lo: int, hi: int) -> int | None:
                lo2 = max(0, int(lo))
                hi2 = min(int(col_grad.size), int(hi))
                if hi2 - lo2 < 3:
                    return None
                seg = grad_s[lo2:hi2]
                try:
                    j = int(np.argmax(seg))
                except Exception:
                    return None
                return int(lo2 + j)

            li = _argmax_in(exp_left - r, exp_left + r)
            ri = _argmax_in(exp_right - r, exp_right + r)
            if li is not None and ri is not None and (ri - li) >= cfg.min_w_px:
                vx0 = int(x0 + li + 1)
                vx1 = int(x0 + ri - 1)
        except Exception:
            vx0 = None
            vx1 = None

        # (2) Fallback: texture span (variance-based) if borders weren't found.
        if vx0 is None or vx1 is None or (vx1 - vx0) < cfg.min_w_px:
            method = "col_std"
            try:
                col_std = g.astype(np.float32).std(axis=0)
                if col_std.size < cfg.min_w_px:
                    self._last_update_ts = now
                    return

                win = max(7, int(col_std.size * 0.01))
                if win % 2 == 0:
                    win += 1
                kernel = np.ones((win,), dtype=np.float32) / float(win)
                smooth = np.convolve(col_std, kernel, mode="same")

                med = float(np.median(smooth))
                mad = float(np.median(np.abs(smooth - med)))
                thr = med + max(3.0, 3.0 * mad)
                if float(np.max(smooth)) > 0:
                    thr = min(thr, float(np.max(smooth)) * 0.85)
                mask = smooth >= thr

                # If the robust threshold is too strict (common on darker scenes),
                # fall back to a percentile mask to still get a usable estimate.
                if not bool(mask.any()):
                    try:
                        thr2 = float(np.percentile(smooth, 70.0))
                        mask = smooth >= thr2
                    except Exception:
                        mask = smooth >= float(np.max(smooth)) * 0.5

                if not bool(mask.any()):
                    self._last_update_ts = now
                    return

                best_l = 0
                best_r = 0
                cur_l = None
                for i, v in enumerate(mask.tolist()):
                    if v and cur_l is None:
                        cur_l = i
                    if (not v) and cur_l is not None:
                        if (i - cur_l) > (best_r - best_l):
                            best_l, best_r = int(cur_l), int(i)
                        cur_l = None
                if cur_l is not None:
                    i = int(len(mask))
                    if (i - cur_l) > (best_r - best_l):
                        best_l, best_r = int(cur_l), int(i)

                if (best_r - best_l) < cfg.min_w_px:
                    self._last_update_ts = now
                    return

                vx0 = int(x0 + best_l)
                vx1 = int(x0 + best_r)
            except Exception:
                self._last_update_ts = now
                return

        # Compose viewport rect in frame coords.
        vx0 = int(vx0) - cfg.pad_px
        vx1 = int(vx1) + cfg.pad_px
        vy0 = int(y0) - cfg.pad_px
        vy1 = int(y1) + cfg.pad_px

        vx0 = max(0, min(frame_w - 2, vx0))
        vx1 = max(vx0 + 2, min(frame_w, vx1))
        vy0 = max(0, min(frame_h - 2, vy0))
        vy1 = max(vy0 + 2, min(frame_h, vy1))

        # Convert to source px coordinates.
        src_rect = self._frame_to_src_rect(
            fx=vx0,
            fy=vy0,
            fw=int(vx1 - vx0),
            fh=int(vy1 - vy0),
            frame_w=frame_w,
            frame_h=frame_h,
            source_w=source_w,
            source_h=source_h,
        )
        if src_rect is None:
            self._last_update_ts = now
            return

        # Smooth in source space, but "snap" faster when a UI panel suddenly opens/closes.
        if self._last_src_rect is not None:
            ox, oy, ow, oh = self._last_src_rect
            nx, ny, nw, nh = src_rect

            # Estimate the magnitude of change in *frame px* (approx) by comparing source px.
            # This is good enough to detect sudden layout changes.
            try:
                delta = max(abs(float(nx) - float(ox)), abs(float(nw) - float(ow)))
            except Exception:
                delta = 0.0

            # Choose smoothing factor.
            # - cfg.smoothing_small for stable tracking
            # - cfg.smoothing_large for sudden changes
            a = float(cfg.smoothing_small)
            try:
                if delta >= float(cfg.snap_threshold_px):
                    a = float(cfg.smoothing_large)
            except Exception:
                a = float(cfg.smoothing_small)

            # Back-compat: allow VIEWPORT_SMOOTHING to override both if user wants.
            try:
                if os.getenv("VIEWPORT_SMOOTHING", "").strip() != "":
                    a = float(cfg.smoothing)
            except Exception:
                pass

            src_rect = (
                (1.0 - a) * ox + a * nx,
                (1.0 - a) * oy + a * ny,
                (1.0 - a) * ow + a * nw,
                (1.0 - a) * oh + a * nh,
            )

        self._last_src_rect = src_rect
        self._last_update_ts = now

        try:
            x_src, y_src, w_src, h_src = src_rect
            rois["game_viewport"] = {
                "unit": "px",
                "x": float(x_src),
                "y": float(y_src),
                "w": float(w_src),
                "h": float(h_src),
            }
            rois["_viewport_auto"] = {
                "ts": float(now),
                "method": str(method),
            }
        except Exception:
            pass
