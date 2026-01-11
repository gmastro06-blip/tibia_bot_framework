from __future__ import annotations

import cv2
from typing import Any, Dict, Mapping

class DebugOverlay:
    """Overlay de debug para visualizar ROIs.

    Soporta:
    - ROIs en píxeles: (x, y, w, h)
    - ROIs normalizadas: {x,y,w,h} (0..1) con opcional `_source_resolution` y `_roi_offset_px`

    Nota: no importa OCRProcessor para evitar cargar EasyOCR.
    """

    @staticmethod
    def _roi_to_px(
        frame: Any,
        rois: Mapping[str, Any],
        roi_def: Mapping[str, Any],
        *,
        resolution: tuple[int, int] | None = None,
    ) -> tuple[int, int, int, int]:
        frame_w, frame_h = int(frame.shape[1]), int(frame.shape[0])
        if resolution is None:
            resolution = (frame_w, frame_h)

        source_resolution = rois.get("_source_resolution") if hasattr(rois, "get") else None
        if (
            isinstance(source_resolution, (list, tuple))
            and len(source_resolution) == 2
            and source_resolution[0]
            and source_resolution[1]
        ):
            source_w, source_h = int(source_resolution[0]), int(source_resolution[1])
        else:
            source_w, source_h = int(resolution[0]), int(resolution[1])

        scale = min(frame_w / source_w, frame_h / source_h) if source_w and source_h else 1.0
        content_w = source_w * scale
        content_h = source_h * scale
        offset_x = (frame_w - content_w) / 2.0
        offset_y = (frame_h - content_h) / 2.0

        unit = str(roi_def.get("unit", "") if hasattr(roi_def, "get") else "").lower()
        x_val = roi_def.get("x") if hasattr(roi_def, "get") else None
        y_val = roi_def.get("y") if hasattr(roi_def, "get") else None
        w_val = roi_def.get("w") if hasattr(roi_def, "get") else None
        h_val = roi_def.get("h") if hasattr(roi_def, "get") else None

        def _f(v: Any, default: float) -> float:
            try:
                if v is None:
                    return default
                return float(v)
            except Exception:
                return default

        def _is_normalized(v: Any) -> bool:
            try:
                vf = float(v)
            except Exception:
                return False
            return 0.0 <= vf <= 1.0

        is_norm = (
            unit != "px"
            and _is_normalized(x_val)
            and _is_normalized(y_val)
            and _is_normalized(w_val)
            and _is_normalized(h_val)
        )

        if is_norm:
            x_src = _f(x_val, 0.0) * source_w
            y_src = _f(y_val, 0.0) * source_h
            w_src = _f(w_val, 0.0) * source_w
            h_src = _f(h_val, 0.0) * source_h
        else:
            x_src = _f(x_val, 0.0)
            y_src = _f(y_val, 0.0)
            w_src = _f(w_val, 0.0)
            h_src = _f(h_val, 0.0)

        # Offset global (en px del source), aplicado a todas las ROIs.
        try:
            off = rois.get("_roi_offset_px") if hasattr(rois, "get") else None
            if isinstance(off, (list, tuple)) and len(off) == 2:
                x_src += float(off[0] or 0.0)
                y_src += float(off[1] or 0.0)
        except Exception:
            pass

        x = int(round(offset_x + x_src * scale))
        y = int(round(offset_y + y_src * scale))
        w = int(round(w_src * scale))
        h = int(round(h_src * scale))

        x = max(0, min(x, frame_w - 1))
        y = max(0, min(y, frame_h - 1))
        w = max(1, min(w, frame_w - x))
        h = max(1, min(h, frame_h - y))
        return x, y, w, h

    def draw(self, frame: cv2.Mat, rois: Dict, target: str, *, resolution: tuple[int, int] | None = None) -> None:
        for name, roi in (rois or {}).items():
            try:
                if not name or str(name).startswith("_"):
                    continue

                rect: tuple[int, int, int, int] | None = None
                if isinstance(roi, (list, tuple)) and len(roi) >= 4:
                    rect = (int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3]))
                elif isinstance(roi, dict) and all(k in roi for k in ("x", "y", "w", "h")):
                    rect = self._roi_to_px(frame, rois, roi, resolution=resolution)

                if rect is None:
                    continue

                x, y, w, h = rect
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                # Etiqueta breve (opcional, barato)
                cv2.putText(
                    frame,
                    str(name),
                    (x + 2, max(0, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 0),
                    1,
                )
            except Exception:
                continue

        cv2.putText(frame, f"Target: {target}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 2)
        # cv2.imshow('Debug', frame)
        # cv2.waitKey(1)