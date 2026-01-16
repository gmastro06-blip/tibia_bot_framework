from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict


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


def _write_ppm(path: str, rgb) -> None:
    h, w = int(rgb.shape[0]), int(rgb.shape[1])
    header = f"P6\n{w} {h}\n255\n".encode("ascii")
    with open(path, "wb") as f:
        f.write(header)
        f.write(rgb.tobytes())


def _select_roi_tk(title: str, img_bgr):
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

    hint = tk.Label(root, text="Arrastra para seleccionar. Enter=OK, Esc=Cancelar")
    hint.pack()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        # Treat Ctrl+C as cancel for this step (avoid crashing the whole wizard).
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

    x0v = state.get("x0")
    y0v = state.get("y0")
    x1v = state.get("x1")
    y1v = state.get("y1")
    if x0v is None or y0v is None:
        return 0, 0, 0, 0
    if x1v is None:
        x1v = x0v
    if y1v is None:
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


def _roi_to_px(*, frame, rois: dict, resolution: tuple[int, int], roi_def: dict) -> tuple[int, int, int, int]:
    """Convierte ROI (norm o px-in-source) a píxeles del frame (mismo modelo que OCRProcessor._roi_to_px)."""
    frame_w, frame_h = int(frame.shape[1]), int(frame.shape[0])
    source_resolution = rois.get("_source_resolution")
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

    unit = str(roi_def.get("unit", "")).lower()
    x_val = roi_def.get("x")
    y_val = roi_def.get("y")
    w_val = roi_def.get("w")
    h_val = roi_def.get("h")

    def _f(v: Any, default: float) -> float:
        try:
            if v is None:
                return default
            return float(v)
        except Exception:
            return default

    def _is_norm(v: Any) -> bool:
        try:
            vf = float(v)
        except Exception:
            return False
        return 0.0 <= vf <= 1.0

    is_norm = unit != "px" and _is_norm(x_val) and _is_norm(y_val) and _is_norm(w_val) and _is_norm(h_val)
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

    x = int(round(offset_x + x_src * scale))
    y = int(round(offset_y + y_src * scale))
    w = int(round(w_src * scale))
    h = int(round(h_src * scale))
    x = max(0, min(x, frame_w - 1))
    y = max(0, min(y, frame_h - 1))
    w = max(1, min(w, frame_w - x))
    h = max(1, min(h, frame_h - y))
    return x, y, w, h


def _draw_preview(frame_bgr, rois: dict, resolution: tuple[int, int], out_file: Path) -> None:
    import cv2

    img = frame_bgr.copy()
    try:
        if "_source_resolution" not in rois:
            rois["_source_resolution"] = [resolution[0], resolution[1]]
    except Exception:
        pass

    for name, roi_def in list(rois.items()):
        if str(name).startswith("_"):
            continue
        if not isinstance(roi_def, dict):
            continue
        try:
            x, y, w, h = _roi_to_px(frame=img, rois=rois, resolution=resolution, roi_def=roi_def)
            cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(img, str(name), (x, max(12, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        except Exception:
            continue

    out_file.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_file), img)


@dataclass
class Step:
    name: str
    base: str
    help: str = ""


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Wizard: calibrar ROIs base + sub-ROIs con selector Tkinter (blindado).")
    p.add_argument("--monitor", type=int, default=int(os.getenv("FORCE_MONITOR", "2") or 2))
    p.add_argument(
        "--out",
        type=str,
        default="",
        help=(
            "Optional output profile JSON path (recommended). If set, writes a standalone ROI profile compatible with "
            "src/main.load_roi_config() via ROIS_CONFIG."
        ),
    )
    p.add_argument(
        "--template",
        type=str,
        default="",
        help=(
            "Optional template ROI config to start from (defaults to the resolution-based rois_guess file). "
            "Useful if you want to refine an existing profile."
        ),
    )
    p.add_argument(
        "--fresh-frame",
        action="store_true",
        help="Capture a fresh frame before each ROI selection step (slower but more robust).",
    )
    p.add_argument("--write", action="store_true", help="Escribe las ROIs en el config correspondiente.")
    p.add_argument("--preview-dir", type=str, default=str(Path("logs") / "roi_preview"))
    p.add_argument(
        "--only",
        type=str,
        default="",
        help="Lista separada por comas de ROIs a calibrar (ej: hp_top_ocr,mp_top_ocr,cap_ocr).",
    )
    p.add_argument(
        "--skip-bases",
        action="store_true",
        help="No pedir ROIs base (usa las ya existentes en el config).",
    )
    return p.parse_args()


def _capture_one(cap) -> Any:
    frame = None
    for _ in range(60):
        frame = cap.capture()
        if frame is not None:
            break
        try:
            time.sleep(0.02)
        except Exception:
            pass
    return frame


def main() -> int:
    _add_src_to_syspath()

    from capture.dxgi_capture import DXGICapture
    from vision.presence import is_hungry_hsv, is_nonempty_icon

    args = _parse_args()

    cap = DXGICapture(force_monitor=args.monitor)

    frame = _capture_one(cap)
    if frame is None:
        raise SystemExit("No se pudo capturar ningún frame")

    resolution = (int(frame.shape[1]), int(frame.shape[0]))
    cfg_path = _pick_config_file(resolution)
    if (args.template or "").strip():
        cfg_path = str(Path(str(args.template)).as_posix())
    rois, source_resolution = _load_config(cfg_path)
    rois = dict(rois)
    rois["_source_resolution"] = source_resolution

    source_w, source_h = int(source_resolution[0]), int(source_resolution[1])
    frame_w, frame_h = int(frame.shape[1]), int(frame.shape[0])

    out_path = (args.out or "").strip()
    if out_path:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    print(f"🧭 Template: {cfg_path} | frame={frame_w}x{frame_h} source={source_resolution}")
    if out_path:
        print(f"🧭 Profile out: {out_path}")
    print("▶ Wizard: selecciona ROIs base y sub-ROIs. Enter=OK, Esc=Cancelar en cada paso.")

    only_raw = (args.only or "").strip()
    only: set[str] | None = None
    if only_raw:
        only = {s.strip() for s in only_raw.split(",") if s.strip()}

    base_steps = [
        Step("hpmp_top_strip", "frame", "Franja superior completa donde están HP/MP."),
        Step("skills_panel", "frame", "Panel de skills (columna derecha izquierda)."),
        Step("battlelist_panel", "frame", "Panel battle list (debajo de skills)."),
        Step("right_hud_panel", "frame", "HUD derecho (minimap/equipment/states/barras)."),
        Step("game_viewport", "frame", "Viewport del juego."),
        Step("chat_panel", "frame", "Chat inferior completo."),
    ]

    if not args.skip_bases:
        for st in base_steps:
            if only is not None and st.name not in only:
                continue
            if bool(args.fresh_frame):
                nf = _capture_one(cap)
                if nf is not None:
                    frame = nf
                    frame_w, frame_h = int(frame.shape[1]), int(frame.shape[0])
            msg = f"\n🧩 Selecciona base ROI: {st.name} (sobre frame)"
            if st.help:
                msg += f"\n    ℹ️  {st.help}"
            print(msg)
            rx, ry, rw, rh = _select_roi_tk(f"Selecciona {st.name} (base)", frame)
            if rw <= 1 or rh <= 1:
                print(f"⚠️  Saltado {st.name} (cancelado)")
                continue
            rois[st.name] = _frame_px_to_source_norm(
                x=int(rx),
                y=int(ry),
                w=int(rw),
                h=int(rh),
                frame_w=frame_w,
                frame_h=frame_h,
                source_w=source_w,
                source_h=source_h,
            )
            ts = time.time()
            _draw_preview(frame, rois, resolution, Path(args.preview_dir) / f"{ts:.6f}_preview_after_{st.name}.png")

    def _select_in_base(roi_name: str, base_name: str) -> None:
        nonlocal frame, frame_w, frame_h
        if bool(args.fresh_frame):
            nf = _capture_one(cap)
            if nf is not None:
                frame = nf
                frame_w, frame_h = int(frame.shape[1]), int(frame.shape[0])
        base_def = rois.get(base_name)
        if not isinstance(base_def, dict):
            print(f"⚠️  No existe base '{base_name}', salteo {roi_name}")
            return
        try:
            bx, by, bw, bh = _roi_to_px(frame=frame, rois=rois, resolution=resolution, roi_def=base_def)
        except Exception:
            print(f"⚠️  No pude recortar base '{base_name}', salteo {roi_name}")
            return

        # Si la base es demasiado baja (ej: top strip), seleccionar sobre el frame completo
        # es mucho más usable que intentar dibujar un rectángulo en una tira de pocos píxeles.
        use_full_frame = bool(int(bh) < 120)
        sel_x = 0
        sel_y = 0
        sel_w = 0
        sel_h = 0
        if use_full_frame:
            print(f"\n🧩 Selecciona ROI: {roi_name} (idealmente dentro de {base_name})")
            print(f"    ℹ️  Nota: {base_name} es muy bajo; selecciona sobre el frame completo.")
            # En modo frame completo, forzamos que el ROI quede dentro de la base
            # recortando por intersección (así pasa el sanity check de nesting).
            for attempt in range(1, 4):
                rx, ry, rw, rh = _select_roi_tk(f"Selecciona {roi_name} (sobre frame completo)", frame)
                if rw <= 1 or rh <= 1:
                    print(f"⚠️  Saltado {roi_name} (cancelado)")
                    return

                x0 = int(rx)
                y0 = int(ry)
                x1 = int(rx + max(1, rw))
                y1 = int(ry + max(1, rh))

                bx0 = int(bx)
                by0 = int(by)
                bx1 = int(bx + max(1, bw))
                by1 = int(by + max(1, bh))

                ix0 = max(x0, bx0)
                iy0 = max(y0, by0)
                ix1 = min(x1, bx1)
                iy1 = min(y1, by1)

                iw = int(max(0, ix1 - ix0))
                ih = int(max(0, iy1 - iy0))
                if iw >= 2 and ih >= 2:
                    sel_x = int(ix0)
                    sel_y = int(iy0)
                    sel_w = int(iw)
                    sel_h = int(ih)
                    break

                if attempt < 3:
                    print(f"⚠️  {roi_name} quedó fuera de {base_name}. Reintenta (intento {attempt}/3).")
                else:
                    print(f"⚠️  No se pudo seleccionar {roi_name} dentro de {base_name}; salteando.")
                    return
        else:
            base_img = frame[by : by + bh, bx : bx + bw].copy()
            print(f"\n🧩 Selecciona ROI: {roi_name} (dentro de {base_name})")
            rx, ry, rw, rh = _select_roi_tk(f"Selecciona {roi_name} dentro de {base_name}", base_img)
            if rw <= 1 or rh <= 1:
                # Fallbacks específicos para ROIs que podemos derivar fácilmente.
                # Si el usuario cancela, derivamos algo razonable dentro de la base.
                if roi_name == "battlelist_rows" and base_name == "battlelist_panel":
                    header = max(8, int(round(float(bh) * 0.10)))
                    sel_x = int(bx)
                    sel_y = int(by + header)
                    sel_w = int(max(1, bw))
                    sel_h = int(max(1, bh - header))
                    print(
                        f"⚠️  {roi_name} cancelado; usando fallback automático (panel - header {header}px)."
                    )
                elif roi_name in {"cap_ocr", "soul_ocr"} and base_name == "skills_panel":
                    # En Tibia, Soul/Cap son campos numéricos hacia la derecha y parte baja del panel.
                    # Elegimos el tercio-derecho como campo de números y un alto moderado.
                    x_pad = max(2, int(round(float(bw) * 0.02)))
                    y_pad = max(2, int(round(float(bh) * 0.02)))

                    field_x = int(bx + float(bw) * 0.55)
                    field_w = int(max(1, (bx + bw) - field_x - x_pad))

                    if roi_name == "soul_ocr":
                        rel_y = 0.70
                    else:
                        rel_y = 0.82

                    field_y = int(by + float(bh) * rel_y)
                    field_h = int(max(1, float(bh) * 0.12))

                    # Clamp to base
                    sel_x = max(int(bx), min(int(bx + bw - 1), field_x))
                    sel_y = max(int(by), min(int(by + bh - 1), field_y))
                    sel_w = int(max(1, min(int(bx + bw) - sel_x, field_w)))
                    sel_h = int(max(1, min(int(by + bh) - sel_y - y_pad, field_h)))

                    print(
                        f"⚠️  {roi_name} cancelado; usando fallback automático dentro de skills_panel (x={sel_x}, y={sel_y}, w={sel_w}, h={sel_h})."
                    )
                else:
                    print(f"⚠️  Saltado {roi_name} (cancelado)")
                    return
            else:
                sel_x = int(bx + rx)
                sel_y = int(by + ry)
                sel_w = int(rw)
                sel_h = int(rh)

        if sel_w <= 1 or sel_h <= 1:
            print(f"⚠️  Saltado {roi_name} (vacío)")
            return
        rois[roi_name] = _frame_px_to_source_norm(
            x=sel_x,
            y=sel_y,
            w=int(sel_w),
            h=int(sel_h),
            frame_w=frame_w,
            frame_h=frame_h,
            source_w=source_w,
            source_h=source_h,
        )

        ts = time.time()
        _draw_preview(frame, rois, resolution, Path(args.preview_dir) / f"{ts:.6f}_preview_after_{roi_name}.png")

    nested_steps: list[tuple[str, str]] = [
        ("hp_top_ocr", "hpmp_top_strip"),
        ("mp_top_ocr", "hpmp_top_strip"),
        ("cap_ocr", "skills_panel"),
        ("soul_ocr", "skills_panel"),
        ("battlelist_rows", "battlelist_panel"),
        ("minimap_content", "right_hud_panel"),
        ("equipment_slots", "right_hud_panel"),
        ("states_icons", "right_hud_panel"),
        ("hpmp_low_panel", "right_hud_panel"),
        ("hp_low_bar", "hpmp_low_panel"),
        ("mp_low_bar", "hpmp_low_panel"),
        ("ring_slot", "equipment_slots"),
        ("amulet_slot", "equipment_slots"),
        ("hungry_icon", "states_icons"),
    ]

    for roi_name, base_name in nested_steps:
        if only is not None and roi_name not in only:
            continue
        _select_in_base(roi_name, base_name)

    rois_out = {k: v for k, v in rois.items() if not str(k).startswith("_")}

    if args.write:
        _save_config(cfg_path, rois_guess_norm=rois_out, source_resolution=source_resolution)
        print(f"\n✍️  Escrito template actualizado: {cfg_path}")

    if out_path:
        _save_config(out_path, rois_guess_norm=rois_out, source_resolution=source_resolution)
        print(f"\n✍️  Escrito profile: {out_path}")
        print("\n➡️  Para usarlo en el bot:")
        print(f"   $env:ROIS_CONFIG='{out_path}'")
        print("   poetry run python -m src.main")

    print("\n✅ Validación rápida")
    # OCR validation (deferred init: no bloquea el wizard)
    try:
        from vision.ocr import OCRProcessor

        ocr = OCRProcessor()

        try:
            hp_cur, hp_max, mp_cur, mp_max = ocr.extract_hp_mp_full(frame, rois, resolution, rf_boxes=None)  # type: ignore[arg-type]
            print(f"- HP/MP OCR: HP {hp_cur}/{hp_max} | MP {mp_cur}/{mp_max}")
        except Exception:
            print("- HP/MP OCR: (error)")

        cap_val = ocr.extract_capacity(frame, rois, resolution)  # type: ignore[arg-type]
        print(f"- CAP OCR: {cap_val}")
    except Exception:
        print("- OCR: (error)")

    try:
        ring = None
        amulet = None
        if isinstance(rois.get("ring_slot"), dict):
            x, y, w, h = _roi_to_px(frame=frame, rois=rois, resolution=resolution, roi_def=rois["ring_slot"])  # type: ignore[arg-type]
            ring = bool(is_nonempty_icon(frame[y : y + h, x : x + w]))
        if isinstance(rois.get("amulet_slot"), dict):
            x, y, w, h = _roi_to_px(frame=frame, rois=rois, resolution=resolution, roi_def=rois["amulet_slot"])  # type: ignore[arg-type]
            amulet = bool(is_nonempty_icon(frame[y : y + h, x : x + w]))
        print(f"- Ring: {ring} | Amulet: {amulet}")
    except Exception:
        print("- Ring/Amulet: (error)")

    try:
        hungry = None
        if isinstance(rois.get("hungry_icon"), dict):
            x, y, w, h = _roi_to_px(frame=frame, rois=rois, resolution=resolution, roi_def=rois["hungry_icon"])  # type: ignore[arg-type]
            hungry = bool(is_hungry_hsv(frame[y : y + h, x : x + w]))
        print(f"- Hungry: {hungry}")
    except Exception:
        print("- Hungry: (error)")

    print(f"\n🖼️  Previews guardados en: {args.preview_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
