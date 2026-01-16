from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

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


def _resolve_rois_path(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        p = Path(s)
        if not p.is_absolute():
            repo_root = Path(__file__).resolve().parent.parent
            p = (repo_root / p).resolve()
        if p.exists() and p.is_file():
            return str(p)
    except Exception:
        return None
    return None


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


def _write_ppm(path: str, rgb: np.ndarray) -> None:
    h, w = int(rgb.shape[0]), int(rgb.shape[1])
    header = f"P6\n{w} {h}\n255\n".encode("ascii")
    with open(path, "wb") as f:
        f.write(header)
        f.write(rgb.tobytes())


def _select_roi_tk(title: str, img_bgr: np.ndarray, *, hint: str) -> tuple[int, int, int, int]:
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

    class _DragState(TypedDict):
        x0: int | None
        y0: int | None
        x1: int | None
        y1: int | None
        rect: int | None
        cancel: bool

    state: _DragState = {"x0": None, "y0": None, "x1": None, "y1": None, "rect": None, "cancel": False}

    def _clamp(v: int, lo: int, hi: int) -> int:
        return max(lo, min(hi, v))

    def on_down(event):
        x0 = _clamp(int(event.x), 0, img.width() - 1)
        y0 = _clamp(int(event.y), 0, img.height() - 1)
        state["x0"] = x0
        state["y0"] = y0
        state["x1"] = x0
        state["y1"] = y0
        if state["rect"] is not None:
            try:
                canvas.delete(state["rect"])
            except Exception:
                pass
        state["rect"] = canvas.create_rectangle(x0, y0, x0, y0, outline="red", width=2)

    def on_drag(event):
        if state["x0"] is None or state["y0"] is None or state["rect"] is None:
            return
        x0 = state["x0"]
        y0 = state["y0"]
        rect = state["rect"]
        x1 = _clamp(int(event.x), 0, img.width() - 1)
        y1 = _clamp(int(event.y), 0, img.height() - 1)
        state["x1"] = x1
        state["y1"] = y1
        canvas.coords(rect, x0, y0, x1, y1)

    def on_accept(_event=None):
        root.quit()

    def on_cancel(_event=None):
        state["cancel"] = True
        root.quit()

    canvas.bind("<Button-1>", on_down)
    canvas.bind("<B1-Motion>", on_drag)
    root.bind("<Return>", on_accept)
    root.bind("<Escape>", on_cancel)

    tk.Label(root, text=hint).pack()

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


@dataclass
class PanelRef:
    side: str  # left|right
    edge: str  # viewport edge derived from match: left uses "right" edge, right uses "left" edge
    roi_norm: dict
    template_path: str
    search_radius_px: int = 560
    min_score: float = 0.55
    canny_low: int = 60
    canny_high: int = 140


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Configura referencias (templates) de panel izquierdo/derecho para auto-ajustar el viewport cuando aparecen/desaparecen."
    )
    p.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or 2))
    p.add_argument("--rois", type=str, default="", help="Path a ROIs JSON (si omitido, usa ROIS_CONFIG o auto por resolución)")
    p.add_argument("--write", action="store_true", help="Escribe _panel_refs dentro del ROIs JSON")
    p.add_argument("--out-dir", type=str, default="data/anchors", help="Carpeta para guardar templates PNG")
    p.add_argument("--min-score", type=float, default=0.55)
    p.add_argument("--search-radius", type=int, default=560)
    return p.parse_args()


def main() -> int:
    _add_src_to_syspath()

    import cv2

    from capture.dxgi_capture import DXGICapture

    args = _parse_args()

    cap = DXGICapture(force_monitor=args.monitor)

    frame = None
    for _ in range(60):
        frame = cap.capture()
        if frame is not None:
            break
    if frame is None:
        raise SystemExit("No se pudo capturar ningún frame")

    resolution = (int(frame.shape[1]), int(frame.shape[0]))

    cfg_path = (
        _resolve_rois_path(args.rois)
        or _resolve_rois_path(os.getenv("ROIS_CONFIG", ""))
        or _pick_config_file(resolution)
    )
    rois_guess_norm, source_resolution = _load_config(cfg_path)

    try:
        source_w, source_h = int(source_resolution[0] or 0), int(source_resolution[1] or 0)
        if source_w <= 0 or source_h <= 0:
            source_w, source_h = int(resolution[0]), int(resolution[1])
    except Exception:
        source_w, source_h = int(resolution[0]), int(resolution[1])

    out_base = Path(args.out_dir)
    if not out_base.is_absolute():
        repo_root = Path(__file__).resolve().parent.parent
        out_base = (repo_root / out_base).resolve()
    try:
        out_base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # Selección manual
    hint_left = (
        "Selecciona un rectángulo que exista SOLO cuando el PANEL IZQUIERDO está abierto.\n"
        "Ideal: incluye el borde del panel y llega hasta el borde del viewport.\n"
        "Enter=OK, Esc=Cancelar"
    )
    lx, ly, lw, lh = _select_roi_tk("Panel izquierdo (referencia)", frame, hint=hint_left)

    hint_right = (
        "Selecciona un rectángulo que exista SOLO cuando el PANEL DERECHO está abierto.\n"
        "Ideal: incluye el borde del panel y arranca en el borde del viewport.\n"
        "Enter=OK, Esc=Cancelar"
    )
    rx, ry, rw, rh = _select_roi_tk("Panel derecho (referencia)", frame, hint=hint_right)

    if lw <= 1 or lh <= 1 or rw <= 1 or rh <= 1:
        raise SystemExit("Selección cancelada o inválida")

    left_crop = frame[ly : ly + lh, lx : lx + lw].copy()
    right_crop = frame[ry : ry + rh, rx : rx + rw].copy()

    left_path = out_base / "panel_left.png"
    right_path = out_base / "panel_right.png"

    try:
        cv2.imwrite(str(left_path), left_crop)
        cv2.imwrite(str(right_path), right_crop)
    except Exception:
        raise SystemExit("No se pudieron guardar los templates PNG")

    left_norm = _frame_px_to_source_norm(
        x=lx,
        y=ly,
        w=lw,
        h=lh,
        frame_w=int(frame.shape[1]),
        frame_h=int(frame.shape[0]),
        source_w=int(source_w),
        source_h=int(source_h),
    )
    right_norm = _frame_px_to_source_norm(
        x=rx,
        y=ry,
        w=rw,
        h=rh,
        frame_w=int(frame.shape[1]),
        frame_h=int(frame.shape[0]),
        source_w=int(source_w),
        source_h=int(source_h),
    )

    panel_refs = {
        "left": {
            "side": "left",
            "edge": "right",
            "roi_norm": left_norm,
            "template_path": str(Path("data") / "anchors" / "panel_left.png"),
            "search_radius_px": int(max(80, int(args.search_radius))),
            "min_score": float(args.min_score),
            "canny_low": 60,
            "canny_high": 140,
        },
        "right": {
            "side": "right",
            "edge": "left",
            "roi_norm": right_norm,
            "template_path": str(Path("data") / "anchors" / "panel_right.png"),
            "search_radius_px": int(max(80, int(args.search_radius))),
            "min_score": float(args.min_score),
            "canny_low": 60,
            "canny_high": 140,
        },
    }

    print(f"Templates guardados en: {left_path} y {right_path}")
    print(f"ROIs config: {cfg_path}")

    if args.write:
        rois_guess_norm = dict(rois_guess_norm)
        rois_guess_norm["_panel_refs"] = panel_refs
        _save_config(cfg_path, rois_guess_norm=rois_guess_norm, source_resolution=source_resolution)
        print("OK: _panel_refs escrito en el config")
    else:
        # Print JSON for copy/paste/debug
        print(json.dumps({"_panel_refs": panel_refs}, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
