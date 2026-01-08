from __future__ import annotations

import sys
import threading
from pathlib import Path


def _add_src_to_syspath() -> None:
    repo_root = Path(__file__).resolve().parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


class BotUI:
    def __init__(self) -> None:
        _add_src_to_syspath()

        try:
            import tkinter as tk
            from tkinter import messagebox
            from tkinter import ttk
        except Exception as e:
            raise SystemExit(f"Tkinter no está disponible en este entorno: {e}")

        self._tk = tk
        self._messagebox = messagebox
        self._ttk = ttk

        from main import run_bot  # import dentro para respetar sys.path
        from runtime_config import RuntimeConfig

        self._run_bot = run_bot
        self._config = RuntimeConfig()
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None

        self.root = tk.Tk()
        self.root.title("Tibia Bot Framework")
        self.root.resizable(False, False)

        # Estado general
        self.status_var = tk.StringVar(value="Detenido")

        # Configuración (en memoria por ahora)
        self.healing_enabled = tk.BooleanVar(value=False)
        self.heal_hp_below_pct = tk.IntVar(value=70)
        self.heal_mp_below_pct = tk.IntVar(value=30)
        self.heal_action = tk.StringVar(value="")

        self.cavebot_enabled = tk.BooleanVar(value=False)
        self.cavebot_route_path = tk.StringVar(value="configs/route.json")

        container = tk.Frame(self.root, padx=14, pady=14)
        container.pack(fill="both", expand=True)

        notebook = ttk.Notebook(container)
        notebook.pack(fill="both", expand=True)

        tab_control = tk.Frame(notebook)
        tab_healing = tk.Frame(notebook)
        tab_cavebot = tk.Frame(notebook)

        notebook.add(tab_control, text="Control")
        notebook.add(tab_healing, text="Healing")
        notebook.add(tab_cavebot, text="Cavebot")

        # --- TAB: Control ---
        tk.Label(tab_control, text="Estado:").grid(row=0, column=0, sticky="w")
        tk.Label(tab_control, textvariable=self.status_var, width=22, anchor="w").grid(row=0, column=1, sticky="w")

        self.start_btn = tk.Button(tab_control, text="Iniciar", width=12, command=self.start)
        self.stop_btn = tk.Button(tab_control, text="Parar", width=12, command=self.stop, state="disabled")

        self.start_btn.grid(row=1, column=0, pady=(10, 0), sticky="w")
        self.stop_btn.grid(row=1, column=1, pady=(10, 0), sticky="e")

        # --- TAB: Healing ---
        tk.Checkbutton(tab_healing, text="Habilitar healing", variable=self.healing_enabled).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )

        tk.Label(tab_healing, text="Curar si HP < (%)").grid(row=1, column=0, sticky="w", pady=(10, 0))
        tk.Spinbox(tab_healing, from_=1, to=100, textvariable=self.heal_hp_below_pct, width=6).grid(
            row=1, column=1, sticky="w", pady=(10, 0)
        )

        tk.Label(tab_healing, text="Curar si MP < (%)").grid(row=2, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(tab_healing, from_=0, to=100, textvariable=self.heal_mp_below_pct, width=6).grid(
            row=2, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(tab_healing, text="Acción (hotkey/spell)").grid(row=3, column=0, sticky="w", pady=(6, 0))
        tk.Entry(tab_healing, textvariable=self.heal_action, width=28).grid(row=3, column=1, sticky="w", pady=(6, 0))

        # --- TAB: Cavebot ---
        tk.Checkbutton(tab_cavebot, text="Habilitar cavebot", variable=self.cavebot_enabled).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )

        tk.Label(tab_cavebot, text="Ruta (JSON)").grid(row=1, column=0, sticky="w", pady=(10, 0))
        tk.Entry(tab_cavebot, textvariable=self.cavebot_route_path, width=34).grid(
            row=1, column=1, sticky="w", pady=(10, 0)
        )

        tk.Label(
            tab_cavebot,
            text="(Solo UI por ahora: no ejecuta navegación aún)",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))

        # Aplicación en tiempo real: cada cambio de UI actualiza el RuntimeConfig.
        def sync_healing(*_args):
            self._config.update_healing(
                enabled=bool(self.healing_enabled.get()),
                hp_below_pct=int(self.heal_hp_below_pct.get()),
                mp_below_pct=int(self.heal_mp_below_pct.get()),
                action=str(self.heal_action.get()),
            )

        def sync_cavebot(*_args):
            self._config.update_cavebot(
                enabled=bool(self.cavebot_enabled.get()),
                route_path=str(self.cavebot_route_path.get()),
            )

        for v in [self.healing_enabled, self.heal_hp_below_pct, self.heal_mp_below_pct, self.heal_action]:
            v.trace_add("write", sync_healing)
        for v in [self.cavebot_enabled, self.cavebot_route_path]:
            v.trace_add("write", sync_cavebot)

        # Sync inicial
        sync_healing()
        sync_cavebot()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._is_running():
            return

        self._stop_event = threading.Event()

        def _runner() -> None:
            try:
                self._run_bot(stop_event=self._stop_event, runtime_config=self._config)
            except Exception as e:
                # Si explota, reflejarlo en el UI
                self.status_var.set(f"Error: {e}")

        self._thread = threading.Thread(target=_runner, daemon=True)
        self._thread.start()

        self.status_var.set("Ejecutándose")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

    def stop(self) -> None:
        if not self._is_running():
            self.status_var.set("Detenido")
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            return

        if self._stop_event is not None:
            self._stop_event.set()

        # Esperar un poco sin congelar totalmente la UI
        self.root.after(100, self._poll_stopped)

    def _poll_stopped(self) -> None:
        if self._is_running():
            self.root.after(100, self._poll_stopped)
            return

        self.status_var.set("Detenido")
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")

    def on_close(self) -> None:
        if self._is_running():
            if not self._messagebox.askyesno("Salir", "El bot está corriendo. ¿Quieres pararlo y salir?"):
                return
            self.stop()
            # Dar un pequeño margen antes de cerrar
            self.root.after(300, self.root.destroy)
            return

        self.root.destroy()

    def run(self) -> None:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            # Permite cerrar la UI desde consola sin traceback ruidoso
            try:
                if self._is_running() and self._stop_event is not None:
                    self._stop_event.set()
            except Exception:
                pass
            try:
                self.root.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    BotUI().run()
