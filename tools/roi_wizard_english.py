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
) -> dict[str, float]:
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

    # Base scale to fit the window; user-controlled zoom multiplies this.
    max_w, max_h = 1200, 800
    base_scale = min(1.0, max_w / max(1, img_w), max_h / max(1, img_h))
    if base_scale <= 0:
        base_scale = 1.0

    # We keep selection in *original* image pixels so zoom changes preserve the rectangle.
    sel_orig: list[int] = []  # [x0, y0, x1, y1] in original pixels

    with tempfile.NamedTemporaryFile(delete=False, suffix=".ppm") as tmp:
        ppm_path = tmp.name

    root = tk.Tk()
    root.title(title)

    zoom_var = tk.DoubleVar(value=1.0)
    img_ref: dict[str, object] = {"photo": None, "image_id": None}
    rect_id: int | None = None

    top = tk.Frame(root)
    top.pack(fill="x")

    tk.Label(top, text="Zoom").pack(side="left", padx=(6, 4))

    def _set_zoom(v: float) -> None:
        try:
            v0 = float(v)
        except Exception:
            v0 = float(zoom_var.get() or 1.0)
        v0 = max(0.5, min(4.0, v0))
        try:
            zoom_var.set(v0)
        except Exception:
            pass

    def _zoom_in() -> None:
        _set_zoom(float(zoom_var.get() or 1.0) * 1.25)
        _render()

    def _zoom_out() -> None:
        _set_zoom(float(zoom_var.get() or 1.0) / 1.25)
        _render()

    tk.Button(top, text="-", width=3, command=_zoom_out).pack(side="left")
    tk.Button(top, text="+", width=3, command=_zoom_in).pack(side="left", padx=(4, 6))

    zoom_scale = tk.Scale(
        top,
        from_=0.5,
        to=4.0,
        resolution=0.1,
        orient="horizontal",
        variable=zoom_var,
        showvalue=True,
        length=280,
        command=lambda _v: _render(),
    )
    zoom_scale.pack(side="left")

    canvas = tk.Canvas(root, width=int(img_w * base_scale), height=int(img_h * base_scale), highlightthickness=0)
    canvas.pack()

    def _eff_scale() -> float:
        try:
            z = float(zoom_var.get() or 1.0)
        except Exception:
            z = 1.0
        s = float(base_scale) * float(z)
        return s if s > 0 else 1.0

    def _render() -> None:
        nonlocal rect_id

        s = _eff_scale()
        disp = img_bgr
        if s != 1.0:
            disp = cv2.resize(
                img_bgr,
                (max(1, int(round(img_w * s))), max(1, int(round(img_h * s)))),
                interpolation=(cv2.INTER_AREA if s < 1.0 else cv2.INTER_NEAREST),
            )
        disp_rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
        try:
            _write_ppm(ppm_path, disp_rgb)
        except Exception:
            pass

        photo = tk.PhotoImage(file=ppm_path)
        img_ref["photo"] = photo

        try:
            canvas.config(width=photo.width(), height=photo.height())
        except Exception:
            pass

        if img_ref.get("image_id") is None:
            img_ref["image_id"] = canvas.create_image(0, 0, anchor=tk.NW, image=photo)
        else:
            try:
                canvas.itemconfig(int(img_ref["image_id"]), image=photo)
            except Exception:
                pass

        # Re-draw rectangle from original coords.
        if len(sel_orig) == 4:
            try:
                dx0 = int(round(sel_orig[0] * s))
                dy0 = int(round(sel_orig[1] * s))
                dx1 = int(round(sel_orig[2] * s))
                dy1 = int(round(sel_orig[3] * s))
                if rect_id is None:
                    rect_id = canvas.create_rectangle(dx0, dy0, dx1, dy1, outline="red", width=2)
                else:
                    canvas.coords(rect_id, dx0, dy0, dx1, dy1)
            except Exception:
                pass

    _render()

    x0_s: int | None = None
    y0_s: int | None = None
    cancel = False

    def _clamp(v: int, lo: int, hi: int) -> int:
        return max(lo, min(hi, v))

    def _sync_sel_from_disp(x0d: int, y0d: int, x1d: int, y1d: int) -> None:
        # Convert display coords to original coords and store.
        s = _eff_scale()
        if s <= 0:
            s = 1.0
        ox0 = int(round(min(x0d, x1d) / s))
        oy0 = int(round(min(y0d, y1d) / s))
        ox1 = int(round(max(x0d, x1d) / s))
        oy1 = int(round(max(y0d, y1d) / s))
        ox0 = max(0, min(img_w - 1, ox0))
        oy0 = max(0, min(img_h - 1, oy0))
        ox1 = max(0, min(img_w - 1, ox1))
        oy1 = max(0, min(img_h - 1, oy1))
        sel_orig[:] = [ox0, oy0, ox1, oy1]

    def on_down(event):
        nonlocal x0_s, y0_s, rect_id
        w = int(canvas.winfo_width() or 0)
        h = int(canvas.winfo_height() or 0)
        x0_s = _clamp(int(event.x), 0, max(0, w - 1))
        y0_s = _clamp(int(event.y), 0, max(0, h - 1))
        _sync_sel_from_disp(int(x0_s), int(y0_s), int(x0_s), int(y0_s))

        if rect_id is not None:
            try:
                canvas.delete(rect_id)
            except Exception:
                pass
            rect_id = None
        rect_id = canvas.create_rectangle(x0_s, y0_s, x0_s, y0_s, outline="red", width=2)

    def on_drag(event):
        nonlocal rect_id
        if x0_s is None or y0_s is None:
            return
        w = int(canvas.winfo_width() or 0)
        h = int(canvas.winfo_height() or 0)
        x1_s = _clamp(int(event.x), 0, max(0, w - 1))
        y1_s = _clamp(int(event.y), 0, max(0, h - 1))
        _sync_sel_from_disp(int(x0_s), int(y0_s), int(x1_s), int(y1_s))
        try:
            if rect_id is not None:
                canvas.coords(rect_id, x0_s, y0_s, x1_s, y1_s)
        except Exception:
            pass

    def on_accept(_event=None):
        root.quit()

    def on_cancel(_event=None):
        nonlocal cancel
        cancel = True
        root.quit()

    def on_wheel(event):
        # Windows: event.delta is multiples of 120.
        try:
            d = int(getattr(event, "delta", 0) or 0)
        except Exception:
            d = 0
        if d == 0:
            return
        if d > 0:
            _set_zoom(float(zoom_var.get() or 1.0) * 1.10)
        else:
            _set_zoom(float(zoom_var.get() or 1.0) / 1.10)
        _render()

    canvas.bind("<Button-1>", on_down)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<MouseWheel>", on_wheel)
    root.bind("<Return>", on_accept)
    root.bind("<Escape>", on_cancel)

    tk.Label(root, text=hint).pack()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        cancel = True
    finally:
        try:
            root.destroy()
        except Exception:
            pass
        try:
            os.unlink(ppm_path)
        except Exception:
            pass

    if bool(cancel) or len(sel_orig) != 4:
        return 0, 0, 0, 0

    ox0, oy0, ox1, oy1 = sel_orig
    return int(ox0), int(oy0), int(max(1, ox1 - ox0)), int(max(1, oy1 - oy0))


ENGLISH_TO_LEGACY: dict[str, str] = {
    # requested names -> current codebase names
    "hpbarup": "hp_top_ocr",
    "mpbarup": "mp_top_ocr",
    "minimap": "minimap_content",
    "gamewindows": "game_viewport",
    "ring": "ring_slot",
    "amulet": "amulet_slot",
    "statusbar": "states_icons",
    "hpdownbar": "hp_low_bar",
    "mpdownbar": "mp_low_bar",
    "cap": "cap_ocr",
    "soul": "soul_ocr",
}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Wizard interactivo para configurar ROIs con nombres en inglés (y compatibilidad con nombres actuales).\n"
            "Arrastra un rectángulo, Enter=OK, Esc=Cancelar."
        )
    )
    p.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or 2))
    p.add_argument(
        "--rois",
        type=str,
        default="",
        help="Path a ROIs JSON (si omitido, usa ROIS_CONFIG o auto por resolución)",
    )
    p.add_argument("--write", action="store_true", help="Escribe los ROIs en el JSON")
    return p.parse_args()


def main() -> int:
    _add_src_to_syspath()

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

    cfg_file = Path(cfg_path)
    root = json.loads(cfg_file.read_text(encoding="utf-8"))
    if not isinstance(root, dict):
        raise SystemExit("ROIs JSON inválido (root no es dict)")

    rois = root.get("rois_guess_norm")
    if rois is None:
        rois = root.get("rois")
    if rois is None:
        rois = {}
    if not isinstance(rois, dict):
        raise SystemExit("ROIs JSON inválido (rois_guess_norm no es dict)")

    source_resolution = root.get("source_resolution")
    if isinstance(source_resolution, (list, tuple)) and len(source_resolution) == 2:
        source_w, source_h = int(source_resolution[0] or 0), int(source_resolution[1] or 0)
    else:
        source_w, source_h = int(resolution[0]), int(resolution[1])
        root["source_resolution"] = [source_w, source_h]

    frame_w, frame_h = int(frame.shape[1]), int(frame.shape[0])

    # Keep a stable order.
    steps: list[tuple[str, str]] = [
        ("gamewindows", "Selecciona el área del JUEGO (viewport / game window)."),
        ("minimap", "Selecciona el área del MINIMAPA (contenido del minimap)."),
        ("hpbarup", "Selecciona la zona de HP ARRIBA (texto/barra superior)."),
        ("mpbarup", "Selecciona la zona de MP ARRIBA (texto/barra superior)."),
        ("hpdownbar", "Selecciona la barra de HP ABAJO (HP low bar)."),
        ("mpdownbar", "Selecciona la barra de MP ABAJO (MP low bar)."),
        ("statusbar", "Selecciona la zona de ICONOS/ESTADOS (paralyze/haste/utamo/hungry)."),
        ("ring", "Selecciona el SLOT de ANILLO (ring)."),
        ("amulet", "Selecciona el SLOT de AMULETO (amulet)."),
        ("cap", "Selecciona el texto de CAPACIDAD (cap)."),
        ("soul", "Selecciona el texto de SOUL (soul)."),
    ]

    picked_any = False
    for en_name, desc in steps:
        legacy = ENGLISH_TO_LEGACY.get(en_name)
        title = f"ROI Wizard: {en_name}" + (f"  (legacy: {legacy})" if legacy else "")
        hint = f"{desc}\n\nROI: {en_name}  (Enter=OK, Esc=Cancelar)"
        x, y, w, h = _select_roi_tk(title, frame, hint=hint)
        if w <= 1 or h <= 1:
            raise SystemExit(f"Selección cancelada/vacía en {en_name}")

        roi_norm = _frame_px_to_source_norm(
            x=int(x),
            y=int(y),
            w=int(w),
            h=int(h),
            frame_w=frame_w,
            frame_h=frame_h,
            source_w=source_w,
            source_h=source_h,
        )
        rois[str(en_name)] = roi_norm
        if legacy:
            rois[str(legacy)] = roi_norm
        picked_any = True

    if not picked_any:
        raise SystemExit("No se seleccionó ningún ROI")

    # Write back.
    root.setdefault("rois_guess_norm", {})
    if not isinstance(root.get("rois_guess_norm"), dict):
        root["rois_guess_norm"] = {}
    root["rois_guess_norm"].update(rois)

    if args.write:
        cfg_file.write_text(json.dumps(root, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"OK: ROIs escritos en {cfg_path}")
    else:
        print(json.dumps({"rois_guess_norm": rois}, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
