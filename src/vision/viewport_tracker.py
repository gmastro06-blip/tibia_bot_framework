from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Tuple

import cv2
import numpy as np

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
    # Second-pass search radius when the viewport shifts a lot (e.g. 2-3 panels).
    search_radius_px_large: int = 560
    # Border peak selection tuning (Sobel)
    peak_percentile: float = 97.5
    peak_min_sep_px: int = 25
    peak_max_candidates: int = 24
    prior_weight: float = 0.8
    # Confidence / freezing
    min_confidence: float = 0.25
    # Tuned defaults: react quickly when a side panel opens/closes,
    # but stay stable when nothing changes.
    snap_threshold_px: int = 30
    smoothing_small: float = 0.25
    smoothing_large: float = 0.85
    # Discrete-state snapping: learn common (x,w) viewport states (e.g. panel combos)
    # and snap to them when close. Helps when left/right HUD panels open/close in steps.
    state_snap_px: int = 18
    max_states: int = 8


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
        # Learned discrete viewport states in *source px* space.
        # Each entry: (x, y, w, h, hits, last_ts)
        self._states: list[tuple[float, float, float, float, int, float]] = []

        # Optional panel reference templates (manual setup) to detect when
        # side panels are open and snap viewport boundaries accordingly.
        # Cache: path -> (edges, (th, tw))
        self._panel_tmpl_cache: dict[str, tuple[np.ndarray, tuple[int, int]]] = {}

    @staticmethod
    def _to_gray(img: np.ndarray) -> np.ndarray:
        if img is None or getattr(img, "size", 0) == 0:
            return np.zeros((1, 1), dtype=np.uint8)
        if img.ndim == 2:
            return img
        try:
            return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        except Exception:
            return img.reshape(-1).astype(np.uint8)

    def _load_panel_refs(self, rois: Mapping[str, Any]) -> dict[str, Any] | None:
        if not isinstance(rois, dict):
            return None
        raw = rois.get("_panel_refs")
        if isinstance(raw, dict) and raw:
            return raw
        return None

    def _load_panel_template(self, *, path: str, canny_low: int, canny_high: int) -> tuple[np.ndarray, tuple[int, int]] | None:
        p = (path or "").strip()
        if not p:
            return None
        cached = self._panel_tmpl_cache.get(p)
        if cached is not None:
            return cached
        try:
            img = cv2.imread(p)
        except Exception:
            img = None
        if img is None or getattr(img, "size", 0) == 0:
            return None
        g = self._to_gray(img)
        try:
            edges = cv2.Canny(g, int(canny_low), int(canny_high))
        except Exception:
            edges = g
        edges = np.asarray(edges, dtype=np.uint8)
        th, tw = int(edges.shape[0]), int(edges.shape[1])
        if th <= 0 or tw <= 0:
            return None
        self._panel_tmpl_cache[p] = (edges, (th, tw))
        return (edges, (th, tw))

    def _panel_match(
        self,
        *,
        frame: np.ndarray,
        rois: Mapping[str, Any],
        resolution: Tuple[int, int],
        roi_to_px,
        ref: Mapping[str, Any],
    ) -> tuple[float, int, int, int, int] | None:
        """Return (score, found_x, found_y, tw, th) in frame pixels."""

        if frame is None or getattr(frame, "size", 0) == 0:
            return None
        if not isinstance(ref, Mapping):
            return None

        roi_norm = ref.get("roi_norm")
        if not isinstance(roi_norm, Mapping):
            return None

        template_path = str(ref.get("template_path") or "").strip()
        if not template_path:
            return None

        # Resolve relative paths from repo root.
        try:
            if not os.path.isabs(template_path):
                # src/vision/viewport_tracker.py -> repo root
                repo_root = Path(__file__).resolve().parents[2]
                template_path = str((repo_root / template_path).resolve())
        except Exception:
            pass

        try:
            canny_low = int(ref.get("canny_low", 60))
        except Exception:
            canny_low = 60
        try:
            canny_high = int(ref.get("canny_high", 140))
        except Exception:
            canny_high = 140

        tmpl_loaded = self._load_panel_template(path=template_path, canny_low=canny_low, canny_high=canny_high)
        if tmpl_loaded is None:
            return None
        tmpl_edges, (th, tw) = tmpl_loaded

        try:
            x_exp, y_exp, w_exp, h_exp = roi_to_px(frame, rois, resolution, roi_norm)
        except Exception:
            return None

        frame_h, frame_w = int(frame.shape[0]), int(frame.shape[1])
        try:
            r = int(ref.get("search_radius_px", 560))
        except Exception:
            r = 560
        r = max(40, min(1200, int(r)))
        sx0 = max(0, int(x_exp - r))
        sy0 = max(0, int(y_exp - r))
        sx1 = min(frame_w, int(x_exp + w_exp + r))
        sy1 = min(frame_h, int(y_exp + h_exp + r))
        if (sx1 - sx0) < max(20, tw + 2) or (sy1 - sy0) < max(20, th + 2):
            return None

        win = frame[sy0:sy1, sx0:sx1]
        g = self._to_gray(win)
        try:
            win_edges = cv2.Canny(g, int(canny_low), int(canny_high))
        except Exception:
            win_edges = g

        try:
            from typing import cast

            win_u8 = cast("cv2.Mat", np.asarray(win_edges, dtype=np.uint8))
            tmpl_u8 = cast("cv2.Mat", tmpl_edges)
            res = cv2.matchTemplate(win_u8, tmpl_u8, cv2.TM_CCOEFF_NORMED)
            _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(res)
        except Exception:
            return None

        score = float(max_val)
        try:
            min_score = float(ref.get("min_score", 0.55))
        except Exception:
            min_score = 0.55
        if score < float(min_score):
            return None

        found_x = int(sx0 + int(max_loc[0]))
        found_y = int(sy0 + int(max_loc[1]))
        return score, found_x, found_y, int(tw), int(th)

    def _estimate_panel_clips(
        self, *, x: float, w: float
    ) -> tuple[float, float, int | None, int | None, float | None, float | None]:
        """Estimate how much the viewport is clipped on left/right (in source px).

        Returns:
          (left_clip_px, right_clip_px, left_panels_est, right_panels_est, unit_left_px, unit_right_px)

        This is purely heuristic and learns from the set of observed viewport states.
        Works well when there are a few discrete panel configurations (0..3 panels).
        """
        if not self._states:
            return 0.0, 0.0, None, None, None, None

        # Baseline: most "open" viewport => max width; tie-breaker: smallest x.
        base = max(self._states, key=lambda s: (float(s[2]), -float(s[0]), int(s[4])))
        bx, by, bw, bh, hits, last_ts = base
        try:
            bx = float(bx)
            bw = float(bw)
        except Exception:
            return 0.0, 0.0, None, None, None, None

        left_clip = max(0.0, float(x) - float(bx))
        right_clip = max(0.0, (float(bx) + float(bw)) - (float(x) + float(w)))

        # Learn a "unit" width per side as the smallest positive clip observed.
        def _unit(side: str) -> float | None:
            vals: list[float] = []
            for sx, sy, sw, sh, shits, slast in self._states:
                try:
                    sx = float(sx)
                    sw = float(sw)
                except Exception:
                    continue
                l = max(0.0, float(sx) - float(bx))
                r = max(0.0, (float(bx) + float(bw)) - (float(sx) + float(sw)))
                v = l if side == "left" else r
                if v >= 8.0:
                    vals.append(float(v))
            if not vals:
                return None
            return float(min(vals))

        unit_l = _unit("left")
        unit_r = _unit("right")

        def _est(clip: float, unit: float | None) -> int | None:
            if unit is None or unit <= 0:
                return None
            if clip < (0.5 * unit):
                return 0
            n = int(round(float(clip) / float(unit)))
            return max(0, min(3, n))

        return left_clip, right_clip, _est(left_clip, unit_l), _est(right_clip, unit_r), unit_l, unit_r

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
            search_radius_px_large=max(80, _i("VIEWPORT_SEARCH_RADIUS_PX_LARGE", 560)),
            peak_percentile=max(80.0, min(99.9, _f("VIEWPORT_PEAK_PERCENTILE", 97.5))),
            peak_min_sep_px=max(5, _i("VIEWPORT_PEAK_MIN_SEP_PX", 25)),
            peak_max_candidates=max(6, _i("VIEWPORT_PEAK_MAX_CANDIDATES", 24)),
            prior_weight=max(0.0, min(5.0, _f("VIEWPORT_PRIOR_WEIGHT", 0.8))),
            min_confidence=max(0.0, min(1.0, _f("VIEWPORT_MIN_CONFIDENCE", 0.25))),
            snap_threshold_px=max(10, _i("VIEWPORT_SNAP_THRESHOLD_PX", 30)),
            smoothing_small=max(0.0, min(0.95, _f("VIEWPORT_SMOOTHING_SMALL", 0.25))),
            smoothing_large=max(0.0, min(0.95, _f("VIEWPORT_SMOOTHING_LARGE", 0.85))),
            state_snap_px=max(5, _i("VIEWPORT_STATE_SNAP_PX", 18)),
            max_states=max(2, _i("VIEWPORT_MAX_STATES", 8)),
        )

    def _state_snap(self, src_rect: tuple[float, float, float, float], *, now: float, cfg: ViewportTrackConfig) -> tuple[float, float, float, float]:
        """Snap to a learned discrete viewport state when close.

        This is designed for Tibia layouts where left/right HUD panels open/close
        in a small set of discrete configurations.
        """
        try:
            nx, ny, nw, nh = (float(src_rect[0]), float(src_rect[1]), float(src_rect[2]), float(src_rect[3]))
        except Exception:
            return src_rect

        best_i: int | None = None
        best_d = 1e18
        for i, (sx, sy, sw, sh, hits, last_ts) in enumerate(self._states):
            # Distance mostly on (x,w); y/h are usually stable due to top strip + chat bounds.
            d = abs(nx - float(sx)) + abs(nw - float(sw))
            if d < best_d:
                best_d = d
                best_i = i

        snap_thr = float(cfg.state_snap_px)
        if best_i is not None and best_d <= snap_thr:
            sx, sy, sw, sh, hits, _ = self._states[best_i]
            hits = int(hits) + 1
            self._states[best_i] = (float(sx), float(sy), float(sw), float(sh), hits, float(now))
            # Snap to the state's x/w, keep ny/nh if they drift slightly.
            return (float(sx), float(ny), float(sw), float(nh))

        # Otherwise learn/update a new state.
        self._states.append((float(nx), float(ny), float(nw), float(nh), 1, float(now)))
        # Prune: keep most recent/high-hit states.
        if len(self._states) > int(cfg.max_states):
            self._states.sort(key=lambda s: (-(int(s[4])), -float(s[5])))
            self._states = self._states[: int(cfg.max_states)]
        return src_rect

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

        # Establish horizontal bounds using panel reference templates (optional)
        # and right-side panels as hard stop.
        x0 = 0
        x1 = frame_w

        # (A) Optional: explicit panel refs (manual templates) to detect when panels are open.
        panel_refs = self._load_panel_refs(rois)
        panel_dbg = {}
        if panel_refs is not None:
            try:
                left_ref = panel_refs.get("left") if isinstance(panel_refs, dict) else None
            except Exception:
                left_ref = None
            try:
                right_ref = panel_refs.get("right") if isinstance(panel_refs, dict) else None
            except Exception:
                right_ref = None

            # Left panel: we want viewport start at the RIGHT edge of the matched template.
            try:
                if isinstance(left_ref, Mapping):
                    m = self._panel_match(frame=frame, rois=rois, resolution=resolution, roi_to_px=roi_to_px, ref=left_ref)
                    if m is not None:
                        s, fx, fy, tw, th = m
                        x0 = max(x0, int(fx + tw + 1))
                        panel_dbg["left_score"] = float(s)
                        panel_dbg["left_x0"] = int(fx)
                        panel_dbg["left_w"] = int(tw)
            except Exception:
                pass

            # Right panel: we want viewport end at the LEFT edge of the matched template.
            try:
                if isinstance(right_ref, Mapping):
                    m = self._panel_match(frame=frame, rois=rois, resolution=resolution, roi_to_px=roi_to_px, ref=right_ref)
                    if m is not None:
                        s, fx, fy, tw, th = m
                        # fx is the left edge of the panel template.
                        x1 = min(x1, int(fx - 1))
                        panel_dbg["right_score"] = float(s)
                        panel_dbg["right_x0"] = int(fx)
                        panel_dbg["right_w"] = int(tw)
            except Exception:
                pass

        # Optional: if the config defines left panel ROIs, use their right edge as a hard start.
        left_candidates: list[int] = []
        for roi_key in (
            "left_hud_panel",
            "left_hud",
            "left_panel",
            "left_sidebar",
        ):
            try:
                if rois.get(roi_key) is not None:
                    px, py, pw, ph = roi_to_px(frame, rois, resolution, rois[roi_key])
                    left_candidates.append(int(px + pw))
            except Exception:
                continue
        if left_candidates:
            cand0 = int(max(left_candidates) + 1)
            if 0 <= cand0 <= int(frame_w * 0.45):
                x0 = max(x0, cand0)
        right_candidates: list[int] = []
        for roi_key in ("skills_panel", "battlelist_panel", "right_hud_panel"):
            try:
                if rois.get(roi_key) is not None:
                    px, py, pw, ph = roi_to_px(frame, rois, resolution, rois[roi_key])
                    right_candidates.append(int(px))
            except Exception:
                continue
        if right_candidates:
            # Only apply if the candidate is plausibly near the right side.
            # (ROI configs can be off; a bad hard-stop would clip the viewport.)
            cand = int(max(0, min(right_candidates) - 1))
            if int(frame_w * 0.55) <= cand <= int(frame_w - 40):
                x1 = min(x1, cand)

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
        confidence: float | None = None
        meta: dict[str, Any] = {}

        try:
            if isinstance(panel_dbg, dict) and panel_dbg:
                meta["panel_refs"] = dict(panel_dbg)
        except Exception:
            pass
        try:
            gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
            col_grad = np.mean(np.abs(gx), axis=0)

            win = max(7, int(col_grad.size * 0.01))
            if win % 2 == 0:
                win += 1
            kernel = np.ones((win,), dtype=np.float32) / float(win)
            grad_s = np.convolve(col_grad.astype(np.float32), kernel, mode="same")

            # Use current game_viewport as a prior.
            exp_left = int(col_grad.size * 0.10)
            exp_right = int(col_grad.size * 0.90)
            try:
                if rois.get("game_viewport") is not None:
                    ex, ey, ew, eh = roi_to_px(frame, rois, resolution, rois["game_viewport"])
                    exp_left = max(0, min(int(col_grad.size - 1), int(ex - x0)))
                    exp_right = max(0, min(int(col_grad.size - 1), int((ex + ew) - x0)))
            except Exception:
                pass

            # Candidate peak selection: choose a pair of strong border peaks.
            # This is more robust than a single argmax when the map has strong textures.
            pctl = float(cfg.peak_percentile)
            try:
                thr = float(np.percentile(grad_s, pctl))
            except Exception:
                thr = float(np.max(grad_s)) * 0.85
            thr = max(thr, float(np.median(grad_s)) + 1e-6)
            mx = float(np.max(grad_s)) if grad_s.size else 0.0

            # Find local maxima above threshold.
            idx = np.where(grad_s >= thr)[0]
            peaks: list[tuple[int, float]] = []
            if idx.size:
                for i in idx.tolist():
                    ii = int(i)
                    if ii <= 0 or ii >= int(grad_s.size - 1):
                        continue
                    v = float(grad_s[ii])
                    if v >= float(grad_s[ii - 1]) and v >= float(grad_s[ii + 1]):
                        peaks.append((ii, v))

            # Fallback: if threshold too strict, use top-K by value.
            if not peaks:
                try:
                    k = min(int(cfg.peak_max_candidates), int(grad_s.size))
                    top_idx = np.argsort(grad_s)[-k:]
                    peaks = [(int(i), float(grad_s[int(i)])) for i in top_idx.tolist()]
                except Exception:
                    peaks = []

            # Keep best candidates with min separation.
            peaks.sort(key=lambda t: (-t[1], t[0]))
            selected: list[int] = []
            min_sep = int(cfg.peak_min_sep_px)
            for i, v in peaks:
                if all(abs(int(i) - int(j)) >= min_sep for j in selected):
                    selected.append(int(i))
                if len(selected) >= int(cfg.peak_max_candidates):
                    break
            selected.sort()

            best_pair: tuple[int, int] | None = None
            best_score = -1e18
            prior_w = float(cfg.prior_weight)

            # Constrain within band width.
            max_x = int(col_grad.size - 1)
            for idx_l in range(len(selected)):
                li = int(selected[idx_l])
                for idx_r in range(idx_l + 1, len(selected)):
                    ri = int(selected[idx_r])
                    width = int(ri - li)
                    if width < int(cfg.min_w_px):
                        continue
                    if width > max_x:
                        continue

                    # Score: peak strength + closeness to prior edges.
                    s = float(grad_s[li]) + float(grad_s[ri])
                    if prior_w > 0.0:
                        s -= prior_w * (abs(li - int(exp_left)) + abs(ri - int(exp_right))) / 2.0

                    # Prefer wider viewport a bit (helps when one border is missed).
                    s += 0.01 * float(width)

                    if s > best_score:
                        best_score = s
                        best_pair = (li, ri)

            if best_pair is not None:
                li, ri = best_pair
                vx0 = int(x0 + li + 1)
                vx1 = int(x0 + ri - 1)

                # Confidence: how strong are the selected peaks above the percentile threshold.
                try:
                    peak_strength = (float(grad_s[int(li)]) + float(grad_s[int(ri)])) / 2.0
                    if mx > thr:
                        confidence = max(0.0, min(1.0, (peak_strength - thr) / (mx - thr)))
                    else:
                        confidence = 0.0
                except Exception:
                    confidence = None

                meta = {
                    "thr": float(thr),
                    "max": float(mx),
                    "li": int(li),
                    "ri": int(ri),
                }
        except Exception:
            vx0 = None
            vx1 = None
            confidence = None
            meta = {}

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

                # Confidence from how far the chosen span's mean is above threshold.
                try:
                    mx = float(np.max(smooth)) if smooth.size else 0.0
                    span = smooth[int(best_l) : int(best_r)]
                    span_mean = float(np.mean(span)) if span.size else 0.0
                    if mx > float(thr):
                        confidence = max(0.0, min(1.0, (span_mean - float(thr)) / (mx - float(thr))))
                    else:
                        confidence = 0.0
                except Exception:
                    confidence = None

                meta = {
                    "thr": float(thr),
                    "max": float(mx) if 'mx' in locals() else None,
                    "l": int(best_l),
                    "r": int(best_r),
                }
            except Exception:
                self._last_update_ts = now
                return

        # Compose viewport rect in frame coords.
        vx0_i: int = int(vx0) - int(cfg.pad_px)
        vx1_i: int = int(vx1) + int(cfg.pad_px)
        vy0_i: int = int(y0) - int(cfg.pad_px)
        vy1_i: int = int(y1) + int(cfg.pad_px)

        vx0_i = int(max(0, min(int(frame_w - 2), int(vx0_i))))
        vx1_i = int(max(int(vx0_i + 2), min(int(frame_w), int(vx1_i))))
        vy0_i = int(max(0, min(int(frame_h - 2), int(vy0_i))))
        vy1_i = int(max(int(vy0_i + 2), min(int(frame_h), int(vy1_i))))

        vx0, vx1, vy0, vy1 = vx0_i, vx1_i, vy0_i, vy1_i

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

        # Freeze: if confidence is low, keep last known good viewport.
        frozen = False
        try:
            if confidence is not None and self._last_src_rect is not None:
                if float(confidence) < float(cfg.min_confidence):
                    src_rect = self._last_src_rect
                    frozen = True
        except Exception:
            frozen = False

        # Smooth in source space, but "snap" faster when a UI panel suddenly opens/closes.
        # Also snap to learned discrete states (panel combos) when close.
        src_rect = self._state_snap(src_rect, now=now, cfg=cfg)
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
            alpha: float = float(cfg.smoothing_small)
            try:
                if delta >= float(cfg.snap_threshold_px):
                    alpha = float(cfg.smoothing_large)
            except Exception:
                alpha = float(cfg.smoothing_small)

            # Back-compat: allow VIEWPORT_SMOOTHING to override both if user wants.
            try:
                if os.getenv("VIEWPORT_SMOOTHING", "").strip() != "":
                    alpha = float(cfg.smoothing)
            except Exception:
                pass

            src_rect = (
                (1.0 - alpha) * ox + alpha * nx,
                (1.0 - alpha) * oy + alpha * ny,
                (1.0 - alpha) * ow + alpha * nw,
                (1.0 - alpha) * oh + alpha * nh,
            )

        self._last_src_rect = src_rect
        self._last_update_ts = now

        try:
            x_src, y_src, w_src, h_src = src_rect
            # Panel estimates (0..3) derived from learned viewport states.
            lclip, rclip, lcnt, rcnt, unit_l, unit_r = self._estimate_panel_clips(x=float(x_src), w=float(w_src))
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
                "confidence": None if confidence is None else float(confidence),
                "min_confidence": float(cfg.min_confidence),
                "frozen": bool(frozen),
                "meta": dict(meta) if isinstance(meta, dict) else {},
                "panels": {
                    "left_clip_px": float(lclip),
                    "right_clip_px": float(rclip),
                    "left_panels_est": lcnt,
                    "right_panels_est": rcnt,
                    "unit_left_px": None if unit_l is None else float(unit_l),
                    "unit_right_px": None if unit_r is None else float(unit_r),
                    "n_states": int(len(self._states)),
                },
            }

            # Optional: propagate the learned viewport left/right bounds to related UI ROIs.
            # This helps when side panels open/close (main area width changes), keeping
            # top bars + chat horizontally aligned.
            try:
                enabled = os.getenv("VIEWPORT_AUTO_UPDATE_UI_ROIS", "1").strip().lower() not in {"0", "false", "no"}
            except Exception:
                enabled = True

            if enabled:
                main_left = float(x_src)
                main_w = float(w_src)
                main_right = float(x_src) + float(w_src)

                def _src_rect(name: str) -> tuple[float, float, float, float] | None:
                    try:
                        v = rois.get(name)
                    except Exception:
                        return None
                    if not isinstance(v, Mapping):
                        return None
                    try:
                        unit = str(v.get("unit", "") or "").strip().lower()
                    except Exception:
                        unit = ""

                    def _f(x: Any) -> float | None:
                        try:
                            if x is None:
                                return None
                            return float(x)
                        except Exception:
                            return None

                    x = _f(v.get("x"))
                    y = _f(v.get("y"))
                    w = _f(v.get("w"))
                    h = _f(v.get("h"))
                    if x is None or y is None or w is None or h is None:
                        return None

                    is_norm = unit != "px" and 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and 0.0 <= w <= 1.0 and 0.0 <= h <= 1.0
                    if is_norm:
                        return (x * float(source_w), y * float(source_h), w * float(source_w), h * float(source_h))
                    return (float(x), float(y), float(w), float(h))

                def _set_px(name: str, x: float, y: float, w: float, h: float) -> None:
                    try:
                        if name not in rois:
                            return
                        rois[name] = {
                            "unit": "px",
                            "x": float(max(0.0, min(float(source_w - 1), x))),
                            "y": float(max(0.0, min(float(source_h - 1), y))),
                            "w": float(max(1.0, min(float(source_w) - max(0.0, x), w))),
                            "h": float(max(1.0, min(float(source_h) - max(0.0, y), h))),
                        }
                    except Exception:
                        return

                # 1) Top strip spans exactly the main area (same left/right as viewport).
                top = _src_rect("hpmp_top_strip")
                if top is not None:
                    tx, ty, tw, th = top
                    if float(tw) > 1.0:
                        _set_px("hpmp_top_strip", main_left, float(ty), main_w, float(th))

                        # Adjust child ROIs within the strip (HP/MP OCR) proportionally.
                        for child in ("hp_top_ocr", "mp_top_ocr"):
                            c = _src_rect(child)
                            if c is None:
                                continue
                            cx, cy, cw, ch = c
                            try:
                                rel_x = (float(cx) - float(tx)) / float(tw)
                                rel_w = float(cw) / float(tw)
                            except Exception:
                                continue
                            # Keep vertical placement stable (relative to strip if possible).
                            try:
                                rel_y = (float(cy) - float(ty)) / float(th) if float(th) > 0 else 0.0
                                rel_h = float(ch) / float(th) if float(th) > 0 else 1.0
                            except Exception:
                                rel_y, rel_h = 0.0, 1.0
                            _set_px(
                                child,
                                float(main_left + rel_x * main_w),
                                float(ty + rel_y * th),
                                float(rel_w * main_w),
                                float(rel_h * th),
                            )

                # 2) Chat panel spans exactly the main area (keep y/h as configured).
                chat = _src_rect("chat_panel")
                if chat is not None:
                    cx, cy, cw, ch = chat
                    _set_px("chat_panel", main_left, float(cy), main_w, float(ch))

                # 3) Convenience: expose main right edge in debug meta.
                try:
                    if isinstance(rois.get("_viewport_auto"), dict):
                        rois["_viewport_auto"]["main"] = {
                            "left_px": float(main_left),
                            "right_px": float(main_right),
                            "w_px": float(main_w),
                        }
                except Exception:
                    pass
        except Exception:
            pass
