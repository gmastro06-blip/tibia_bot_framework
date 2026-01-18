from __future__ import annotations

import argparse
import json
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Optional

try:
    from PIL import Image, ImageTk  # type: ignore
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ImageTk = None  # type: ignore


class RoiEditor:
    _bg_photo: Any

    def __init__(self, json_path: str, *, bg_path: str | None = None, show_all: bool = True) -> None:
        self.json_path = Path(json_path)
        self.data = self._load_json(self.json_path)
        self.source_w, self.source_h = self._get_source_resolution(self.data)
        self.rois = self._get_rois_dict(self.data)

        self.root = tk.Tk()
        self.root.title(f"ROI Normalizado Editor — {self.json_path.name}")
        self.root.geometry("1100x780")

        # Canvas mapping (source px -> canvas px)
        self._scale = 1.0
        self._offx = 0.0
        self._offy = 0.0

        # Zoom + view center (in source pixels)
        self._zoom = 1.0
        self._view_center_src: tuple[float, float] = (
            float(self.source_w) / 2.0,
            float(self.source_h) / 2.0,
        )

        # Background image (optional)
        self._bg_path: Optional[Path] = None
        self._bg_photo = None  # must keep reference alive
        self._bg_canvas_id: Optional[int] = None

        # Overlay mode
        self._show_all_var = tk.BooleanVar(value=bool(show_all))
        self._all_roi_ids: list[int] = []

        self.selected_roi: Optional[str] = None
        self.rect_id: Optional[int] = None
        self.label_id: Optional[int] = None
        self._handle_ids: list[int] = []

        # Mouse interaction state
        self._hit_tol_px = 8
        self._drag_active = False
        self._drag_mode: str = ""
        self._drag_handle: str = ""
        self._drag_roi: Optional[str] = None
        self._drag_start_src: tuple[float, float] = (0.0, 0.0)
        self._drag_start_rect_src: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

        # Pan interaction state
        self._space_down = False
        self._pan_active = False
        self._pan_start_canvas: tuple[float, float] = (0.0, 0.0)
        self._pan_start_off: tuple[float, float] = (0.0, 0.0)

        # UI
        self.canvas = tk.Canvas(self.root, bg="#111", highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        # Zoom (Windows: MouseWheel; Linux: Button-4/5)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)
        self.canvas.bind("<Button-5>", self._on_mousewheel)

        # Pan (middle mouse)
        self.canvas.bind("<Button-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonRelease-2>", self._on_pan_end)

        # Pan (Space + left drag)
        try:
            self.root.bind_all("<KeyPress-space>", self._on_space_down)
            self.root.bind_all("<KeyRelease-space>", self._on_space_up)
        except Exception:
            pass

        self.sidebar = ttk.Frame(self.root, width=320)
        self.sidebar.pack(side=tk.RIGHT, fill=tk.Y)

        # ROI edit vars
        self._x_var = tk.StringVar(value="")
        self._y_var = tk.StringVar(value="")
        self._w_var = tk.StringVar(value="")
        self._h_var = tk.StringVar(value="")

        self._build_sidebar()

        # Optional background preload
        try:
            if bg_path:
                p = Path(bg_path)
                if p.is_file():
                    self._bg_path = p
        except Exception:
            pass

        self._recompute_transform()
        self._redraw_all()

    @staticmethod
    def _load_json(path: Path) -> dict:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _get_source_resolution(data: dict) -> tuple[int, int]:
        res = data.get("source_resolution")
        if not isinstance(res, (list, tuple)) or len(res) < 2:
            raise ValueError("JSON inválido: falta 'source_resolution': [w, h]")
        return int(res[0]), int(res[1])

    @staticmethod
    def _get_rois_dict(data: dict) -> dict:
        rois = data.get("rois_guess_norm")
        if rois is None:
            rois = data.get("rois")
        if not isinstance(rois, dict):
            raise ValueError("JSON inválido: falta 'rois_guess_norm' (dict)")
        return rois

    def _build_sidebar(self) -> None:
        ttk.Label(self.sidebar, text="ROIs", font=("Segoe UI", 11, "bold")).pack(
            pady=(12, 6), padx=10, anchor="w"
        )

        self.listbox = tk.Listbox(self.sidebar, height=18, selectmode=tk.SINGLE)
        self.listbox.pack(fill=tk.BOTH, expand=False, padx=10)
        self.listbox.bind("<<ListboxSelect>>", self.on_select)
        self._refresh_listbox()

        bg_frame = ttk.LabelFrame(self.sidebar, text="Imagen de fondo")
        bg_frame.pack(fill=tk.X, padx=10, pady=(10, 0))
        ttk.Button(bg_frame, text="Cargar…", command=self.load_background).pack(
            padx=10, pady=(8, 4), fill=tk.X
        )
        ttk.Button(bg_frame, text="Quitar", command=self.clear_background).pack(
            padx=10, pady=(0, 8), fill=tk.X
        )

        view_frame = ttk.LabelFrame(self.sidebar, text="Vista")
        view_frame.pack(fill=tk.X, padx=10, pady=(10, 0))
        ttk.Checkbutton(
            view_frame,
            text="Mostrar todas las ROIs",
            variable=self._show_all_var,
            command=self._redraw_all,
        ).pack(padx=10, pady=(8, 8), anchor="w")

        zoom_row = ttk.Frame(view_frame)
        zoom_row.pack(fill=tk.X, padx=10, pady=(0, 10))

        self._zoom_label_var = tk.StringVar(value="Zoom: 100%")
        ttk.Label(zoom_row, textvariable=self._zoom_label_var).pack(side=tk.LEFT)

        ttk.Button(zoom_row, text="-", width=3, command=lambda: self._zoom_step(0.9)).pack(side=tk.RIGHT)
        ttk.Button(zoom_row, text="+", width=3, command=lambda: self._zoom_step(1.1)).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(zoom_row, text="100%", width=6, command=self._zoom_reset).pack(side=tk.RIGHT, padx=(0, 6))

        edit = ttk.LabelFrame(self.sidebar, text="Editar ROI (normalizado)")
        edit.pack(fill=tk.X, padx=10, pady=(10, 0))

        def row(label: str, var: tk.StringVar) -> None:
            r = ttk.Frame(edit)
            r.pack(fill=tk.X, padx=10, pady=3)
            ttk.Label(r, text=label, width=2).pack(side=tk.LEFT)
            ttk.Entry(r, textvariable=var).pack(side=tk.RIGHT, fill=tk.X, expand=True)

        row("x", self._x_var)
        row("y", self._y_var)
        row("w", self._w_var)
        row("h", self._h_var)

        ttk.Button(edit, text="Aplicar", command=self.apply_fields).pack(
            padx=10, pady=(6, 10), fill=tk.X
        )

        hint = (
            "Tip: Para Cap/Soul, a veces el valor está\n"
            "debajo del label. Ajusta w/h para\n"
            "encerrar solo los dígitos."
        )
        ttk.Label(self.sidebar, text=hint, justify="left").pack(
            padx=10, pady=(10, 0), anchor="w"
        )

        btns = ttk.Frame(self.sidebar)
        btns.pack(fill=tk.X, padx=10, pady=12)
        ttk.Button(btns, text="Guardar JSON", command=self.save).pack(fill=tk.X)
        ttk.Button(btns, text="Salir", command=self.root.destroy).pack(fill=tk.X, pady=(8, 0))

    def _refresh_listbox(self) -> None:
        self.listbox.delete(0, tk.END)
        for name in sorted(self.rois.keys()):
            self.listbox.insert(tk.END, name)

    def _recompute_transform(self) -> None:
        cw = max(1, int(self.canvas.winfo_width()))
        ch = max(1, int(self.canvas.winfo_height()))
        sw = max(1, int(self.source_w))
        sh = max(1, int(self.source_h))

        fit_scale = min(cw / sw, ch / sh)
        self._scale = float(fit_scale) * float(self._zoom)

        # Keep view center stable across resizes/zoom.
        cx_src, cy_src = self._view_center_src
        self._offx = (float(cw) / 2.0) - float(cx_src) * float(self._scale)
        self._offy = (float(ch) / 2.0) - float(cy_src) * float(self._scale)

        try:
            self._zoom_label_var.set(f"Zoom: {int(round(self._zoom * 100.0))}%")
        except Exception:
            pass

    def _src_to_canvas(self, x: float, y: float) -> tuple[float, float]:
        return self._offx + x * self._scale, self._offy + y * self._scale

    def _canvas_to_src(self, x: float, y: float) -> tuple[float, float]:
        try:
            sx = (float(x) - float(self._offx)) / max(1e-9, float(self._scale))
            sy = (float(y) - float(self._offy)) / max(1e-9, float(self._scale))
        except Exception:
            return 0.0, 0.0

        sx = max(0.0, min(float(self.source_w), float(sx)))
        sy = max(0.0, min(float(self.source_h), float(sy)))
        return sx, sy

    def _canvas_to_src_unclamped(self, x: float, y: float) -> tuple[float, float]:
        try:
            sx = (float(x) - float(self._offx)) / max(1e-9, float(self._scale))
            sy = (float(y) - float(self._offy)) / max(1e-9, float(self._scale))
            return float(sx), float(sy)
        except Exception:
            return 0.0, 0.0

    def _set_view_center_from_anchor(self, *, anchor_src: tuple[float, float], anchor_canvas: tuple[float, float]) -> None:
        # Choose center so that anchor_src maps to anchor_canvas.
        cw = max(1.0, float(self.canvas.winfo_width()))
        ch = max(1.0, float(self.canvas.winfo_height()))
        ax, ay = float(anchor_canvas[0]), float(anchor_canvas[1])
        sx, sy = float(anchor_src[0]), float(anchor_src[1])
        scale = max(1e-9, float(self._scale))
        cx_src = sx - ((ax - (cw / 2.0)) / scale)
        cy_src = sy - ((ay - (ch / 2.0)) / scale)
        self._view_center_src = (cx_src, cy_src)

    def _update_view_center_from_offsets(self) -> None:
        try:
            cw = max(1.0, float(self.canvas.winfo_width()))
            ch = max(1.0, float(self.canvas.winfo_height()))
            scale = max(1e-9, float(self._scale))
            cx_src = ((cw / 2.0) - float(self._offx)) / scale
            cy_src = ((ch / 2.0) - float(self._offy)) / scale
            self._view_center_src = (float(cx_src), float(cy_src))
        except Exception:
            pass

    def _zoom_set(self, new_zoom: float, *, anchor_canvas: tuple[float, float] | None = None) -> None:
        new_zoom = float(new_zoom)
        new_zoom = max(0.1, min(8.0, new_zoom))

        anchor = anchor_canvas
        if anchor is None:
            # Default anchor: center of canvas
            anchor = (float(self.canvas.winfo_width()) / 2.0, float(self.canvas.winfo_height()) / 2.0)

        # Keep the source point under the anchor stable.
        src_pt = self._canvas_to_src_unclamped(anchor[0], anchor[1])
        self._zoom = float(new_zoom)
        self._recompute_transform()
        self._set_view_center_from_anchor(anchor_src=src_pt, anchor_canvas=anchor)
        self._recompute_transform()
        self._redraw_all()

    def _zoom_step(self, factor: float) -> None:
        try:
            z = float(self._zoom) * float(factor)
        except Exception:
            z = float(self._zoom)
        self._zoom_set(z)

    def _zoom_reset(self) -> None:
        self._zoom_set(1.0)

    def _on_mousewheel(self, evt) -> None:
        # Windows: evt.delta (120 increments). Linux: Button-4/5.
        try:
            if getattr(evt, "num", None) == 4:
                direction = 1
            elif getattr(evt, "num", None) == 5:
                direction = -1
            else:
                direction = 1 if int(getattr(evt, "delta", 0) or 0) > 0 else -1
        except Exception:
            direction = 0
        if direction == 0:
            return

        factor = 1.1 if direction > 0 else 0.9
        self._zoom_set(float(self._zoom) * float(factor), anchor_canvas=(float(evt.x), float(evt.y)))

    def _roi_src_px_to_norm(self, x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        sw = max(1.0, float(self.source_w))
        sh = max(1.0, float(self.source_h))
        xn = float(x) / sw
        yn = float(y) / sh
        wn = float(w) / sw
        hn = float(h) / sh
        return xn, yn, wn, hn

    def _set_roi_norm(self, name: str, x: float, y: float, w: float, h: float, *, sync_fields: bool = True) -> None:
        roi = self.rois.get(name)
        if not isinstance(roi, dict):
            self.rois[name] = {}
            roi = self.rois[name]

        # Clamp + ensure positive size
        x = max(0.0, min(1.0, float(x)))
        y = max(0.0, min(1.0, float(y)))
        w = max(0.0, float(w))
        h = max(0.0, float(h))
        w = min(1.0 - x, w)
        h = min(1.0 - y, h)

        roi["x"] = float(x)
        roi["y"] = float(y)
        roi["w"] = float(w)
        roi["h"] = float(h)

        if sync_fields:
            self._sync_fields_from_roi(name)

    def _select_roi(self, name: str) -> None:
        if name not in self.rois:
            return
        self.selected_roi = name
        # Sync listbox selection
        try:
            names = [self.listbox.get(i) for i in range(self.listbox.size())]
            idx = names.index(name)
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(idx)
            self.listbox.see(idx)
        except Exception:
            pass
        self._sync_fields_from_roi(name)
        self._redraw_all()

    def _pick_roi_at_src(self, sx: float, sy: float) -> Optional[str]:
        # Pick the smallest-area ROI that contains the point.
        best = None
        best_area = None
        for name, roi in self.rois.items():
            if not isinstance(roi, dict):
                continue
            try:
                x, y, w, h = self._roi_norm_to_src_px(roi)
                if sx < x or sy < y or sx > (x + w) or sy > (y + h):
                    continue
                area = float(max(0.0, w) * max(0.0, h))
                if best is None or (best_area is not None and area < best_area):
                    best = str(name)
                    best_area = area
            except Exception:
                continue
        return best

    def _hit_test_handle(self, name: str, cx: float, cy: float) -> str:
        # Returns: 'nw','ne','sw','se','n','s','w','e','move',''
        roi = self.rois.get(name)
        if not isinstance(roi, dict):
            return ""
        try:
            sx, sy, sw, sh = self._roi_norm_to_src_px(roi)
            x0, y0 = self._src_to_canvas(sx, sy)
            x1, y1 = self._src_to_canvas(sx + sw, sy + sh)
        except Exception:
            return ""

        tol = float(self._hit_tol_px)

        def near(a: float, b: float) -> bool:
            return abs(float(a) - float(b)) <= tol

        inside = (cx >= x0 and cx <= x1 and cy >= y0 and cy <= y1)
        if not inside:
            return ""

        # Corners
        if near(cx, x0) and near(cy, y0):
            return "nw"
        if near(cx, x1) and near(cy, y0):
            return "ne"
        if near(cx, x0) and near(cy, y1):
            return "sw"
        if near(cx, x1) and near(cy, y1):
            return "se"

        # Edges
        if near(cy, y0):
            return "n"
        if near(cy, y1):
            return "s"
        if near(cx, x0):
            return "w"
        if near(cx, x1):
            return "e"

        return "move"

    def _cursor_for_handle(self, handle: str) -> str:
        mapping = {
            "move": "fleur",
            "n": "sb_v_double_arrow",
            "s": "sb_v_double_arrow",
            "e": "sb_h_double_arrow",
            "w": "sb_h_double_arrow",
            "nw": "size_nw_se",
            "se": "size_nw_se",
            "ne": "size_ne_sw",
            "sw": "size_ne_sw",
        }
        return mapping.get(handle, "")

    def _roi_norm_to_src_px(self, roi: dict) -> tuple[float, float, float, float]:
        x = float(roi.get("x", 0.0)) * float(self.source_w)
        y = float(roi.get("y", 0.0)) * float(self.source_h)
        w = float(roi.get("w", 0.0)) * float(self.source_w)
        h = float(roi.get("h", 0.0)) * float(self.source_h)
        return x, y, w, h

    def _draw_background(self) -> None:
        if self._bg_path is None or Image is None or ImageTk is None:
            if self._bg_canvas_id is not None:
                try:
                    self.canvas.delete(self._bg_canvas_id)
                except Exception:
                    pass
                self._bg_canvas_id = None
            self._bg_photo = None
            return

        try:
            img = Image.open(self._bg_path).convert("RGB")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo cargar imagen:\n{e}")
            self._bg_path = None
            return

        try:
            iw, ih = img.size
            if (iw, ih) != (int(self.source_w), int(self.source_h)):
                ar_img = float(iw) / max(1.0, float(ih))
                ar_src = float(self.source_w) / max(1.0, float(self.source_h))
                if abs(ar_img - ar_src) > 0.01:
                    try:
                        messagebox.showwarning(
                            "Aviso",
                            "La imagen no coincide con el aspect ratio de 'source_resolution'.\n"
                            "Se mostrará reescalada y puede no alinear perfecto.",
                        )
                    except Exception:
                        pass
                img = img.resize((int(self.source_w), int(self.source_h)))
        except Exception:
            pass

        vw = max(1, int(round(self.source_w * self._scale)))
        vh = max(1, int(round(self.source_h * self._scale)))
        img_view = img.resize((vw, vh))

        self._bg_photo = ImageTk.PhotoImage(img_view)

        x0, y0 = self._src_to_canvas(0.0, 0.0)
        if self._bg_canvas_id is None:
            self._bg_canvas_id = self.canvas.create_image(x0, y0, anchor="nw", image=self._bg_photo)
        else:
            self.canvas.coords(self._bg_canvas_id, x0, y0)
            self.canvas.itemconfig(self._bg_canvas_id, image=self._bg_photo)

        try:
            self.canvas.tag_lower(self._bg_canvas_id)
        except Exception:
            pass

    def _clear_all_roi_overlays(self) -> None:
        try:
            for cid in list(self._all_roi_ids):
                try:
                    self.canvas.delete(cid)
                except Exception:
                    pass
        except Exception:
            pass
        self._all_roi_ids = []

    def _draw_all_rois(self) -> None:
        self._clear_all_roi_overlays()

        # Only draw when enabled.
        try:
            if not bool(self._show_all_var.get()):
                return
        except Exception:
            return

        # Draw thin rectangles for every ROI.
        for name in sorted(self.rois.keys()):
            if self.selected_roi and name == self.selected_roi:
                continue
            roi = self.rois.get(name)
            if not isinstance(roi, dict):
                continue
            try:
                sx, sy, sw, sh = self._roi_norm_to_src_px(roi)
                x0, y0 = self._src_to_canvas(sx, sy)
                x1, y1 = self._src_to_canvas(sx + sw, sy + sh)
                rid = self.canvas.create_rectangle(x0, y0, x1, y1, outline="#ffcc00", width=1)
                self._all_roi_ids.append(int(rid))

                # Label only for reasonably large ROIs (avoid unreadable clutter).
                if (x1 - x0) >= 40 and (y1 - y0) >= 18:
                    tid = self.canvas.create_text(
                        x0 + 3,
                        y0 + 2,
                        anchor="nw",
                        text=name,
                        fill="#ffcc00",
                        font=("Consolas", 8),
                    )
                    self._all_roi_ids.append(int(tid))
            except Exception:
                continue

    def _draw_selected_roi(self) -> None:
        # clear handles
        try:
            for hid in list(self._handle_ids):
                try:
                    self.canvas.delete(hid)
                except Exception:
                    pass
        except Exception:
            pass
        self._handle_ids = []

        if self.rect_id is not None:
            try:
                self.canvas.delete(self.rect_id)
            except Exception:
                pass
            self.rect_id = None
        if self.label_id is not None:
            try:
                self.canvas.delete(self.label_id)
            except Exception:
                pass
            self.label_id = None

        if not self.selected_roi:
            return
        roi = self.rois.get(self.selected_roi)
        if not isinstance(roi, dict):
            return

        sx, sy, sw, sh = self._roi_norm_to_src_px(roi)
        x0, y0 = self._src_to_canvas(sx, sy)
        x1, y1 = self._src_to_canvas(sx + sw, sy + sh)

        self.rect_id = self.canvas.create_rectangle(x0, y0, x1, y1, outline="#00ff66", width=2)
        self.label_id = self.canvas.create_text(
            x0 + 6,
            y0 + 6,
            anchor="nw",
            text=self.selected_roi,
            fill="#00ff66",
            font=("Consolas", 10, "bold"),
        )

        # Corner handles for resizing
        try:
            hs = 5
            pts = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
            for hx, hy in pts:
                hid = self.canvas.create_rectangle(
                    hx - hs,
                    hy - hs,
                    hx + hs,
                    hy + hs,
                    outline="#00ff66",
                    fill="#00331a",
                    width=1,
                )
                self._handle_ids.append(int(hid))
        except Exception:
            pass

    def _redraw_all(self) -> None:
        self._draw_background()
        self._draw_all_rois()
        self._draw_selected_roi()

    def _on_canvas_resize(self, _evt=None) -> None:
        self._recompute_transform()
        self._redraw_all()

    def on_select(self, _evt=None) -> None:
        try:
            idxs = self.listbox.curselection()
            if not idxs:
                return
            name = str(self.listbox.get(idxs[0]))
        except Exception:
            return

        if name not in self.rois:
            return

        self._select_roi(name)

    def _on_canvas_click(self, evt) -> None:
        # Space-drag pan mode
        if bool(self._space_down):
            self._on_pan_start(evt)
            return

        try:
            sx, sy = self._canvas_to_src(evt.x, evt.y)
        except Exception:
            return

        picked = self._pick_roi_at_src(sx, sy)
        if picked:
            if picked != self.selected_roi:
                self._select_roi(picked)

            handle = self._hit_test_handle(picked, float(evt.x), float(evt.y))
            if not handle:
                return

            # Start drag
            self._drag_active = True
            self._drag_roi = picked
            self._drag_mode = "move" if handle == "move" else "resize"
            self._drag_handle = handle
            self._drag_start_src = (float(sx), float(sy))

            roi = self.rois.get(picked)
            if isinstance(roi, dict):
                self._drag_start_rect_src = self._roi_norm_to_src_px(roi)
        else:
            # Clicked empty space: stop dragging and keep selection.
            self._drag_active = False
            self._drag_roi = None

    def _on_canvas_drag(self, evt) -> None:
        # Space-drag pan mode
        if bool(self._pan_active):
            self._on_pan_move(evt)
            return

        if not self._drag_active or not self._drag_roi:
            return
        roi_name = self._drag_roi

        sx, sy = self._canvas_to_src(evt.x, evt.y)
        sx0, sy0 = self._drag_start_src
        dx = float(sx) - float(sx0)
        dy = float(sy) - float(sy0)

        x, y, w, h = self._drag_start_rect_src
        x0 = float(x)
        y0 = float(y)
        x1 = float(x) + float(w)
        y1 = float(y) + float(h)

        min_px = 2.0

        if self._drag_mode == "move":
            x0 = x0 + dx
            y0 = y0 + dy
            x1 = x1 + dx
            y1 = y1 + dy
        else:
            hnd = str(self._drag_handle or "")
            if "w" in hnd:
                x0 = x0 + dx
            if "e" in hnd:
                x1 = x1 + dx
            if "n" in hnd:
                y0 = y0 + dy
            if "s" in hnd:
                y1 = y1 + dy

        # Normalize/correct ordering
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0

        # Enforce minimum size
        if (x1 - x0) < min_px:
            x1 = x0 + min_px
        if (y1 - y0) < min_px:
            y1 = y0 + min_px

        # Clamp to source bounds
        sw = float(self.source_w)
        sh = float(self.source_h)

        # For move, keep size constant while clamping.
        if self._drag_mode == "move":
            ww = x1 - x0
            hh = y1 - y0
            x0 = max(0.0, min(sw - ww, x0))
            y0 = max(0.0, min(sh - hh, y0))
            x1 = x0 + ww
            y1 = y0 + hh
        else:
            x0 = max(0.0, min(sw, x0))
            y0 = max(0.0, min(sh, y0))
            x1 = max(0.0, min(sw, x1))
            y1 = max(0.0, min(sh, y1))

        new_x = x0
        new_y = y0
        new_w = max(min_px, x1 - x0)
        new_h = max(min_px, y1 - y0)

        xn, yn, wn, hn = self._roi_src_px_to_norm(new_x, new_y, new_w, new_h)
        # Clamp normalized (handles bounds)
        self._set_roi_norm(roi_name, xn, yn, wn, hn, sync_fields=True)
        self._redraw_all()

    def _on_canvas_release(self, _evt=None) -> None:
        # End pan if we were panning via Space+drag
        if bool(self._pan_active):
            self._on_pan_end(_evt)

        self._drag_active = False
        self._drag_mode = ""
        self._drag_handle = ""
        self._drag_roi = None

    def _on_space_down(self, _evt=None) -> None:
        self._space_down = True

    def _on_space_up(self, _evt=None) -> None:
        self._space_down = False
        if self._pan_active:
            self._on_pan_end(_evt)

    def _on_pan_start(self, evt) -> None:
        # Stop ROI dragging if any
        self._drag_active = False
        self._drag_mode = ""
        self._drag_handle = ""
        self._drag_roi = None

        self._pan_active = True
        try:
            self._pan_start_canvas = (float(evt.x), float(evt.y))
        except Exception:
            self._pan_start_canvas = (0.0, 0.0)
        self._pan_start_off = (float(self._offx), float(self._offy))
        try:
            self.canvas.configure(cursor="hand2")
        except Exception:
            pass

    def _on_pan_move(self, evt) -> None:
        if not self._pan_active:
            return
        try:
            cx0, cy0 = self._pan_start_canvas
            dx = float(evt.x) - float(cx0)
            dy = float(evt.y) - float(cy0)
        except Exception:
            return

        ox0, oy0 = self._pan_start_off
        self._offx = float(ox0) + float(dx)
        self._offy = float(oy0) + float(dy)
        self._update_view_center_from_offsets()
        self._redraw_all()

    def _on_pan_end(self, _evt=None) -> None:
        self._pan_active = False
        self._update_view_center_from_offsets()
        try:
            self.canvas.configure(cursor="")
        except Exception:
            pass

    def _on_canvas_motion(self, evt) -> None:
        # Cursor feedback (only when not dragging)
        if self._drag_active or self._pan_active:
            return

        if bool(self._space_down):
            try:
                self.canvas.configure(cursor="hand2")
            except Exception:
                pass
            return

        cur = ""
        try:
            if self.selected_roi:
                h = self._hit_test_handle(self.selected_roi, float(evt.x), float(evt.y))
                cur = self._cursor_for_handle(h)
        except Exception:
            cur = ""
        try:
            self.canvas.configure(cursor=cur)
        except Exception:
            pass

    def _sync_fields_from_roi(self, name: str) -> None:
        roi = self.rois.get(name)
        if not isinstance(roi, dict):
            return

        def fmt(v: Any) -> str:
            if v is None:
                return ""
            try:
                return f"{float(v):.6f}"
            except Exception:
                try:
                    return f"{float(str(v)):.6f}"
                except Exception:
                    return ""

        self._x_var.set(fmt(roi.get("x")))
        self._y_var.set(fmt(roi.get("y")))
        self._w_var.set(fmt(roi.get("w")))
        self._h_var.set(fmt(roi.get("h")))

    def apply_fields(self) -> None:
        if not self.selected_roi:
            return

        def parse(v: str) -> float:
            return float((v or "").strip())

        try:
            x = parse(self._x_var.get())
            y = parse(self._y_var.get())
            w = parse(self._w_var.get())
            h = parse(self._h_var.get())
        except Exception:
            messagebox.showerror("Error", "Valores inválidos. Usa números (ej: 0.915000).")
            return

        x = max(0.0, min(1.0, x))
        y = max(0.0, min(1.0, y))
        w = max(0.0, min(1.0 - x, w))
        h = max(0.0, min(1.0 - y, h))

        roi = self.rois.get(self.selected_roi)
        if not isinstance(roi, dict):
            self.rois[self.selected_roi] = {}
            roi = self.rois[self.selected_roi]

        roi["x"] = float(x)
        roi["y"] = float(y)
        roi["w"] = float(w)
        roi["h"] = float(h)

        self._sync_fields_from_roi(self.selected_roi)
        self._redraw_all()

    def save(self) -> None:
        try:
            if "rois_guess_norm" in self.data and isinstance(self.data.get("rois_guess_norm"), dict):
                self.data["rois_guess_norm"] = self.rois
            elif "rois" in self.data and isinstance(self.data.get("rois"), dict):
                self.data["rois"] = self.rois
            else:
                self.data["rois_guess_norm"] = self.rois

            self.json_path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            messagebox.showinfo("OK", f"Guardado: {self.json_path}")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo guardar JSON:\n{e}")

    def load_background(self) -> None:
        if Image is None or ImageTk is None:
            messagebox.showerror("Error", "Pillow no está disponible en este entorno.")
            return

        p = filedialog.askopenfilename(
            title="Selecciona imagen de fondo",
            filetypes=[
                ("Imagen", "*.png;*.jpg;*.jpeg;*.bmp"),
                ("PNG", "*.png"),
                ("JPG", "*.jpg;*.jpeg"),
                ("Todos", "*.*"),
            ],
        )
        if not p:
            return

        self._bg_path = Path(p)
        self._redraw_all()

    def clear_background(self) -> None:
        self._bg_path = None
        self._redraw_all()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Editor simple de ROIs normalizados (con fondo opcional)")
    ap.add_argument(
        "json",
        nargs="?",
        default="configs/rois_guess_1920x1080.json",
        help="Ruta al JSON de ROIs (default: configs/rois_guess_1920x1080.json)",
    )
    ap.add_argument(
        "--bg",
        default="",
        help="Ruta a una imagen (PNG/JPG) para usar como fondo (ej: captura 1920x1080)",
    )
    ap.add_argument(
        "--show-all",
        default="1",
        help="1/0 para mostrar todas las ROIs (default: 1)",
    )
    args = ap.parse_args(argv)

    try:
        bg = str(args.bg or "").strip() or None
        show_all = str(args.show_all or "1").strip().lower() not in {"0", "false", "no", "off"}
        editor = RoiEditor(args.json, bg_path=bg, show_all=bool(show_all))
        editor.root.mainloop()
        return 0
    except Exception as e:
        try:
            messagebox.showerror("Error", str(e))
        except Exception:
            pass
        print(str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))