from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np


@dataclass(frozen=True)
class DebugPanelConfig:
    enabled: bool
    fps: float
    out_file: str
    width: int
    height: int


def debug_panel_config_from_env() -> DebugPanelConfig:
    enabled = os.getenv("DEBUG_PANEL_ENABLED", "").strip().lower() in {"1", "true", "yes"}
    try:
        fps = float(os.getenv("DEBUG_PANEL_FPS", "7").strip() or "7")
    except Exception:
        fps = 7.0
    fps = max(0.1, min(30.0, float(fps)))

    out_file = os.getenv("DEBUG_PANEL_OUT_FILE", "logs/debug_panel.png").strip() or "logs/debug_panel.png"

    try:
        width = int(float(os.getenv("DEBUG_PANEL_W", "800").strip() or "800"))
    except Exception:
        width = 800
    try:
        height = int(float(os.getenv("DEBUG_PANEL_H", "600").strip() or "600"))
    except Exception:
        height = 600

    width = max(320, min(1920, int(width)))
    height = max(240, min(1080, int(height)))

    return DebugPanelConfig(enabled=enabled, fps=fps, out_file=out_file, width=width, height=height)


def _fmt2(v: float | None) -> str:
    if v is None:
        return "?"
    try:
        return f"{float(v):.2f}"
    except Exception:
        return "?"


def build_debug_panel(
    *,
    status_line: str,
    coords_line: str,
    motion_img: np.ndarray | None,
    metrics: Mapping[str, float | None],
    size: tuple[int, int] = (800, 600),
) -> np.ndarray:
    """Build a black/white debug panel image.

    Layout (approx):
      - Top: 2 lines (status + coords)
      - Center: motion trace (white on black) + crosshair
      - Bottom: metrics line

    Returns: BGR uint8 image.
    """

    try:
        import cv2
    except Exception:
        # Keep a predictable return even if cv2 is missing.
        w, h = int(size[0]), int(size[1])
        return np.zeros((h, w, 3), dtype=np.uint8)

    w, h = int(size[0]), int(size[1])
    img = np.zeros((h, w, 3), dtype=np.uint8)

    top_h = 72
    bot_h = 44
    pad = 14

    def put(text: str, *, x: int, y: int) -> None:
        try:
            cv2.putText(
                img,
                str(text or ""),
                (int(x), int(y)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
        except Exception:
            pass

    # Top text
    put(status_line, x=pad, y=26)
    put(coords_line, x=pad, y=54)

    # Motion region
    mx0 = pad
    my0 = top_h
    mx1 = w - pad
    my1 = h - bot_h
    mw = max(1, mx1 - mx0)
    mh = max(1, my1 - my0)

    # Prefer a square motion viewport centered within the available region.
    mside = int(min(mw, mh))
    cx0 = int(mx0 + (mw - mside) // 2)
    cy0 = int(my0 + (mh - mside) // 2)
    cx1 = int(cx0 + mside)
    cy1 = int(cy0 + mside)

    try:
        cv2.rectangle(img, (cx0, cy0), (cx1, cy1), (255, 255, 255), 1)
    except Exception:
        pass

    if motion_img is not None and getattr(motion_img, "size", 0) > 0:
        try:
            if motion_img.ndim == 2:
                mi = cv2.cvtColor(motion_img, cv2.COLOR_GRAY2BGR)
            else:
                mi = motion_img
            mi_rs = cv2.resize(mi, (mside, mside), interpolation=cv2.INTER_NEAREST)
            dest = img[cy0:cy1, cx0:cx1]
            # White-on-black max composite.
            img[cy0:cy1, cx0:cx1] = np.maximum(dest, mi_rs)
        except Exception:
            pass
    else:
        put("no motion", x=cx0 + 10, y=cy0 + 26)

    # Crosshair (always)
    try:
        cpx = int(cx0 + mside // 2)
        cpy = int(cy0 + mside // 2)
        cv2.line(img, (cpx, cy0), (cpx, cy1), (255, 255, 255), 1)
        cv2.line(img, (cx0, cpy), (cx1, cpy), (255, 255, 255), 1)
        cv2.circle(img, (cpx, cpy), 3, (255, 255, 255), -1)
    except Exception:
        pass

    # Bottom metrics
    resp = metrics.get("response")
    dx = metrics.get("dx_tiles")
    dy = metrics.get("dy_tiles")
    ax = metrics.get("acc_dx")
    ay = metrics.get("acc_dy")

    metrics_line = f"resp={_fmt2(resp)} dx={_fmt2(dx)} dy={_fmt2(dy)} acc=({_fmt2(ax)},{_fmt2(ay)})"
    put(metrics_line, x=pad, y=h - 16)

    return img


class MotionTrace:
    """A tiny persistent canvas that accumulates motion deltas as a trail."""

    def __init__(self, *, size: int = 320, px_per_tile: float = 14.0) -> None:
        self.size = int(max(64, min(1024, int(size))))
        self.px_per_tile = float(max(1.0, min(80.0, float(px_per_tile))))
        # Use BGR so OpenCV typing + compositing are straightforward.
        self._canvas: np.ndarray = np.zeros((self.size, self.size, 3), dtype=np.uint8)
        self._x = float(self.size // 2)
        self._y = float(self.size // 2)

    def reset(self) -> None:
        self._canvas[:] = 0
        self._x = float(self.size // 2)
        self._y = float(self.size // 2)

    def update(self, *, dx_tiles: float | None, dy_tiles: float | None) -> None:
        try:
            import cv2
        except Exception:
            return

        if dx_tiles is None and dy_tiles is None:
            return

        try:
            dx = float(dx_tiles or 0.0)
            dy = float(dy_tiles or 0.0)
        except Exception:
            return

        # Light decay each tick to make a trail.
        try:
            self._canvas = (self._canvas.astype(np.float32) * 0.92).clip(0, 255).astype(np.uint8)
        except Exception:
            pass

        x0 = float(self._x)
        y0 = float(self._y)
        x1 = x0 + dx * self.px_per_tile
        y1 = y0 + dy * self.px_per_tile

        # Clamp within bounds with a small margin.
        margin = 2.0
        x1 = max(margin, min(float(self.size - 1) - margin, x1))
        y1 = max(margin, min(float(self.size - 1) - margin, y1))

        try:
            cv2.line(
                self._canvas,
                (int(round(x0)), int(round(y0))),
                (int(round(x1)), int(round(y1))),
                (255, 255, 255),
                2,
            )
            cv2.circle(self._canvas, (int(round(x1)), int(round(y1))), 2, (255, 255, 255), -1)
        except Exception:
            pass

        self._x = float(x1)
        self._y = float(y1)

    @property
    def image(self) -> np.ndarray:
        return self._canvas


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    except Exception:
        # Best-effort cleanup
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


class DebugPanelExporter:
    def __init__(self, cfg: DebugPanelConfig) -> None:
        self.cfg = cfg
        self._last_ts = 0.0
        self._trace = MotionTrace(size=320, px_per_tile=14.0)

    def maybe_export(
        self,
        *,
        status_line: str,
        coords_line: str,
        response: float | None,
        dx_tiles: float | None,
        dy_tiles: float | None,
        acc_dx: float | None,
        acc_dy: float | None,
        now: float | None = None,
    ) -> None:
        if not self.cfg.enabled:
            return

        t = time.time() if now is None else float(now)
        interval_s = 1.0 / max(0.1, float(self.cfg.fps))
        if (t - float(self._last_ts)) < float(interval_s):
            return
        self._last_ts = float(t)

        # Update persistent trace.
        try:
            self._trace.update(dx_tiles=dx_tiles, dy_tiles=dy_tiles)
        except Exception:
            pass

        panel = build_debug_panel(
            status_line=str(status_line or ""),
            coords_line=str(coords_line or ""),
            motion_img=self._trace.image,
            metrics={
                "response": response,
                "dx_tiles": dx_tiles,
                "dy_tiles": dy_tiles,
                "acc_dx": acc_dx,
                "acc_dy": acc_dy,
            },
            size=(int(self.cfg.width), int(self.cfg.height)),
        )

        # Encode PNG in-memory then atomic replace.
        try:
            import cv2

            ok, buf = cv2.imencode(".png", panel)
            if not ok:
                return
            data = bytes(buf)
        except Exception:
            return

        try:
            _atomic_write_bytes(Path(self.cfg.out_file), data)
        except Exception:
            pass
