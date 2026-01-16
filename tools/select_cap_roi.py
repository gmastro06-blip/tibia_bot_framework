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
    return cfg["rois_guess_norm"], cfg["source_resolution"]


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

    # Clamp into source space
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


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Interactively select CAP ROI and write it into the proper rois_guess config.")
    p.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or 2))
    p.add_argument(
        "--force-tk",
        action="store_true",
        help="Use Tkinter selector (no OpenCV GUI). Recommended if OpenCV HighGUI is not available.",
    )
    p.add_argument(
        "--write",
        action="store_true",
        help="If set, writes the selected ROI as cap_ocr into the chosen config file.",
    )
    p.add_argument(
        "--select-on",
        choices=["frame", "skills_panel"],
        default="skills_panel",
        help="Select ROI on full frame or on the skills_panel crop (recommended).",
    )
    return p.parse_args()


def main() -> int:
    _add_src_to_syspath()

    import cv2
    import numpy as np

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

    source_w, source_h = int(source_resolution[0]), int(source_resolution[1])
    frame_w, frame_h = int(frame.shape[1]), int(frame.shape[0])

    if args.select_on == "skills_panel":
        if "skills_panel" not in rois:
            raise SystemExit("Este config no tiene skills_panel; usa --select-on frame")
        x, y, w, h = ocr._roi_to_px(frame, rois, resolution, rois["skills_panel"])  # type: ignore[arg-type]
        crop = frame[y : y + h, x : x + w].copy()

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

        rx, ry, rw, rh = _select_roi("Selecciona CAP (solo el numero)", crop)
        if rw <= 1 or rh <= 1:
            raise SystemExit("ROI vacío/cancelado")
        sel_x = int(x + rx)
        sel_y = int(y + ry)
        sel_w = int(rw)
        sel_h = int(rh)
    else:

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

        sel_x, sel_y, sel_w, sel_h = _select_roi("Selecciona CAP (solo el numero)", frame)
        if sel_w <= 1 or sel_h <= 1:
            raise SystemExit("ROI vacío/cancelado")

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

    print("\n✅ ROI seleccionado")
    print(f"- config: {cfg_path}")
    print(f"- resolution(frame): {frame_w}x{frame_h} source={source_resolution}")
    print(f"- cap_ocr (norm): {json.dumps(roi_norm, ensure_ascii=False)}")

    if args.write:
        # Write into the config file.
        cfg_file = Path(cfg_path)
        with cfg_file.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg.setdefault("rois_guess_norm", {})
        cfg["rois_guess_norm"]["cap_ocr"] = roi_norm
        with cfg_file.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print(f"✍️  Escrito cap_ocr en {cfg_path}")

    return 0


def _write_ppm(path: str, rgb: "np.ndarray") -> None:
    h, w = int(rgb.shape[0]), int(rgb.shape[1])
    header = f"P6\n{w} {h}\n255\n".encode("ascii")
    with open(path, "wb") as f:
        f.write(header)
        f.write(rgb.tobytes())


def _select_roi_tk(title: str, img_bgr: "np.ndarray") -> tuple[int, int, int, int]:
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

    hint = tk.Label(root, text="Arrastra para seleccionar. Enter=OK, Esc=Cancelar")
    hint.pack()

    try:
        root.mainloop()
    finally:
        try:
            root.destroy()
        except Exception:
            pass
        try:
            os.unlink(ppm_path)
        except Exception:
            pass

    if state["cancel"] or state["x0"] is None:
        return 0, 0, 0, 0

    x0v = state.get("x0")
    y0v = state.get("y0")
    x1v = state.get("x1")
    y1v = state.get("y1")
    if not isinstance(x0v, int) or not isinstance(y0v, int):
        return 0, 0, 0, 0
    if not isinstance(x1v, int):
        x1v = x0v
    if not isinstance(y1v, int):
        y1v = y0v

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


if __name__ == "__main__":
    raise SystemExit(main())
