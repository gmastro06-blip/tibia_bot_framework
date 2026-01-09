from __future__ import annotations

import json
import sys
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, List, Optional, Tuple
import re

# Permite ejecutar este archivo directamente (python tools/cloudbot/waypoint_viewer_ui.py)
# y aun así importar módulos bajo <repo>/tools/.
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.cloudbot.quick_loot_profile import QuickLootProfile
from tools.cloudbot.refill_profile import RefillProfile
from tools.cloudbot.deposit_profile import DepositProfile

from tools.cloudbot.waypoints_parser import WPLine, parse_waypoints_in


_POS_RE = re.compile(r"\((\s*-?\d+\s*),(\s*-?\d+\s*),(\s*-?\d+\s*)\)")


class WaypointViewer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CloudBot Waypoint Viewer")
        self.geometry("800x600")

        # Data
        # This file is at <repo>/tools/cloudbot/waypoint_viewer_ui.py
        # parents[0]=cloudbot, parents[1]=tools, parents[2]=<repo>
        self.repo_root: Path = Path(__file__).resolve().parents[2]
        self.scripts_root: Path = self.repo_root / "scripts-master"
        self.reports_root: Path = self.repo_root / "reports" / "cloudbot"
        self.current_script: Optional[str] = None
        self.waypoints: List[WPLine] = []
        self.current_index = 0

        # Offline "cavebot" state (NO automation): derived from setup + waypoint actions
        self.targeting_enabled: bool = False
        self.loot_enabled: bool = False
        self.target_monsters: List[str] = []
        self.quick_loot_profile: QuickLootProfile | None = None
        self.refill_profile: RefillProfile | None = None
        self.deposit_profile: DepositProfile | None = None
        self.route_phase: str = "unknown"  # hunt|refill|deposit|unknown
        self.deposit_step: bool = False
        self._reports_cache: dict[str, Any] = {}

        # UI Elements
        self.menu_bar = tk.Menu(self)
        self.config(menu=self.menu_bar)

        file_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Archivo", menu=file_menu)
        file_menu.add_command(label="Abrir Script", command=self.open_script)
        file_menu.add_command(label="Abrir Archivo...", command=self.open_any_file)
        file_menu.add_separator()
        file_menu.add_command(label="Exportar Ruta (Framework)...", command=self.export_framework_route)
        file_menu.add_command(label="Crear JSON nuevo...", command=self.create_new_json)
        file_menu.add_separator()
        file_menu.add_command(label="Salir", command=self.quit)

        load_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Cargar", menu=load_menu)
        load_menu.add_command(label="Setup Actions JSON", command=lambda: self.load_json_file("setup_actions.json"))
        load_menu.add_command(label="Actions in Waypoints JSON", command=lambda: self.load_json_file("actions_in_waypoints.json"))
        load_menu.add_command(label="Route Stats JSON", command=lambda: self.load_json_file("route_stats.json"))
        load_menu.add_separator()
        load_menu.add_command(label="Perfil Quick Loot...", command=self.load_quick_loot_profile)
        load_menu.add_command(label="Perfil Refill...", command=self.load_refill_profile)
        load_menu.add_command(label="Perfil Deposit...", command=self.load_deposit_profile)
        load_menu.add_command(label="Otro JSON...", command=self.load_custom_json)

        # Canvas for grid/map
        self.canvas = tk.Canvas(self, bg="white")
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Panel for JSON display
        self.json_frame = tk.Frame(self, width=300)
        self.json_frame.pack(side=tk.RIGHT, fill=tk.Y)
        self.json_label = tk.Label(self.json_frame, text="Archivos Cargados:")
        self.json_label.pack(anchor=tk.W)
        self.json_text = tk.Text(self.json_frame, wrap=tk.WORD, height=20)
        self.json_text.pack(fill=tk.BOTH, expand=True)

        # Control frame
        control_frame = tk.Frame(self)
        control_frame.pack(fill=tk.X, side=tk.BOTTOM)

        self.prev_btn = tk.Button(control_frame, text="Anterior", command=self.prev_waypoint)
        self.prev_btn.pack(side=tk.LEFT, padx=5, pady=5)

        self.next_btn = tk.Button(control_frame, text="Siguiente", command=self.next_waypoint)
        self.next_btn.pack(side=tk.LEFT, padx=5, pady=5)

        self.simulate_btn = tk.Button(control_frame, text="Simular Ruta", command=self.toggle_simulation)
        self.simulate_btn.pack(side=tk.LEFT, padx=5, pady=5)

        self.reset_btn = tk.Button(control_frame, text="Reiniciar", command=self.reset_simulation)
        self.reset_btn.pack(side=tk.LEFT, padx=5, pady=5)

        self.index_label = tk.Label(control_frame, text="Waypoint: 0/0")
        self.index_label.pack(side=tk.LEFT, padx=20)

        self.info_label = tk.Label(control_frame, text="", anchor="w")
        self.info_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Bind keys
        self.bind("<Left>", lambda e: self.prev_waypoint())
        self.bind("<Right>", lambda e: self.next_waypoint())

        self.simulating = False
        self.sim_timer = None

        self._render_status()
        self.update_ui()

    def _render_status(self) -> None:
        self.json_text.delete(1.0, tk.END)
        self.json_text.insert(tk.END, "Estado\n\n")
        self.json_text.insert(tk.END, f"repo_root: {self.repo_root}\n")
        self.json_text.insert(tk.END, f"scripts_root: {self.scripts_root} ({'OK' if self.scripts_root.exists() else 'NO'})\n")
        self.json_text.insert(tk.END, f"reports_root: {self.reports_root} ({'OK' if self.reports_root.exists() else 'NO'})\n")
        if self.current_script:
            self.json_text.insert(tk.END, f"script_actual: {self.current_script}\n")
            script_reports = self.reports_root / self.current_script
            self.json_text.insert(tk.END, f"reports_script: {script_reports} ({'OK' if script_reports.exists() else 'NO'})\n")
            if self.target_monsters:
                preview = ", ".join(self.target_monsters[:10])
                if len(self.target_monsters) > 10:
                    preview += f" ... (+{len(self.target_monsters) - 10})"
                self.json_text.insert(tk.END, f"target_monsters: {len(self.target_monsters)} -> {preview}\n")

        self.json_text.insert(tk.END, f"\nEstado simulado (offline)\n")
        self.json_text.insert(tk.END, f"- targeting: {'ON' if self.targeting_enabled else 'OFF'}\n")
        self.json_text.insert(tk.END, f"- loot: {'ON' if self.loot_enabled else 'OFF'}\n")

        if self.quick_loot_profile is not None:
            s = self.quick_loot_profile.summary()
            self.json_text.insert(tk.END, "\nQuick Loot (perfil offline)\n")
            self.json_text.insert(tk.END, f"- mode: {s['mode']}\n")
            self.json_text.insert(tk.END, f"- accepted: {s['accepted_count']}\n")
            self.json_text.insert(tk.END, f"- skipped: {s['skipped_count']}\n")
            self.json_text.insert(tk.END, f"- container_categories: {s['container_categories_count']}\n")

        if self.refill_profile is not None:
            s = self.refill_profile.summary()
            self.json_text.insert(tk.END, "\nRefill (perfil offline)\n")
            self.json_text.insert(tk.END, f"- items: {s['items_count']}\n")
            if s.get("preview"):
                self.json_text.insert(tk.END, f"- preview: {', '.join(s['preview'])}\n")

        if self.deposit_profile is not None:
            s = self.deposit_profile.summary()
            self.json_text.insert(tk.END, "\nDeposit (perfil offline)\n")
            self.json_text.insert(tk.END, f"- mode: {s['mode']}\n")
            self.json_text.insert(tk.END, f"- keep: {s['keep_count']}\n")
            self.json_text.insert(tk.END, f"- deposit: {s['deposit_count']}\n")

        self.json_text.insert(tk.END, "\nRuta (marcas)\n")
        self.json_text.insert(tk.END, f"- phase: {self.route_phase}\n")
        self.json_text.insert(tk.END, "\nTips:\n- Archivo → Abrir Script: selecciona una carpeta dentro de scripts-master\n- Cargar → Route Stats JSON: se lee desde reports/cloudbot/<script>/\n")

    def load_quick_loot_profile(self) -> None:
        initial_dir = (
            str(self.repo_root / "configs")
            if (self.repo_root / "configs").exists()
            else str(self.repo_root)
        )
        filename = filedialog.askopenfilename(
            title="Cargar perfil Quick Loot (JSON)",
            initialdir=initial_dir,
            filetypes=[
                ("JSON (.json)", "*.json"),
                ("Todos", "*.*"),
            ],
        )
        if not filename:
            return
        try:
            profile = QuickLootProfile.load(Path(filename))
            self.quick_loot_profile = profile
            self.display_json(profile.summary(), f"Quick Loot: {Path(filename).name}")
            self._render_status()
            self.update_ui()
        except Exception as e:
            messagebox.showerror(
                "Error",
                f"No pude cargar el perfil Quick Loot:\n{filename}\n\n{e}",
            )

    def load_refill_profile(self) -> None:
        initial_dir = (
            str(self.repo_root / "configs")
            if (self.repo_root / "configs").exists()
            else str(self.repo_root)
        )
        filename = filedialog.askopenfilename(
            title="Cargar perfil Refill (JSON)",
            initialdir=initial_dir,
            filetypes=[
                ("JSON (.json)", "*.json"),
                ("Todos", "*.*"),
            ],
        )
        if not filename:
            return
        try:
            profile = RefillProfile.load(Path(filename))
            self.refill_profile = profile
            self.display_json(profile.summary(), f"Refill: {Path(filename).name}")
            self._render_status()
            self.update_ui()
        except Exception as e:
            messagebox.showerror(
                "Error",
                f"No pude cargar el perfil Refill:\n{filename}\n\n{e}",
            )

    def load_deposit_profile(self) -> None:
        initial_dir = (
            str(self.repo_root / "configs")
            if (self.repo_root / "configs").exists()
            else str(self.repo_root)
        )
        filename = filedialog.askopenfilename(
            title="Cargar perfil Deposit (JSON)",
            initialdir=initial_dir,
            filetypes=[
                ("JSON (.json)", "*.json"),
                ("Todos", "*.*"),
            ],
        )
        if not filename:
            return
        try:
            profile = DepositProfile.load(Path(filename))
            self.deposit_profile = profile
            self.display_json(profile.summary(), f"Deposit: {Path(filename).name}")
            self._render_status()
            self.update_ui()
        except Exception as e:
            messagebox.showerror(
                "Error",
                f"No pude cargar el perfil Deposit:\n{filename}\n\n{e}",
            )

    def _guess_initial_dir(self) -> str:
        # Prefer a useful initial directory for dialogs.
        if self.current_script:
            sr = self.scripts_root / self.current_script
            if sr.exists():
                return str(sr)
            rr = self.reports_root / self.current_script
            if rr.exists():
                return str(rr)
        if self.scripts_root.exists():
            return str(self.scripts_root)
        return str(self.repo_root)

    def open_any_file(self) -> None:
        """Permite elegir cualquier archivo, pero solo interpreta extensiones conocidas."""
        filename = filedialog.askopenfilename(
            title="Abrir archivo",
            initialdir=self._guess_initial_dir(),
            filetypes=[
                ("Waypoints (.in)", "*.in"),
                ("JSON (.json)", "*.json"),
                ("Todos", "*.*"),
            ],
        )
        if not filename:
            return

        path = Path(filename)
        ext = path.suffix.lower()

        if ext == ".in":
            # Expecting a waypoints.in-like file
            self._load_waypoints_file(path)
            return

        if ext == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                self.display_json(data, path.name)
            except Exception as e:
                messagebox.showerror("Error", f"No pude leer JSON ({path}): {e}")
            return

        messagebox.showwarning(
            "No reconocido",
            f"Extensión no reconocida: {ext}\n\n"
            "Puedo interpretar:\n- .in (waypoints)\n- .json (reportes/config)\n\n"
            "Selecciona un archivo con esas extensiones.",
        )

    def _load_waypoints_file(self, wp_file: Path) -> None:
        if not wp_file.exists():
            messagebox.showerror("Error", f"No existe el archivo:\n{wp_file}")
            return

        self.waypoints = parse_waypoints_in(wp_file)
        self.current_index = 0

        # Infer current_script if this file lives under scripts-master/<script>/
        try:
            rel = wp_file.resolve().relative_to(self.scripts_root.resolve())
            if len(rel.parts) >= 2:
                self.current_script = rel.parts[0]
        except Exception:
            # If opened outside scripts-master, keep current_script as-is.
            pass

        # Load targeting/loot config from reports if available
        self._load_reports_for_current_script()
        # Reset simulated state for a new run
        self.targeting_enabled = False
        self.loot_enabled = False

        self._render_status()
        self.draw_map()
        self.update_ui()

    def _load_reports_for_current_script(self) -> None:
        self.target_monsters = []
        self._reports_cache = {}
        if not self.current_script:
            return
        script_reports = self.reports_root / self.current_script
        if not script_reports.exists():
            return

        setup_path = script_reports / "setup_actions.json"
        if setup_path.exists():
            try:
                setup = json.loads(setup_path.read_text(encoding="utf-8", errors="replace"))
                self._reports_cache["setup_actions.json"] = setup
                tms = setup.get("target_monsters")
                if isinstance(tms, list):
                    names: List[str] = []
                    for item in tms:
                        if isinstance(item, dict):
                            n = item.get("monster") or item.get("name") or item.get("creature")
                            if isinstance(n, str) and n.strip():
                                names.append(n.strip())
                    # stable unique
                    uniq: List[str] = []
                    seen = set()
                    for n in names:
                        key = n.lower()
                        if key in seen:
                            continue
                        seen.add(key)
                        uniq.append(n)
                    self.target_monsters = uniq
            except Exception:
                pass

    def load_json_file(self, filename: str):
        if not self.current_script:
            messagebox.showwarning("Advertencia", "Primero abre un script.")
            return
        # Buscar en reports/cloudbot/<script_name>/
        reports_path = self.reports_root / self.current_script / filename
        if not reports_path.exists():
            messagebox.showerror(
                "Error",
                f"No se encontró {filename} en:\n{reports_path}\n\n"
                "Ejecuta primero el analizador:\n"
                "python tools/cloudbot/analyze_scripts.py --scripts-root scripts-master --out-dir reports/cloudbot",
            )
            return
        try:
            data = json.loads(reports_path.read_text(encoding="utf-8"))
            self.display_json(data, filename)
        except Exception as e:
            messagebox.showerror("Error", f"Error cargando {filename}: {e}")

    def load_custom_json(self):
        initial = str(self.repo_root)
        if self.current_script:
            candidate = self.reports_root / self.current_script
            if candidate.exists():
                initial = str(candidate)
        filename = filedialog.askopenfilename(
            title="Seleccionar archivo JSON",
            initialdir=initial,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not filename:
            return
        try:
            data = json.loads(Path(filename).read_text(encoding="utf-8"))
            self.display_json(data, Path(filename).name)
        except Exception as e:
            messagebox.showerror("Error", f"Error cargando archivo: {e}")

    def display_json(self, data: Any, title: str):
        self.json_text.delete(1.0, tk.END)
        self.json_text.insert(tk.END, f"=== {title} ===\n\n")
        self.json_text.insert(tk.END, json.dumps(data, indent=2, ensure_ascii=False))
        self.json_label.config(text=f"Archivo: {title}")

    def open_script(self):
        if not self.scripts_root.exists():
            messagebox.showerror("Error", f"Carpeta scripts-master no encontrada en:\n{self.scripts_root}")
            return

        script_name = filedialog.askdirectory(
            initialdir=str(self.scripts_root),
            title="Seleccionar script (carpeta)",
            mustexist=True,
        )
        if not script_name:
            return

        script_path = Path(script_name)
        if not script_path.is_dir():
            return

        wp_file = script_path / "waypoints.in"
        if not wp_file.exists():
            messagebox.showerror("Error", "No se encontró waypoints.in en la carpeta seleccionada.")
            return

        self.current_script = script_path.name
        self._load_waypoints_file(wp_file)

    def export_framework_route(self) -> None:
        """Exporta un route.json compatible con el framework (offline, sin automatización)."""
        if not self.waypoints:
            messagebox.showwarning("Advertencia", "Primero carga un script/waypoints.")
            return

        # Convertimos puntos con posición a waypoints del framework (x,y + name/action opcional)
        exported: List[dict] = []
        pending_name: Optional[str] = None
        pending_action: Optional[str] = None

        for w in self.waypoints:
            if w.kind == "label" and w.label:
                pending_name = w.label
                continue
            if w.kind == "action" and w.action:
                pending_action = w.action
                continue
            if w.pos is None:
                continue

            x, y, _z = w.pos
            entry: dict = {"x": int(x), "y": int(y)}
            if pending_name:
                entry["name"] = pending_name
            if pending_action:
                entry["action"] = pending_action
            exported.append(entry)
            pending_name = None
            pending_action = None

        if not exported:
            messagebox.showerror("Error", "No hay puntos con posición para exportar.")
            return

        default_name = "route_exported.json"
        if self.current_script:
            default_name = f"route_{self.current_script}.json"

        initial_dir = str(self.repo_root / "configs") if (self.repo_root / "configs").exists() else str(self.repo_root)
        out_path = filedialog.asksaveasfilename(
            title="Guardar ruta (framework)",
            initialdir=initial_dir,
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not out_path:
            return

        Path(out_path).write_text(json.dumps(exported, indent=2, ensure_ascii=False), encoding="utf-8")
        messagebox.showinfo("Exportado", f"Ruta exportada a:\n{out_path}")

    def create_new_json(self) -> None:
        """Crea un archivo JSON nuevo (plantilla) para relacionarlo con tu configuración."""
        initial_dir = self._guess_initial_dir()
        default_name = "custom_config.json"
        if self.current_script:
            # Prefer saving alongside reports for that script
            rr = self.reports_root / self.current_script
            if rr.exists():
                initial_dir = str(rr)
            default_name = f"custom_{self.current_script}.json"

        out_path = filedialog.asksaveasfilename(
            title="Crear JSON nuevo",
            initialdir=initial_dir,
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not out_path:
            return

        template = {
            "script": self.current_script,
            "notes": "Plantilla offline: describe cómo quieres relacionar este script con tu configuración.",
            "mapping": {
                "route_file": "configs/route.json",
                "mode": "steps",
                "comment": "Ejemplo: puedes guardar aquí ajustes/metadata sin automatizar nada.",
            },
        }
        Path(out_path).write_text(json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")
        messagebox.showinfo("Creado", f"JSON creado en:\n{out_path}")

    def draw_map(self):
        self.canvas.delete("all")
        if not self.waypoints:
            return

        # Collect positions with their original waypoint index
        positions: List[Tuple[int, int, int]] = []  # (wp_index, x, y)
        for i, w in enumerate(self.waypoints):
            if w.pos is None:
                continue
            positions.append((i, w.pos[0], w.pos[1]))

        if not positions:
            return

        # Normalize to canvas
        xs = [x for (_i, x, _y) in positions]
        ys = [y for (_i, _x, y) in positions]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        width = self.canvas.winfo_width() or 800
        height = self.canvas.winfo_height() or 600
        margin = 50

        def scale_x(x):
            return margin + (x - min_x) / (max_x - min_x + 1) * (width - 2 * margin)

        def scale_y(y):
            # Invert Y for Tibia coords (north up)
            return margin + (max_y - y) / (max_y - min_y + 1) * (height - 2 * margin)

        # Draw lines
        prev_point = None
        for wp_index, x, y in positions:
            point = (scale_x(x), scale_y(y))
            if prev_point:
                self.canvas.create_line(prev_point[0], prev_point[1], point[0], point[1], fill="blue", width=2)
            prev_point = point

            # Draw point
            color = "red" if wp_index == self.current_index else "gray"
            self.canvas.create_oval(point[0]-5, point[1]-5, point[0]+5, point[1]+5, fill=color, outline="black")

            # Label if action or label
            w = self.waypoints[wp_index]
            if w.kind in ("label", "action"):
                label_text = (w.label or w.action) or ""
                self.canvas.create_text(point[0], point[1]-10, text=label_text, font=("Arial", 8), fill="black")

    def prev_waypoint(self):
        if self.waypoints and self.current_index > 0:
            self.current_index -= 1
            self._apply_state_up_to_index(self.current_index)
            self.draw_map()
            self.update_ui()

    def next_waypoint(self):
        if self.waypoints and self.current_index < len(self.waypoints) - 1:
            self.current_index += 1
            self._apply_state_up_to_index(self.current_index)
            self.draw_map()
            self.update_ui()

    def _apply_state_up_to_index(self, index: int) -> None:
        """Recompute simulated targeting/loot state up to a given waypoint index."""
        targeting = False
        loot = False
        phase = "unknown"
        deposit_step = False
        for w in self.waypoints[: index + 1]:
            if w.kind == "label" and w.label:
                lbl = w.label.strip().lower()
                if "refill" in lbl or lbl == "refil":
                    phase = "refill"
                elif "deposit" in lbl or "depot" in lbl:
                    phase = "deposit"
                elif "hunt" in lbl or "go_hunt" in lbl or lbl == "start":
                    phase = "hunt"

            if w.kind != "action" or not w.action:
                continue
            a = w.action.strip().lower()
            if a == "target_on":
                targeting = True
            elif a == "target_off":
                targeting = False
            elif a == "loot_on":
                loot = True
            elif a == "loot_off":
                loot = False
            elif a == "refill":
                phase = "refill"
            elif a == "deposit":
                deposit_step = True
        self.targeting_enabled = targeting
        self.loot_enabled = loot
        self.route_phase = phase
        self.deposit_step = deposit_step

    def update_ui(self):
        if not self.waypoints:
            self.index_label.config(text="Waypoint: 0/0")
            self.info_label.config(text="")
            self.prev_btn.config(state=tk.DISABLED)
            self.next_btn.config(state=tk.DISABLED)
            self.simulate_btn.config(state=tk.DISABLED)
            self.reset_btn.config(state=tk.DISABLED)
            return

        self.index_label.config(text=f"Waypoint: {self.current_index + 1}/{len(self.waypoints)}")

        w = self.waypoints[self.current_index]
        info = f"Tipo: {w.kind}"
        if w.label:
            info += f" | Label: {w.label}"
        if w.action:
            info += f" | Action: {w.action}"
        if w.pos:
            info += f" | Pos: {w.pos}"

        # Show offline state (closest-to-cavebot without automation)
        info += f" | Phase: {self.route_phase}"
        info += f" | Targeting: {'ON' if self.targeting_enabled else 'OFF'}"
        info += f" | Loot: {'ON' if self.loot_enabled else 'OFF'}"
        if self.target_monsters:
            info += f" | Targets: {len(self.target_monsters)}"
        if self.quick_loot_profile is not None:
            s = self.quick_loot_profile.summary()
            info += f" | QuickLoot: {s['mode']} (A:{s['accepted_count']}, S:{s['skipped_count']})"
        if self.refill_profile is not None:
            s = self.refill_profile.summary()
            info += f" | Refill: {s['items_count']} items"
        if self.deposit_profile is not None:
            s = self.deposit_profile.summary()
            info += f" | Deposit: {s['mode']} (K:{s['keep_count']}, D:{s['deposit_count']})"
        if self.deposit_step:
            info += " | Deposit: STEP"
        self.info_label.config(text=info)

        if self.simulating:
            self.prev_btn.config(state=tk.DISABLED)
            self.next_btn.config(state=tk.DISABLED)
        else:
            self.prev_btn.config(state=tk.NORMAL if self.current_index > 0 else tk.DISABLED)
            self.next_btn.config(state=tk.NORMAL if self.current_index < len(self.waypoints) - 1 else tk.DISABLED)

        self.simulate_btn.config(state=tk.NORMAL)
        self.reset_btn.config(state=tk.NORMAL)

    def toggle_simulation(self):
        if self.simulating:
            self.stop_simulation()
        else:
            self.start_simulation()

    def start_simulation(self):
        if not self.waypoints:
            return
        self.simulating = True
        self.simulate_btn.config(text="Pausar Simulación")
        self.prev_btn.config(state=tk.DISABLED)
        self.next_btn.config(state=tk.DISABLED)
        self.reset_btn.config(state=tk.DISABLED)
        self.simulate_step()

    def stop_simulation(self):
        self.simulating = False
        self.simulate_btn.config(text="Simular Ruta")
        if self.sim_timer:
            self.after_cancel(self.sim_timer)
            self.sim_timer = None
        self.update_ui()

    def simulate_step(self):
        if not self.simulating or not self.waypoints:
            return

        # Avanzar al siguiente waypoint
        if self.current_index < len(self.waypoints) - 1:
            self.current_index += 1
            self._apply_state_up_to_index(self.current_index)
            self.draw_map()
            self.update_ui()
            # Simular tiempo basado en distancia (ej: 0.5s por unidad de distancia)
            delay = 500  # ms
            self.sim_timer = self.after(delay, self.simulate_step)
        else:
            self.stop_simulation()
            messagebox.showinfo("Simulación Completa", "Ruta completada.")

    def reset_simulation(self):
        self.current_index = 0
        self._apply_state_up_to_index(self.current_index)
        self.draw_map()
        self.update_ui()


if __name__ == "__main__":
    app = WaypointViewer()
    app.mainloop()
