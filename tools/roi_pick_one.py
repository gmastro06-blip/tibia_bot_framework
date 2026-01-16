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


def _content_transform(*, frame_w: int, frame_h: int, source_w: int, source_h: int) -> tuple[float, float, float]:
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

    x_src = (float(x) - off_x) / max(1e-6, scale)
    y_src = (float(y) - off_y) / max(1e-6, scale)
    w_src = float(w) / max(1e-6, scale)
    h_src = float(h) / max(1e-6, scale)

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


def _norm_to_px_rect(
    *, roi_def: Mapping[str, Any], frame_w: int, frame_h: int, source_w: int, source_h: int
) -> tuple[int, int, int, int] | None:
    try:
        x = float(roi_def.get("x", 0.0))
        y = float(roi_def.get("y", 0.0))
        w = float(roi_def.get("w", 0.0))
        h = float(roi_def.get("h", 0.0))
    except Exception:
        return None

    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and 0.0 <= w <= 1.0 and 0.0 <= h <= 1.0):
        return None

    scale, off_x, off_y = _content_transform(frame_w=frame_w, frame_h=frame_h, source_w=source_w, source_h=source_h)

    x_src = x * float(source_w)
    y_src = y * float(source_h)
    w_src = w * float(source_w)
    h_src = h * float(source_h)

    fx = int(round(off_x + x_src * scale))
    fy = int(round(off_y + y_src * scale))
    fw = int(round(w_src * scale))
    fh = int(round(h_src * scale))

    fx = max(0, min(fx, frame_w - 1))
    fy = max(0, min(fy, frame_h - 1))
    fw = max(1, min(fw, frame_w - fx))
    fh = max(1, min(fh, frame_h - fy))
    return fx, fy, fw, fh


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    sys.path.insert(0, str(src_dir))
    sys.path.insert(0, str(repo_root))

    from capture.dxgi_capture import DXGICapture

    ap = argparse.ArgumentParser(
        description=(
            "Interactive picker for a single ROI. "
            "Drag a rectangle, press 's' to save it into the ROI config."
        )
    )
    ap.add_argument("--rois", default=os.getenv("ROIS_CONFIG", "configs/rois_guess_1920x1080.json"))
    ap.add_argument("--out", default="", help="Where to write updated ROI JSON (default: overwrite --rois)")
    ap.add_argument("--roi", required=True, help="ROI name to edit, e.g. ring_slot, coords_ocr, chat_panel")
    ap.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or "2"))
    ap.add_argument("--window", default="roi_pick_one", help="OpenCV window name")
    args = ap.parse_args()

    out_path = str(args.out).strip() or str(args.rois).strip()

    cap = DXGICapture(force_monitor=int(args.monitor))
    frame = cap.capture()
    if frame is None:
        print("Failed to capture frame (frame=None)")
        return 2

    frame_h, frame_w = int(frame.shape[0]), int(frame.shape[1])
    resolution = (frame_w, frame_h)

    rois, source_res, raw_root = _load_rois(str(args.rois), fallback_resolution=resolution)
    source_w, source_h = int(source_res[0]), int(source_res[1])

    state = DragState()
    picked: tuple[int, int, int, int] | None = None

    existing = None
    try:
        roi_def = rois.get(str(args.roi))
        if isinstance(roi_def, Mapping):
            existing = _norm_to_px_rect(
                roi_def=roi_def,
                frame_w=frame_w,
                frame_h=frame_h,
                source_w=source_w,
                source_h=source_h,
            )
    except Exception:
        existing = None

    def on_mouse(event, x, y, _flags, _userdata):
        nonlocal picked
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
            picked = state.rect()

    cv2.namedWindow(str(args.window), cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(str(args.window), on_mouse)

    print("Controls:")
    print("  drag mouse: select rect")
    print("  s: save selected rect into ROI config")
    print("  c: clear selection")
    print("  q / ESC: quit")
    print(f"Editing ROI: {args.roi}")

    while True:
        vis = frame.copy()

        if existing is not None:
            ex, ey, ew, eh = existing
            cv2.rectangle(vis, (ex, ey), (ex + ew, ey + eh), (0, 255, 255), 2)
            cv2.putText(
                vis,
                f"existing: {args.roi}",
                (ex + 2, max(14, ey - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 255),
                2,
            )

        if picked is not None:
            px, py, pw, ph = picked
            cv2.rectangle(vis, (px, py), (px + pw, py + ph), (0, 255, 0), 2)
            cv2.putText(
                vis,
                f"new: {args.roi}",
                (px + 2, max(14, py - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )

        cur = state.rect()
        if cur is not None and state.dragging:
            x0, y0, w0, h0 = cur
            cv2.rectangle(vis, (x0, y0), (x0 + w0, y0 + h0), (255, 255, 255), 1)

        cv2.imshow(str(args.window), vis)
        k = cv2.waitKey(16) & 0xFF

        if k in (27, ord("q")):
            break

        if k == ord("c"):
            picked = None

        if k == ord("s"):
            if picked is None:
                print("Nothing selected yet.")
                continue

            norm = px_to_norm_roi(
                rect_px=picked,
                frame_w=frame_w,
                frame_h=frame_h,
                source_w=source_w,
                source_h=source_h,
            )
            print("Selected px:", picked)
            print("Selected norm:", norm)

            root = dict(raw_root)
            target_key = (
                "rois_guess_norm"
                if isinstance(raw_root.get("rois_guess_norm"), dict)
                else ("rois" if isinstance(raw_root.get("rois"), dict) else None)
            )
            if target_key is None:
                target = root
            else:
                target = dict(root.get(target_key) or {})

            target[str(args.roi)] = norm

            if target_key is not None:
                root[target_key] = target

            try:
                p = Path(out_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(root, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"Saved updated ROI config to: {p}")
                existing = picked
            except Exception as e:
                print(f"Failed to write {out_path}: {e}")

    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
