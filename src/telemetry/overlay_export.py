from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


@dataclass
class OverlayConfig:
    enabled: bool
    interval_s: float
    out_dir: str
    draw_tile_grid: bool
    tile_px: int


def overlay_config_from_env() -> OverlayConfig:
    enabled = os.getenv("OVERLAY_ENABLED", "").strip().lower() in {"1", "true", "yes"}
    try:
        interval_s = float(os.getenv("OVERLAY_INTERVAL_S", "1.0"))
    except Exception:
        interval_s = 1.0
    out_dir = os.getenv("OVERLAY_OUT_DIR", "logs/debug_overlay").strip() or "logs/debug_overlay"
    draw_tile_grid = os.getenv("OVERLAY_TILE_GRID", "1").strip().lower() not in {"0", "false", "no"}
    tile_raw = os.getenv("OVERLAY_TILE_PX", "").strip() or os.getenv("TIBIA_TILE_PX", "32").strip() or "32"
    try:
        tile_px = int(float(tile_raw))
    except Exception:
        tile_px = 32
    tile_px = max(4, min(128, tile_px))
    return OverlayConfig(
        enabled=enabled,
        interval_s=max(0.1, interval_s),
        out_dir=out_dir,
        draw_tile_grid=draw_tile_grid,
        tile_px=tile_px,
    )


class OverlayExporter:
    def __init__(self, cfg: OverlayConfig) -> None:
        self.cfg = cfg
        self._last_ts = 0.0

    def maybe_export(
        self,
        frame_bgr: np.ndarray,
        *,
        viewport_rect: Optional[Tuple[int, int, int, int]] = None,
        boxes: Optional[Sequence[Dict[str, Any]]] = None,
        blocked_offsets: Optional[Sequence[Tuple[int, int]]] = None,
        target_label: str = "",
        info_lines: Optional[Sequence[str]] = None,
        battlelist_lines: Optional[Sequence[str]] = None,
        roi_rects: Optional[Sequence[Tuple[str, Tuple[int, int, int, int]]]] = None,
    ) -> None:
        if not self.cfg.enabled:
            return
        now = time.time()
        if now - self._last_ts < float(self.cfg.interval_s):
            return
        self._last_ts = now

        try:
            import cv2
        except Exception:
            return

        img = frame_bgr.copy()

        def draw_label(text: str, *, x: int, y: int, fg=(255, 255, 255), bg=(0, 0, 0)) -> None:
            try:
                font = cv2.FONT_HERSHEY_SIMPLEX
                scale = 0.55
                thickness = 2
                (tw, th), _ = cv2.getTextSize(str(text), font, scale, thickness)
                pad = 4
                cv2.rectangle(img, (x - pad, y - th - pad), (x + tw + pad, y + pad), bg, -1)
                cv2.putText(img, str(text), (x, y), font, scale, fg, thickness)
            except Exception:
                pass

        if viewport_rect is not None:
            x, y, w, h = viewport_rect
            try:
                cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 255), 2)
                cv2.putText(img, "viewport", (x + 4, y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            except Exception:
                pass

            # Optional tile grid overlay (helps debug viewport_tile_offsets mapping).
            if self.cfg.draw_tile_grid and self.cfg.tile_px > 0:
                try:
                    tile = int(self.cfg.tile_px)
                    x0, y0 = int(x), int(y)
                    x1, y1 = int(x + w), int(y + h)
                    center_x = x0 + int(w // 2)
                    center_y = y0 + int(h // 2)

                    # Center crosshair.
                    cv2.drawMarker(
                        img,  # type: ignore[arg-type]
                        (center_x, center_y),
                        (255, 255, 0),
                        markerType=cv2.MARKER_CROSS,
                        markerSize=16,
                        thickness=2,
                    )

                    # Limit lines in case of tiny tile sizes.
                    max_lines = 240
                    n_lines = 0

                    # Vertical lines, anchored at center.
                    xx = center_x
                    while xx >= x0 and n_lines < max_lines:
                        cv2.line(img, (xx, y0), (xx, y1), (80, 80, 80), 1)
                        xx -= tile
                        n_lines += 1
                    xx = center_x + tile
                    while xx <= x1 and n_lines < max_lines:
                        cv2.line(img, (xx, y0), (xx, y1), (80, 80, 80), 1)
                        xx += tile
                        n_lines += 1

                    # Horizontal lines, anchored at center.
                    yy = center_y
                    while yy >= y0 and n_lines < max_lines:
                        cv2.line(img, (x0, yy), (x1, yy), (80, 80, 80), 1)
                        yy -= tile
                        n_lines += 1
                    yy = center_y + tile
                    while yy <= y1 and n_lines < max_lines:
                        cv2.line(img, (x0, yy), (x1, yy), (80, 80, 80), 1)
                        yy += tile
                        n_lines += 1
                except Exception:
                    pass

        # Draw Roboflow boxes
        if boxes:
            for b in boxes:
                if not isinstance(b, dict):
                    continue
                try:
                    cx = float(b.get("x", 0.0))
                    cy = float(b.get("y", 0.0))
                    bw = float(b.get("width", 0.0))
                    bh = float(b.get("height", 0.0))
                    cls = str(b.get("class", ""))
                    conf = float(b.get("confidence", 0.0) or 0.0)
                except Exception:
                    continue

                x0 = int(round(cx - bw / 2))
                y0 = int(round(cy - bh / 2))
                x1 = int(round(cx + bw / 2))
                y1 = int(round(cy + bh / 2))
                try:
                    cv2.rectangle(img, (x0, y0), (x1, y1), (0, 200, 0), 2)
                    cv2.putText(
                        img,
                        f"{cls} {conf:.2f}",
                        (x0, max(0, y0 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (0, 200, 0),
                        2,
                    )
                except Exception:
                    pass

        # Draw ROI rectangles (optional, provided by caller).
        if roi_rects:
            for name, rect in roi_rects:
                try:
                    x, y, w, h = rect
                    x0 = int(x)
                    y0 = int(y)
                    x1 = int(x + w)
                    y1 = int(y + h)
                    cv2.rectangle(img, (x0, y0), (x1, y1), (255, 0, 255), 2)
                    cv2.putText(
                        img,
                        str(name),
                        (x0 + 3, max(0, y0 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.55,
                        (255, 0, 255),
                        2,
                    )
                except Exception:
                    continue

        # Stacked info lines (keeps overlay readable and avoids overlap).
        try:
            lines: List[str] = []
            if info_lines:
                for s in info_lines:
                    try:
                        ss = str(s)
                        if ss:
                            lines.append(ss)
                    except Exception:
                        continue

            if target_label:
                lines.append(f"target: {target_label}")

            if blocked_offsets:
                sample = list(blocked_offsets)[:12]
                lines.append(f"blocked={len(blocked_offsets)} sample={sample}")

            if viewport_rect is not None and self.cfg.tile_px > 0:
                x, y, w, h = viewport_rect
                lines.append(f"tile_px={int(self.cfg.tile_px)} viewport=({int(x)},{int(y)},{int(w)},{int(h)})")

            yy = 22
            for s in lines[:12]:
                draw_label(s, x=10, y=yy)
                yy += 22

            # Battlelist block (first 5 rows). Rendered below the main info stack.
            if battlelist_lines:
                bl: List[str] = []
                for s in battlelist_lines:
                    try:
                        ss = str(s)
                        if ss:
                            bl.append(ss)
                    except Exception:
                        continue
                if bl:
                    yy += 10
                    draw_label("battlelist:", x=10, y=yy, fg=(255, 255, 255), bg=(20, 20, 20))
                    yy += 22
                    for s in bl[:5]:
                        draw_label(s, x=10, y=yy, fg=(255, 255, 255), bg=(20, 20, 20))
                        yy += 22
        except Exception:
            pass

        # Draw blocked offsets in the viewport grid.
        if blocked_offsets:
            # Draw blocked offsets in the viewport grid.
            try:
                if viewport_rect is not None and self.cfg.tile_px > 0:
                    x, y, w, h = viewport_rect
                    x0, y0 = int(x), int(y)
                    x1, y1 = int(x + w), int(y + h)
                    cx = x0 + int(w // 2)
                    cy = y0 + int(h // 2)
                    tile = int(self.cfg.tile_px)
                    for dx, dy in list(blocked_offsets)[:250]:
                        px = int(round(cx + int(dx) * tile))
                        py = int(round(cy + int(dy) * tile))
                        if px < x0 or px > x1 or py < y0 or py > y1:
                            continue
                        cv2.circle(img, (px, py), max(2, tile // 6), (0, 0, 255), -1)
            except Exception:
                pass

        out_dir = Path(self.cfg.out_dir)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return

        p = out_dir / f"{now:.6f}.png"
        try:
            cv2.imwrite(str(p), img)
        except Exception:
            pass
