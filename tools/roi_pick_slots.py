from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import cv2


@dataclass
class DragState:
    dragging: bool = False
    x0: int = 0
    y0: int = 0
    x1: int = 0
    y1: int = 0

    def rect(self) -> tuple[int, int, int, int] | None:
        if not self.dragging and (self.x0 == self.x1 and self.y0 == self.y1):
            return None
        x0 = min(self.x0, self.x1)
        y0 = min(self.y0, self.y1)
        x1 = max(self.x0, self.x1)
        y1 = max(self.y0, self.y1)
        w = max(1, x1 - x0)
        h = max(1, y1 - y0)
        return x0, y0, w, h


def _load_rois(path: str, *, fallback_resolution: tuple[int, int]) -> tuple[dict[str, Any], list[int], dict[str, Any]]:
    """Load ROI config and return (rois_dict, source_resolution, raw_json_root)."""

    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"ROI config not found: {path}")

    raw_root = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw_root, dict):
        raise ValueError(f"Invalid ROI JSON root type in {path}: {type(raw_root)}")

    rois = raw_root.get("rois_guess_norm")
    if rois is None:
        rois = raw_root.get("rois")
    if rois is None:
        rois = raw_root

    if not isinstance(rois, dict):
        raise ValueError(f"Invalid ROI format in {path}")

    src = raw_root.get("source_resolution")
    if isinstance(src, (list, tuple)) and len(src) == 2:
        source_resolution = [int(src[0]), int(src[1])]
    else:
        source_resolution = [int(fallback_resolution[0]), int(fallback_resolution[1])]

    out = dict(rois)
    out["_source_resolution"] = source_resolution
    return out, source_resolution, raw_root


def _content_transform(
    *, frame_w: int, frame_h: int, source_w: int, source_h: int
) -> tuple[float, float, float]:
    """Return (scale, offset_x, offset_y) matching OCRProcessor._roi_to_px."""

    if source_w <= 0 or source_h <= 0:
        return 1.0, 0.0, 0.0

    scale = min(frame_w / source_w, frame_h / source_h)
    content_w = source_w * scale
    content_h = source_h * scale
    offset_x = (frame_w - content_w) / 2.0
    offset_y = (frame_h - content_h) / 2.0
    return float(scale), float(offset_x), float(offset_y)


def px_to_norm_roi(
    *,
    rect_px: tuple[int, int, int, int],
    frame_w: int,
    frame_h: int,
    source_w: int,
    source_h: int,
) -> dict[str, float]:
    """Convert frame pixel rect -> normalized ROI in source coordinates."""

    x, y, w, h = rect_px
    scale, off_x, off_y = _content_transform(frame_w=frame_w, frame_h=frame_h, source_w=source_w, source_h=source_h)

    # Invert: src_px = (frame_px - offset) / scale
    x_src = (float(x) - off_x) / max(1e-6, scale)
    y_src = (float(y) - off_y) / max(1e-6, scale)
    w_src = float(w) / max(1e-6, scale)
    h_src = float(h) / max(1e-6, scale)

    # Clamp to source bounds
    x_src = max(0.0, min(x_src, float(source_w - 1)))
    y_src = max(0.0, min(y_src, float(source_h - 1)))
    w_src = max(1.0, min(w_src, float(source_w) - x_src))
    h_src = max(1.0, min(h_src, float(source_h) - y_src))

    return {
        "x": float(x_src) / float(source_w),
        "y": float(y_src) / float(source_h),
        "w": float(w_src) / float(source_w),
        "h": float(h_src) / float(source_h),
    }


def main() -> int:
    # Match repo import style (modules live under src/)
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    sys.path.insert(0, str(src_dir))
    sys.path.insert(0, str(repo_root))

    from capture.dxgi_capture import DXGICapture

    ap = argparse.ArgumentParser(
        description=(
            "Interactive picker for ring_slot + amulet_slot ROIs. "
            "Drag a rectangle, then press 'r' or 'a' to assign. Press 's' to save." 
        )
    )
    ap.add_argument("--rois", default=os.getenv("ROIS_CONFIG", "configs/rois_guess_1920x1080.json"))
    ap.add_argument("--out", default="configs/rois_guess_1920x1080.json", help="Where to write updated ROI JSON")
    ap.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or "2"))
    ap.add_argument("--window", default="roi_pick_slots", help="OpenCV window name")
    args = ap.parse_args()

    cap = DXGICapture(force_monitor=int(args.monitor))
    frame = cap.capture()
    if frame is None:
        print("Failed to capture frame (frame=None)")
        return 2

    frame_h, frame_w = int(frame.shape[0]), int(frame.shape[1])
    resolution = (frame_w, frame_h)

    rois, source_res, raw_root = _load_rois(args.rois, fallback_resolution=resolution)
    source_w, source_h = int(source_res[0]), int(source_res[1])

    # For context, draw equipment_slots if present.
    equip_rect = None
    try:
        equip_def = rois.get("equipment_slots")
        if isinstance(equip_def, dict):
            # Reuse roi_to_px logic from tools/roi_tuner_dump.py (inline minimal)
            scale, off_x, off_y = _content_transform(frame_w=frame_w, frame_h=frame_h, source_w=source_w, source_h=source_h)
            x_src = float(equip_def.get("x", 0.0)) * float(source_w)
            y_src = float(equip_def.get("y", 0.0)) * float(source_h)
            w_src = float(equip_def.get("w", 0.0)) * float(source_w)
            h_src = float(equip_def.get("h", 0.0)) * float(source_h)
            x = int(round(off_x + x_src * scale))
            y = int(round(off_y + y_src * scale))
            w = int(round(w_src * scale))
            h = int(round(h_src * scale))
            equip_rect = (x, y, w, h)
    except Exception:
        equip_rect = None

    state = DragState()
    picked: dict[str, tuple[int, int, int, int]] = {}

    def on_mouse(event, x, y, _flags, _userdata):
        if event == cv2.EVENT_LBUTTONDOWN:
            state.dragging = True
            state.x0 = int(x)
            state.y0 = int(y)
            state.x1 = int(x)
            state.y1 = int(y)
        elif event == cv2.EVENT_MOUSEMOVE and state.dragging:
            state.x1 = int(x)
            state.y1 = int(y)
        elif event == cv2.EVENT_LBUTTONUP:
            state.dragging = False
            state.x1 = int(x)
            state.y1 = int(y)

    cv2.namedWindow(str(args.window), cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(str(args.window), on_mouse)

    print("Controls:")
    print("  drag mouse: select rect")
    print("  r: assign current rect to ring_slot")
    print("  a: assign current rect to amulet_slot")
    print("  s: save updated JSON")
    print("  q / ESC: quit")

    while True:
        vis = frame.copy()

        if equip_rect is not None:
            ex, ey, ew, eh = equip_rect
            cv2.rectangle(vis, (ex, ey), (ex + ew, ey + eh), (0, 255, 255), 2)
            cv2.putText(vis, "equipment_slots", (ex + 2, max(12, ey - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)

        # Draw existing picks
        for name, (rx, ry, rw, rh) in picked.items():
            color = (255, 0, 0) if name == "ring_slot" else (0, 128, 255)
            cv2.rectangle(vis, (rx, ry), (rx + rw, ry + rh), color, 2)
            cv2.putText(vis, name, (rx + 2, max(12, ry - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1)

        # Draw current drag rect
        cur = state.rect()
        if cur is not None:
            x0, y0, w0, h0 = cur
            cv2.rectangle(vis, (x0, y0), (x0 + w0, y0 + h0), (0, 255, 0), 1)

        cv2.imshow(str(args.window), vis)
        k = cv2.waitKey(16) & 0xFF

        if k in (27, ord("q")):
            break

        if k == ord("r"):
            cur = state.rect()
            if cur is not None:
                picked["ring_slot"] = cur
                print("ring_slot px:", cur)
                print("ring_slot norm:", px_to_norm_roi(rect_px=cur, frame_w=frame_w, frame_h=frame_h, source_w=source_w, source_h=source_h))

        if k == ord("a"):
            cur = state.rect()
            if cur is not None:
                picked["amulet_slot"] = cur
                print("amulet_slot px:", cur)
                print("amulet_slot norm:", px_to_norm_roi(rect_px=cur, frame_w=frame_w, frame_h=frame_h, source_w=source_w, source_h=source_h))

        if k == ord("s"):
            # Merge into JSON root under rois_guess_norm if present.
            out_path = Path(str(args.out))

            root = dict(raw_root)
            target_key = "rois_guess_norm" if isinstance(raw_root.get("rois_guess_norm"), dict) else ("rois" if isinstance(raw_root.get("rois"), dict) else None)
            if target_key is None:
                # Root itself is rois mapping
                rois_map: dict[str, Any] = dict(raw_root)
                target = rois_map
                root = rois_map
            else:
                target = dict(root.get(target_key) or {})

            changed_any = False
            for name in ("ring_slot", "amulet_slot"):
                if name not in picked:
                    continue
                norm = px_to_norm_roi(
                    rect_px=picked[name],
                    frame_w=frame_w,
                    frame_h=frame_h,
                    source_w=source_w,
                    source_h=source_h,
                )
                target[name] = norm
                changed_any = True

            if not changed_any:
                print("Nothing to save yet (pick ring_slot and/or amulet_slot first).")
                continue

            if target_key is not None:
                root[target_key] = target

            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(root, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"Saved updated ROI config to: {out_path}")
            except Exception as e:
                print(f"Failed to write {out_path}: {e}")

    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
