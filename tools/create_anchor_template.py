from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np


def _add_src_to_syspath() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def _pick_config_file(resolution: tuple[int, int]) -> str:
    width, height = resolution
    config_files = {
        (2048, 1076): "configs/rois_guess.json",
        (1920, 1080): "configs/rois_guess_1920x1080.json",
        (1920, 1009): "configs/rois_guess_1920x1080.json",
    }
    return config_files.get((width, height), "configs/rois_guess_1920x1080.json")


def _load_config(path: str) -> tuple[dict, list[int]]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg.get("rois_guess_norm", {}), cfg.get("source_resolution", [0, 0])


def _save_config(path: str, *, rois_guess_norm: dict, source_resolution: list[int]) -> None:
    payload = {"source_resolution": source_resolution, "rois_guess_norm": rois_guess_norm}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _compute_letterbox(frame_w: int, frame_h: int, source_w: int, source_h: int) -> tuple[float, float, float]:
    scale = min(frame_w / source_w, frame_h / source_h) if source_w and source_h else 1.0
    content_w = source_w * scale
    content_h = source_h * scale
    offset_x = (frame_w - content_w) / 2.0
    offset_y = (frame_h - content_h) / 2.0
    return scale, offset_x, offset_y


def _frame_px_to_source_norm(
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    frame_w: int,
    frame_h: int,
    source_w: int,
    source_h: int,
) -> dict:
    scale, offset_x, offset_y = _compute_letterbox(frame_w, frame_h, source_w, source_h)
    if scale <= 0:
        scale = 1.0

    sx = (float(x) - offset_x) / scale
    sy = (float(y) - offset_y) / scale
    sw = float(w) / scale
    sh = float(h) / scale

    sx = max(0.0, min(float(source_w - 1), sx))
    sy = max(0.0, min(float(source_h - 1), sy))
    sw = max(1.0, min(float(source_w) - sx, sw))
    sh = max(1.0, min(float(source_h) - sy, sh))

    return {
        "x": float(sx) / float(source_w),
        "y": float(sy) / float(source_h),
        "w": float(sw) / float(source_w),
        "h": float(sh) / float(source_h),
    }


def _write_ppm(path: str, rgb: "np.ndarray") -> None:
    h, w = int(rgb.shape[0]), int(rgb.shape[1])
    header = f"P6\n{w} {h}\n255\n".encode("ascii")
    with open(path, "wb") as f:
        f.write(header)
        f.write(rgb.tobytes())


def _select_roi_tk(title: str, img_bgr: "np.ndarray") -> tuple[int, int, int, int]:
    """Tkinter ROI selector (drag rectangle, Enter=accept, Esc=cancel)."""
    import tkinter as tk

    import cv2

    if img_bgr is None or getattr(img_bgr, "size", 0) == 0:
        return 0, 0, 0, 0

    img_h, img_w = int(img_bgr.shape[0]), int(img_bgr.shape[1])

    max_w, max_h = 1200, 800
    scale = min(1.0, max_w / max(1, img_w), max_h / max(1, img_h))
    disp = img_bgr
    if scale < 1.0:
        disp = cv2.resize(img_bgr, (int(img_w * scale), int(img_h * scale)), interpolation=cv2.INTER_AREA)

    disp_rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ppm") as tmp:
        ppm_path = tmp.name
    _write_ppm(ppm_path, disp_rgb)

    root = tk.Tk()
    root.title(title)

    img = tk.PhotoImage(file=ppm_path)
    canvas = tk.Canvas(root, width=img.width(), height=img.height(), highlightthickness=0)
    canvas.pack()
    canvas.create_image(0, 0, anchor=tk.NW, image=img)

    state = {"x0": None, "y0": None, "x1": None, "y1": None, "rect": None, "cancel": False}

    def _clamp(v: int, lo: int, hi: int) -> int:
        return max(lo, min(hi, v))

    def on_down(event):
        state["x0"] = _clamp(int(event.x), 0, img.width() - 1)
        state["y0"] = _clamp(int(event.y), 0, img.height() - 1)
        state["x1"] = state["x0"]
        state["y1"] = state["y0"]
        if state["rect"] is not None:
            try:
                canvas.delete(state["rect"])
            except Exception:
                pass
        state["rect"] = canvas.create_rectangle(state["x0"], state["y0"], state["x1"], state["y1"], outline="red", width=2)

    def on_drag(event):
        if state["x0"] is None:
            return
        state["x1"] = _clamp(int(event.x), 0, img.width() - 1)
        state["y1"] = _clamp(int(event.y), 0, img.height() - 1)
        canvas.coords(state["rect"], state["x0"], state["y0"], state["x1"], state["y1"])

    def on_accept(_event=None):
        root.quit()

    def on_cancel(_event=None):
        state["cancel"] = True
        root.quit()

    canvas.bind("<Button-1>", on_down)
    canvas.bind("<B1-Motion>", on_drag)
    root.bind("<Return>", on_accept)
    root.bind("<Escape>", on_cancel)

    hint = tk.Label(root, text="Arrastra para seleccionar el ANCHOR. Enter=OK, Esc=Cancelar")
    hint.pack()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        state["cancel"] = True
    finally:
        try:
            root.destroy()
        except Exception:
            pass
        try:
            os.unlink(ppm_path)
        except Exception:
            pass

    if state["cancel"] or state["x0"] is None or state["y0"] is None:
        return 0, 0, 0, 0

    x0v = int(state.get("x0") or 0)
    y0v = int(state.get("y0") or 0)
    x1v = int(state.get("x1") or x0v)
    y1v = int(state.get("y1") or y0v)

    x0 = int(min(x0v, x1v))
    y0 = int(min(y0v, y1v))
    x1 = int(max(x0v, x1v))
    y1 = int(max(y0v, y1v))

    if scale <= 0:
        scale = 1.0
    ox0 = int(round(x0 / scale))
    oy0 = int(round(y0 / scale))
    ox1 = int(round(x1 / scale))
    oy1 = int(round(y1 / scale))

    ox0 = max(0, min(img_w - 1, ox0))
    oy0 = max(0, min(img_h - 1, oy0))
    ox1 = max(0, min(img_w - 1, ox1))
    oy1 = max(0, min(img_h - 1, oy1))

    return int(ox0), int(oy0), int(max(1, ox1 - ox0)), int(max(1, oy1 - oy0))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create an anchor template for auto-shifting ROIs when HUD moves.")
    p.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or 2))
    p.add_argument(
        "--base",
        choices=["frame", "right_hud_panel", "skills_panel", "equipment_slots", "states_icons"],
        default="right_hud_panel",
        help="Select the anchor inside this base ROI.",
    )
    p.add_argument("--out", type=str, default="data/anchors/hud_anchor.png", help="Where to save the anchor template PNG")
    p.add_argument("--search-radius", type=int, default=220, help="Search radius in frame pixels around expected position")
    p.add_argument("--min-score", type=float, default=0.55, help="Minimum template match score to accept")
    p.add_argument("--interval", type=float, default=0.5, help="Update interval (seconds)")
    p.add_argument("--smoothing", type=float, default=0.35, help="EMA smoothing for offset")
    p.add_argument("--write", action="store_true", help="Write anchor config into the rois_guess config JSON")
    p.add_argument("--force-tk", action="store_true", help="Force Tkinter selector (no OpenCV GUI)")
    return p.parse_args()


def main() -> int:
    _add_src_to_syspath()

    import cv2

    from capture.dxgi_capture import DXGICapture
    from vision.ocr import OCRProcessor

    args = _parse_args()

    cap = DXGICapture(force_monitor=args.monitor)
    ocr = OCRProcessor()

    frame = None
    for _ in range(60):
        frame = cap.capture()
        if frame is not None:
            break
    if frame is None:
        raise SystemExit("No se pudo capturar ningún frame")

    resolution = (int(frame.shape[1]), int(frame.shape[0]))
    cfg_path = _pick_config_file(resolution)
    rois, source_resolution = _load_config(cfg_path)
    rois = dict(rois)
    rois["_source_resolution"] = source_resolution

    if args.base == "frame":
        base_img = frame
        base_offset = (0, 0)
    else:
        if args.base not in rois:
            raise SystemExit(f"El config no tiene ROI base '{args.base}'.")
        x, y, w, h = ocr._roi_to_px(frame, rois, resolution, rois[args.base])  # type: ignore[arg-type]
        base_img = frame[y : y + h, x : x + w].copy()
        base_offset = (int(x), int(y))

    def _select_roi(title: str, img_bgr: np.ndarray) -> tuple[int, int, int, int]:
        if bool(args.force_tk):
            return _select_roi_tk(title, img_bgr)
        try:
            r = cv2.selectROI(title, img_bgr, showCrosshair=True, fromCenter=False)  # type: ignore[arg-type]
            cv2.destroyAllWindows()
            rx, ry, rw, rh = [int(v) for v in r]
            if rw <= 1 or rh <= 1:
                return _select_roi_tk(title, img_bgr)
            return rx, ry, rw, rh
        except Exception:
            return _select_roi_tk(title, img_bgr)

    rx, ry, rw, rh = _select_roi("Selecciona ANCHOR (algo bien estable)", base_img)
    if rw <= 1 or rh <= 1:
        raise SystemExit("ROI vacío/cancelado")

    sel_x = int(base_offset[0] + rx)
    sel_y = int(base_offset[1] + ry)
    sel_w = int(rw)
    sel_h = int(rh)

    source_w, source_h = int(source_resolution[0]), int(source_resolution[1])
    frame_w, frame_h = int(frame.shape[1]), int(frame.shape[0])

    roi_norm = _frame_px_to_source_norm(
        x=sel_x,
        y=sel_y,
        w=sel_w,
        h=sel_h,
        frame_w=frame_w,
        frame_h=frame_h,
        source_w=source_w,
        source_h=source_h,
    )

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = Path(__file__).resolve().parent.parent / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    crop = frame[sel_y : sel_y + sel_h, sel_x : sel_x + sel_w].copy()
    cv2.imwrite(str(out_path), crop)

    anchor_cfg = {
        "roi_norm": roi_norm,
        "template_path": str(Path(args.out).as_posix()),
        "search_radius_px": int(args.search_radius),
        "min_score": float(args.min_score),
        "update_interval_s": float(args.interval),
        "smoothing": float(args.smoothing),
        "canny_low": 60,
        "canny_high": 140,
        "max_shift_src_px": 800.0,
    }

    print("\n✅ Anchor creado")
    print(f"- config: {cfg_path}")
    print(f"- base: {args.base}")
    print(f"- template: {Path(args.out).as_posix()}")
    print(f"- roi_norm: {json.dumps(roi_norm, ensure_ascii=False)}")

    if args.write:
        cfg_file = Path(cfg_path)
        with cfg_file.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg.setdefault("rois_guess_norm", {})
        cfg["rois_guess_norm"]["_anchor"] = anchor_cfg
        with cfg_file.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print(f"✍️  Escrito _anchor en {cfg_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
