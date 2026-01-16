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

    x0_s: int | None = None
    y0_s: int | None = None
    x1_s: int | None = None
    y1_s: int | None = None
    rect_id: int | None = None
    cancel = False

    def _clamp(v: int, lo: int, hi: int) -> int:
        return max(lo, min(hi, v))

    def on_down(event):
        nonlocal x0_s, y0_s, x1_s, y1_s, rect_id
        x0_s = _clamp(int(event.x), 0, img.width() - 1)
        y0_s = _clamp(int(event.y), 0, img.height() - 1)
        x1_s = x0_s
        y1_s = y0_s
        if rect_id is not None:
            try:
                canvas.delete(rect_id)
            except Exception:
                pass
        rect_id = canvas.create_rectangle(x0_s, y0_s, x1_s, y1_s, outline="red", width=2)

    def on_drag(event):
        nonlocal x0_s, y0_s, x1_s, y1_s, rect_id
        if x0_s is None or y0_s is None:
            return
        x1_s = _clamp(int(event.x), 0, img.width() - 1)
        y1_s = _clamp(int(event.y), 0, img.height() - 1)
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

    canvas.bind("<Button-1>", on_down)
    canvas.bind("<B1-Motion>", on_drag)
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

    if bool(cancel) or x0_s is None or y0_s is None:
        return 0, 0, 0, 0

    x0v = int(x0_s)
    y0v = int(y0_s)
    x1v = int(x1_s if x1_s is not None else x0v)
    y1v = int(y1_s if y1_s is not None else y0v)

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
