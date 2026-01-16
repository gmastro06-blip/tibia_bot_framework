from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from vision.roi import roi_to_px_result


@dataclass(frozen=True)
class LayoutState:
    """Snapshot of layout-related state.

    This is intentionally lightweight and JSON-friendly.
    """

    ts: float
    changed: bool
    roi_offset_px: tuple[float, float] | None
    roi_offset_score: float | None
    viewport_auto: dict[str, Any] | None
    reason: str


class LayoutTracker:
    """Orchestrates layout adaptation (anchor + viewport) and exports state.

    This class is a thin wrapper around existing trackers:
    - AnchorTracker updates `rois['_roi_offset_px']`
    - ViewportTracker updates `rois['game_viewport']` + `rois['_viewport_auto']`

    It additionally:
    - Detects whether layout-related state changed this tick.
    - Stores a compact snapshot in `rois['_layout_state']` for replay/overlay.

    New behavior can be disabled via env `LAYOUT_TRACKER_ENABLED=0`.
    """

    def __init__(
        self,
        *,
        anchor_tracker: Any | None = None,
        viewport_tracker: Any | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._anchor = anchor_tracker
        self._viewport = viewport_tracker
        self._enabled = enabled

        self._last_offset: tuple[float, float] | None = None
        self._last_viewport: tuple[float, float, float, float] | None = None

    def is_enabled(self) -> bool:
        if self._enabled is not None:
            return bool(self._enabled)
        try:
            return (str(__import__("os").environ.get("LAYOUT_TRACKER_ENABLED", "1"))).strip().lower() not in {
                "0",
                "false",
                "no",
            }
        except Exception:
            return True

    @staticmethod
    def _get_offset(rois: Mapping[str, Any]) -> tuple[float, float] | None:
        try:
            off = rois.get("_roi_offset_px") if hasattr(rois, "get") else None
            if isinstance(off, (list, tuple)) and len(off) >= 2:
                return (float(off[0] or 0.0), float(off[1] or 0.0))
        except Exception:
            return None
        return None

    @staticmethod
    def _get_viewport_src_rect(rois: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
        try:
            v = rois.get("game_viewport") if hasattr(rois, "get") else None
            if not isinstance(v, Mapping):
                return None
            unit = str(v.get("unit", "") or "").strip().lower()
            if unit != "px":
                return None
            x = float(v.get("x", 0.0) or 0.0)
            y = float(v.get("y", 0.0) or 0.0)
            w = float(v.get("w", 0.0) or 0.0)
            h = float(v.get("h", 0.0) or 0.0)
            if w <= 0 or h <= 0:
                return None
            return (x, y, w, h)
        except Exception:
            return None

    def update(self, *, frame, rois: Mapping[str, Any], resolution: tuple[int, int], roi_to_px: Callable) -> LayoutState:
        """Run trackers (best-effort) and export `LayoutState`.

        Side-effects:
        - May mutate `rois` if it is a dict.
        - Writes `rois['_layout_state']` and `rois['_layout_changed']`.
        """

        now = float(time.time())
        if not self.is_enabled() or not isinstance(rois, Mapping):
            st = LayoutState(
                ts=now,
                changed=False,
                roi_offset_px=self._get_offset(rois),
                roi_offset_score=None,
                viewport_auto=None,
                reason="disabled",
            )
            try:
                if isinstance(rois, dict):
                    rois["_layout_state"] = {
                        "ts": st.ts,
                        "changed": st.changed,
                        "reason": st.reason,
                    }
                    rois["_layout_changed"] = False
            except Exception:
                pass
            return st

        # Snapshot before
        off0 = self._get_offset(rois)
        vp0 = self._get_viewport_src_rect(rois)

        # Run underlying trackers (best-effort)
        try:
            if self._anchor is not None and hasattr(self._anchor, "maybe_update"):
                self._anchor.maybe_update(frame=frame, rois=rois, resolution=resolution, roi_to_px=roi_to_px)
        except Exception:
            pass
        try:
            if self._viewport is not None and hasattr(self._viewport, "maybe_update"):
                self._viewport.maybe_update(frame=frame, rois=rois, resolution=resolution, roi_to_px=roi_to_px)
        except Exception:
            pass

        off1 = self._get_offset(rois)
        vp1 = self._get_viewport_src_rect(rois)

        changed = False
        try:
            if off1 != off0:
                changed = True
            if vp1 != vp0:
                changed = True
        except Exception:
            changed = False

        # Persist baselines (for future diffing)
        self._last_offset = off1
        self._last_viewport = vp1

        score = None
        try:
            raw = rois.get("_roi_offset_score") if hasattr(rois, "get") else None
            if raw is not None:
                score = float(raw)
        except Exception:
            score = None

        viewport_auto = None
        try:
            raw = rois.get("_viewport_auto") if hasattr(rois, "get") else None
            if isinstance(raw, dict):
                viewport_auto = dict(raw)
        except Exception:
            viewport_auto = None

        reason = "ok"
        if off1 is None and vp1 is None:
            reason = "no_layout_signals"

        st = LayoutState(
            ts=now,
            changed=bool(changed),
            roi_offset_px=off1,
            roi_offset_score=score,
            viewport_auto=viewport_auto,
            reason=reason,
        )

        try:
            if isinstance(rois, dict):
                rois["_layout_state"] = {
                    "ts": st.ts,
                    "changed": st.changed,
                    "roi_offset_px": list(st.roi_offset_px) if st.roi_offset_px is not None else None,
                    "roi_offset_score": st.roi_offset_score,
                    "viewport_auto": st.viewport_auto,
                    "reason": st.reason,
                }
                rois["_layout_changed"] = bool(st.changed)
                rois["_layout_ts"] = float(st.ts)
        except Exception:
            pass

        return st

    @staticmethod
    def apply_layout(
        *,
        frame_shape: tuple[int, int],
        rois: Mapping[str, Any],
        resolution: tuple[int, int],
        names: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Convert a set of ROI definitions into clamped frame-pixel rectangles.

        Returns a JSON-friendly mapping:
          name -> {"roi": [x,y,w,h] or None, "ok": bool, "reason": str}

        NOTE: This does *not* mutate rois. It is purely a conversion step.
        """

        out: dict[str, dict[str, Any]] = {}
        if not isinstance(rois, Mapping):
            return out

        keys: list[str]
        if names is not None:
            keys = [str(k) for k in names if str(k)]
        else:
            try:
                keys = [str(k) for k in rois.keys() if isinstance(k, str)]
            except Exception:
                keys = []

        for k in keys:
            try:
                roi_def = rois.get(k)
            except Exception:
                roi_def = None
            if not isinstance(roi_def, Mapping):
                continue
            rr = roi_to_px_result(
                frame_shape=(int(frame_shape[0]), int(frame_shape[1])),
                rois=rois,
                resolution=resolution,
                roi_def=roi_def,
            )
            out[k] = {
                "ok": bool(rr.ok),
                "roi": list(rr.roi) if rr.roi is not None else None,
                "reason": str(rr.reason or ""),
            }
        return out
