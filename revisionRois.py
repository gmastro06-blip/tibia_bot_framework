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

    def __init__(self, json_path: str) -> None:
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

        # Background image (optional)
        self._bg_path: Optional[Path] = None
        self._bg_photo = None  # must keep reference alive
        self._bg_canvas_id: Optional[int] = None

        self.selected_roi: Optional[str] = None
        self.rect_id: Optional[int] = None
        self.label_id: Optional[int] = None

        # UI
        self.canvas = tk.Canvas(self.root, bg="#111", highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        self.sidebar = ttk.Frame(self.root, width=320)
        self.sidebar.pack(side=tk.RIGHT, fill=tk.Y)

        # ROI edit vars
        self._x_var = tk.StringVar(value="")
        self._y_var = tk.StringVar(value="")
        self._w_var = tk.StringVar(value="")
        self._h_var = tk.StringVar(value="")

        self._build_sidebar()
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

        self._scale = min(cw / sw, ch / sh)
        view_w = sw * self._scale
        view_h = sh * self._scale
        self._offx = (cw - view_w) / 2.0
        self._offy = (ch - view_h) / 2.0

    def _src_to_canvas(self, x: float, y: float) -> tuple[float, float]:
        return self._offx + x * self._scale, self._offy + y * self._scale

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

    def _draw_selected_roi(self) -> None:
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

    def _redraw_all(self) -> None:
        self._draw_background()
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

        self.selected_roi = name
        self._sync_fields_from_roi(name)
        self._redraw_all()

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
    args = ap.parse_args(argv)

    try:
        editor = RoiEditor(args.json)
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