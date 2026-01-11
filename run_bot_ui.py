from __future__ import annotations

import os
import sys
import threading
import time
import subprocess
from pathlib import Path


def _add_src_to_syspath() -> None:
    repo_root = Path(__file__).resolve().parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


class BotUI:
    def __init__(self) -> None:
        _add_src_to_syspath()

        self._repo_root = Path(__file__).resolve().parent
        self._last_roi_sanity_dir: str | None = None
        self._last_ocr_sanity_dir: str | None = None
        self._last_anchor_sanity_dir: str | None = None

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
        self.stale_var = tk.StringVar(value="-")
        self.health_var = tk.StringVar(value="-")
        self.health_status_var = tk.StringVar(value="-")
        self.anchor_var = tk.StringVar(value="-")
        self.anchor_status_var = tk.StringVar(value="OFF")
        self.idle_var = tk.StringVar(value="-")
        self.idle_status_var = tk.StringVar(value="OFF")

        # UI-side idle tracking (derived from telemetry coords)
        self._idle_last_pos_key: tuple[int, int, int | None] | None = None
        self._idle_last_pos_change_ts: float = 0.0

        # Últimos resultados de sanity-check (persisten en UI)
        self.last_roi_summary_var = tk.StringVar(value="-")
        self.last_roi_dir_var = tk.StringVar(value="-")
        self.last_ocr_summary_var = tk.StringVar(value="-")
        self.last_ocr_dir_var = tk.StringVar(value="-")
        self.last_anchor_summary_var = tk.StringVar(value="-")
        self.last_anchor_dir_var = tk.StringVar(value="-")

        # Configuración (en memoria por ahora)
        self.healing_enabled = tk.BooleanVar(value=False)
        self.heal_hp_below_pct = tk.IntVar(value=70)
        self.heal_mp_below_pct = tk.IntVar(value=30)
        self.heal_action = tk.StringVar(value="")

        self.cavebot_enabled = tk.BooleanVar(value=False)
        self.cavebot_route_path = tk.StringVar(value="configs/route.json")

        # Simulación/overrides de señales (para cuando aún no hay detección real)
        self.sim_enabled = tk.BooleanVar(value=True)
        self.sim_paralyzed = tk.BooleanVar(value=False)
        self.sim_haste_active = tk.BooleanVar(value=False)
        self.sim_utamo_active = tk.BooleanVar(value=False)
        self.sim_hungry = tk.BooleanVar(value=False)

        # Modo asistente (sin inputs) + confirmación humana
        self.asst_enabled = tk.BooleanVar(value=True)
        self.asst_confirm = tk.BooleanVar(value=True)
        self.asst_sound = tk.BooleanVar(value=True)

        # Idle alert (anti-stuck, sin inputs). Se aplica al iniciar el bot vía env vars.
        try:
            _idle_alert_default = float(os.getenv("ASSIST_IDLE_ALERT_S", "0").strip() or "0")
        except Exception:
            _idle_alert_default = 0.0
        try:
            _idle_repeat_default = float(os.getenv("ASSIST_IDLE_REPEAT_S", "10").strip() or "10")
        except Exception:
            _idle_repeat_default = 10.0
        try:
            _ui_idle_fail_default = float(os.getenv("UI_IDLE_FAIL_S", "0").strip() or "0")
        except Exception:
            _ui_idle_fail_default = 0.0

        self.idle_alert_s = tk.DoubleVar(value=max(0.0, float(_idle_alert_default)))
        self.idle_repeat_s = tk.DoubleVar(value=max(1.0, float(_idle_repeat_default)))
        # 0 => auto (UI calcula fail como 2x warn o warn+30)
        self.ui_idle_fail_s = tk.DoubleVar(value=max(0.0, float(_ui_idle_fail_default)))

        # Replay + export JSONL
        self.replay_enabled = tk.BooleanVar(value=False)
        self.replay_interval_ms = tk.IntVar(value=2000)
        self.replay_out_dir = tk.StringVar(value=self._config.replay_snapshot().out_dir)
        self.log_enabled = tk.BooleanVar(value=False)
        self.log_interval_ms = tk.IntVar(value=250)
        self.log_out_file = tk.StringVar(value=self._config.logging_snapshot().out_file)

        # ROI config override (applied at bot start via env var)
        self.rois_config_override = tk.StringVar(value=os.getenv("ROIS_CONFIG", "").strip())

        # Telemetría (solo lectura, viene del loop)
        self.hp_text = tk.StringVar(value="?")
        self.mp_text = tk.StringVar(value="?")
        self.cap_text = tk.StringVar(value="?")
        self.signals_text = tk.StringVar(value="-")
        self.target_text = tk.StringVar(value="-")
        self.reco_text = tk.StringVar(value="-")
        self.cavebot_next_text = tk.StringVar(value="-")
        self.cavebot_wp_text = tk.StringVar(value="-")
        self.cavebot_action_text = tk.StringVar(value="-")

        container = tk.Frame(self.root, padx=14, pady=14)
        container.pack(fill="both", expand=True)

        notebook = ttk.Notebook(container)
        notebook.pack(fill="both", expand=True)

        tab_control = tk.Frame(notebook)
        tab_healing = tk.Frame(notebook)
        tab_cavebot = tk.Frame(notebook)
        tab_config = tk.Frame(notebook)

        notebook.add(tab_control, text="Control")
        notebook.add(tab_healing, text="Healing")
        notebook.add(tab_cavebot, text="Cavebot")
        notebook.add(tab_config, text="Configuración")

        # --- TAB: Control ---
        tk.Label(tab_control, text="Estado:").grid(row=0, column=0, sticky="w")
        tk.Label(tab_control, textvariable=self.status_var, width=22, anchor="w").grid(row=0, column=1, sticky="w")

        self.start_btn = tk.Button(tab_control, text="Iniciar", width=12, command=self.start)
        self.stop_btn = tk.Button(tab_control, text="Parar", width=12, command=self.stop, state="disabled")

        self.start_btn.grid(row=1, column=0, pady=(10, 0), sticky="w")
        self.stop_btn.grid(row=1, column=1, pady=(10, 0), sticky="e")

        tk.Label(tab_control, text="HP:").grid(row=2, column=0, sticky="w", pady=(10, 0))
        tk.Label(tab_control, textvariable=self.hp_text, width=22, anchor="w").grid(row=2, column=1, sticky="w", pady=(10, 0))

        tk.Label(tab_control, text="MP:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_control, textvariable=self.mp_text, width=22, anchor="w").grid(row=3, column=1, sticky="w", pady=(6, 0))

        tk.Label(tab_control, text="Cap:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_control, textvariable=self.cap_text, width=22, anchor="w").grid(row=4, column=1, sticky="w", pady=(6, 0))

        tk.Label(tab_control, text="Señales:").grid(row=5, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_control, textvariable=self.signals_text, width=40, anchor="w").grid(row=5, column=1, sticky="w", pady=(6, 0))

        tk.Label(tab_control, text="Target:").grid(row=6, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_control, textvariable=self.target_text, width=40, anchor="w").grid(row=6, column=1, sticky="w", pady=(6, 0))

        tk.Label(tab_control, text="Recomendación:").grid(row=7, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_control, textvariable=self.reco_text, width=40, anchor="w").grid(row=7, column=1, sticky="w", pady=(6, 0))

        tk.Label(tab_control, text="Waypoint:").grid(row=8, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_control, textvariable=self.cavebot_wp_text, width=40, anchor="w").grid(row=8, column=1, sticky="w", pady=(6, 0))

        tk.Label(tab_control, text="Estado stream:").grid(row=9, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_control, textvariable=self.stale_var, width=40, anchor="w").grid(row=9, column=1, sticky="w", pady=(6, 0))

        tk.Label(tab_control, text="Health:").grid(row=10, column=0, sticky="w", pady=(6, 0))
        self._health_status_label = tk.Label(tab_control, textvariable=self.health_status_var, width=8, anchor="w")
        self._health_status_label.grid(row=10, column=1, sticky="w", pady=(6, 0))
        self._health_detail_label = tk.Label(tab_control, textvariable=self.health_var, width=52, anchor="w")
        self._health_detail_label.grid(row=10, column=2, sticky="w", pady=(6, 0))

        tk.Label(tab_control, text="Anchor:").grid(row=11, column=0, sticky="w", pady=(6, 0))
        self._anchor_status_label = tk.Label(tab_control, textvariable=self.anchor_status_var, width=8, anchor="w")
        self._anchor_status_label.grid(row=11, column=1, sticky="w", pady=(6, 0))
        self._anchor_detail_label = tk.Label(tab_control, textvariable=self.anchor_var, width=52, anchor="w")
        self._anchor_detail_label.grid(row=11, column=2, sticky="w", pady=(6, 0))

        tk.Label(tab_control, text="Idle:").grid(row=12, column=0, sticky="w", pady=(6, 0))
        self._idle_status_label = tk.Label(tab_control, textvariable=self.idle_status_var, width=8, anchor="w")
        self._idle_status_label.grid(row=12, column=1, sticky="w", pady=(6, 0))
        self._idle_detail_label = tk.Label(tab_control, textvariable=self.idle_var, width=52, anchor="w")
        self._idle_detail_label.grid(row=12, column=2, sticky="w", pady=(6, 0))

        # Herramientas de precisión (no bloquean; generan artefactos en logs/)
        tk.Label(tab_control, text="").grid(row=13, column=0)  # separador simple
        tk.Label(tab_control, text="Herramientas:").grid(row=13, column=0, sticky="w", pady=(6, 0))

        def _monitor_default() -> int:
            raw = os.getenv("FORCE_MONITOR", "2").strip() or "2"
            try:
                return int(raw)
            except Exception:
                return 2

        def _run_tool_async(
            cmd: list[str],
            *,
            title: str,
            open_dir: str | None = None,
            on_complete=None,
        ) -> None:
            if not cmd:
                return

            def _extract_out_dir(output: str) -> str | None:
                try:
                    for line in (output or "").splitlines():
                        if line.strip().startswith("OUT_DIR:"):
                            return line.split(":", 1)[1].strip()
                except Exception:
                    return None
                return None

            def _format_report_summary(out_dir_path: str) -> str | None:
                try:
                    p = Path(out_dir_path) / "report.json"
                    if not p.exists():
                        return None
                    import json

                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and "summary" in data and "checks" in data:
                        # ROI sanity report
                        s = data.get("summary") or {}
                        ok = s.get("ok")
                        warn = s.get("warn")
                        fail = s.get("fail")
                        lines = [f"Summary: OK={ok} WARN={warn} FAIL={fail}"]
                        # show a few warnings/fails
                        shown = 0
                        for c in (data.get("checks") or []):
                            try:
                                status = str(c.get("status") or "")
                                if status not in {"WARN", "FAIL"}:
                                    continue
                                name = str(c.get("name") or "")
                                reason = str(c.get("reason") or "")
                                lines.append(f"{status}: {name}: {reason}")
                                shown += 1
                                if shown >= 6:
                                    break
                            except Exception:
                                continue
                        return "\n".join(lines)

                    if isinstance(data, dict) and "ocr" in data:
                        # OCR sanity report
                        ocr = data.get("ocr") or {}
                        pres = data.get("presence") or {}
                        hp = f"{ocr.get('hp_current')}/{ocr.get('hp_max')}"
                        mp = f"{ocr.get('mp_current')}/{ocr.get('mp_max')}"
                        capv = ocr.get("cap_current")
                        coords = ocr.get("coords")
                        lines = [f"HP: {hp}", f"MP: {mp}", f"Cap: {capv}"]
                        if coords is not None:
                            lines.append(f"Coords: {coords}")
                        lines.append(
                            f"Ring: {pres.get('ring_equipped')} | Amulet: {pres.get('amulet_equipped')} | Hungry: {pres.get('hungry')}"
                        )
                        return "\n".join(lines)

                    if isinstance(data, dict) and "anchor" in data:
                        # Anchor sanity report
                        a = data.get("anchor") or {}
                        name = str(a.get("roi_name") or "")
                        dx = a.get("dx_src_px")
                        dy = a.get("dy_src_px")
                        sc = a.get("score")
                        lines = [f"Anchor ROI: {name}", f"dx: {dx} | dy: {dy} | score: {sc}"]
                        return "\n".join(lines)
                except Exception:
                    return None
                return None

            def _format_report_short(out_dir_path: str) -> str | None:
                try:
                    s = _format_report_summary(out_dir_path)
                    if not s:
                        return None
                    first = s.splitlines()[0].strip()
                    return first or None
                except Exception:
                    return None

            def worker() -> None:
                try:
                    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(self._repo_root))
                    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
                    ok = proc.returncode == 0
                except Exception as e:
                    out = str(e)
                    ok = False

                tool_out_dir = _extract_out_dir(out)
                summary = _format_report_summary(tool_out_dir) if tool_out_dir else None
                summary_short = _format_report_short(tool_out_dir) if tool_out_dir else None

                def done() -> None:
                    try:
                        # Let callers persist the exact OUT_DIR for later buttons.
                        try:
                            if on_complete is not None:
                                on_complete(tool_out_dir, ok, out, summary_short)
                        except Exception:
                            pass

                        # Prefer opening the exact run folder.
                        chosen_open = tool_out_dir or open_dir
                        if chosen_open:
                            try:
                                os.makedirs(chosen_open, exist_ok=True)
                                os.startfile(os.path.abspath(chosen_open))
                            except Exception:
                                pass
                        msg = (summary or out).strip() or ("OK" if ok else "FAIL")
                        if ok:
                            self._messagebox.showinfo(title, msg)
                        else:
                            self._messagebox.showerror(title, msg)
                    except Exception:
                        pass

                try:
                    self.root.after(0, done)
                except Exception:
                    pass

            threading.Thread(target=worker, daemon=True).start()

        def run_roi_sanity_ui() -> None:
            rois_path = str(self.rois_config_override.get()).strip()
            out_dir = str(self._repo_root / "logs" / "roi_sanity_ui")
            cmd = [
                sys.executable,
                str(self._repo_root / "tools" / "roi_sanity_check.py"),
                "--monitor",
                str(_monitor_default()),
                "--out-dir",
                out_dir,
                "--save-overlay",
            ]
            if rois_path:
                cmd += ["--rois", rois_path]

            def _on_complete(tool_out_dir: str | None, ok: bool, out: str, summary_short: str | None) -> None:
                if tool_out_dir:
                    self._last_roi_sanity_dir = tool_out_dir
                    try:
                        self.last_roi_dir_var.set(str(tool_out_dir))
                        self.last_roi_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
                    except Exception:
                        pass
                    return
                # Fallback: best-effort newest folder.
                try:
                    base = Path(out_dir)
                    if not base.exists():
                        return
                    dirs = [p for p in base.iterdir() if p.is_dir()]
                    if not dirs:
                        return
                    newest = max(dirs, key=lambda p: p.stat().st_mtime)
                    self._last_roi_sanity_dir = str(newest)
                    try:
                        self.last_roi_dir_var.set(str(newest))
                        self.last_roi_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
                    except Exception:
                        pass
                except Exception:
                    pass

            _run_tool_async(cmd, title="ROI Sanity Check", open_dir=out_dir, on_complete=_on_complete)

        def run_ocr_sanity_ui() -> None:
            rois_path = str(self.rois_config_override.get()).strip()
            out_dir = str(self._repo_root / "logs" / "ocr_sanity_ui")
            cmd = [
                sys.executable,
                str(self._repo_root / "tools" / "ocr_sanity_check.py"),
                "--monitor",
                str(_monitor_default()),
                "--out-dir",
                out_dir,
                "--save-overlay",
                "--save-crops",
            ]
            if rois_path:
                cmd += ["--rois", rois_path]

            def _on_complete(tool_out_dir: str | None, ok: bool, out: str, summary_short: str | None) -> None:
                if tool_out_dir:
                    self._last_ocr_sanity_dir = tool_out_dir
                    try:
                        self.last_ocr_dir_var.set(str(tool_out_dir))
                        self.last_ocr_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
                    except Exception:
                        pass
                    return
                try:
                    base = Path(out_dir)
                    if not base.exists():
                        return
                    dirs = [p for p in base.iterdir() if p.is_dir()]
                    if not dirs:
                        return
                    newest = max(dirs, key=lambda p: p.stat().st_mtime)
                    self._last_ocr_sanity_dir = str(newest)
                    try:
                        self.last_ocr_dir_var.set(str(newest))
                        self.last_ocr_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
                    except Exception:
                        pass
                except Exception:
                    pass

            _run_tool_async(cmd, title="OCR Sanity Check", open_dir=out_dir, on_complete=_on_complete)

        def run_anchor_sanity_ui() -> None:
            rois_path = str(self.rois_config_override.get()).strip()
            out_dir = str(self._repo_root / "logs" / "anchor_sanity_ui")
            cmd = [
                sys.executable,
                str(self._repo_root / "tools" / "anchor_sanity_check.py"),
                "--monitor",
                str(_monitor_default()),
                "--out-dir",
                out_dir,
                "--save-overlay",
            ]
            if rois_path:
                cmd += ["--rois", rois_path]

            def _on_complete(tool_out_dir: str | None, ok: bool, out: str, summary_short: str | None) -> None:
                if tool_out_dir:
                    self._last_anchor_sanity_dir = tool_out_dir
                    try:
                        self.last_anchor_dir_var.set(str(tool_out_dir))
                        self.last_anchor_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
                    except Exception:
                        pass
                    return
                # Fallback: newest folder.
                try:
                    base = Path(out_dir)
                    if not base.exists():
                        return
                    dirs = [p for p in base.iterdir() if p.is_dir()]
                    if not dirs:
                        return
                    newest = max(dirs, key=lambda p: p.stat().st_mtime)
                    self._last_anchor_sanity_dir = str(newest)
                    try:
                        self.last_anchor_dir_var.set(str(newest))
                        self.last_anchor_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
                    except Exception:
                        pass
                except Exception:
                    pass

            _run_tool_async(cmd, title="Anchor Sanity Check", open_dir=out_dir, on_complete=_on_complete)

        def run_anchor_setup_ui() -> None:
            # Interactive: lets you select a stable on-screen anchor and writes it into the ROIs config.
            rois_path = str(self.rois_config_override.get()).strip()
            if not rois_path:
                # If no override is selected, create_anchor_template will pick by resolution.
                rois_path = ""

            # Default template location (repo-relative)
            out_template = str(Path("data") / "anchors" / "hud_anchor.png")

            cmd = [
                sys.executable,
                str(self._repo_root / "tools" / "create_anchor_template.py"),
                "--monitor",
                str(_monitor_default()),
                "--base",
                "frame",
                "--out",
                out_template,
                "--write",
            ]

            # If user selected a specific ROIs file, we want to write into that exact file.
            # The tool currently chooses by resolution, so we pass via env ROIS_CONFIG.
            try:
                if rois_path:
                    os.environ["ROIS_CONFIG"] = rois_path
            except Exception:
                pass

            _run_tool_async(cmd, title="Anchor Setup", open_dir=str(self._repo_root / "data" / "anchors"))

        def _open_last_artifact(which: str, kind: str) -> None:
            try:
                if kind == "roi":
                    last_dir = self._last_roi_sanity_dir
                elif kind == "ocr":
                    last_dir = self._last_ocr_sanity_dir
                else:
                    last_dir = self._last_anchor_sanity_dir
                if not last_dir:
                    self._messagebox.showinfo("Info", "Aún no hay un run reciente.")
                    return
                p = Path(last_dir) / ("overlay.png" if which == "overlay" else "report.json")
                if not p.exists():
                    self._messagebox.showinfo("Info", f"No existe: {p}")
                    return
                os.startfile(os.path.abspath(str(p)))
            except Exception:
                pass

        tk.Button(tab_control, text="ROI sanity", width=12, command=run_roi_sanity_ui).grid(
            row=14, column=1, sticky="w", pady=(6, 0)
        )
        tk.Button(tab_control, text="OCR test", width=12, command=run_ocr_sanity_ui).grid(
            row=14, column=2, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Button(tab_control, text="Anchor test", width=12, command=run_anchor_sanity_ui).grid(
            row=14, column=3, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Button(tab_control, text="Anchor setup", width=12, command=run_anchor_setup_ui).grid(
            row=14, column=4, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Button(tab_control, text="ROI overlay", width=12, command=lambda: _open_last_artifact("overlay", "roi")).grid(
            row=15, column=1, sticky="w", pady=(6, 0)
        )
        tk.Button(tab_control, text="ROI report", width=12, command=lambda: _open_last_artifact("report", "roi")).grid(
            row=15, column=2, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Button(tab_control, text="Anchor overlay", width=12, command=lambda: _open_last_artifact("overlay", "anchor")).grid(
            row=15, column=3, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Button(tab_control, text="OCR overlay", width=12, command=lambda: _open_last_artifact("overlay", "ocr")).grid(
            row=16, column=1, sticky="w", pady=(6, 0)
        )
        tk.Button(tab_control, text="OCR report", width=12, command=lambda: _open_last_artifact("report", "ocr")).grid(
            row=16, column=2, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Button(tab_control, text="Anchor report", width=12, command=lambda: _open_last_artifact("report", "anchor")).grid(
            row=16, column=3, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        # Últimos resultados
        tk.Label(tab_control, text="").grid(row=17, column=0)
        tk.Label(tab_control, text="Último ROI:").grid(row=18, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_control, textvariable=self.last_roi_summary_var, width=22, anchor="w").grid(
            row=18, column=1, sticky="w", pady=(6, 0)
        )
        tk.Label(tab_control, textvariable=self.last_roi_dir_var, width=52, anchor="w").grid(
            row=19, column=1, columnspan=3, sticky="w"
        )

        tk.Label(tab_control, text="Último OCR:").grid(row=20, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_control, textvariable=self.last_ocr_summary_var, width=22, anchor="w").grid(
            row=20, column=1, sticky="w", pady=(6, 0)
        )
        tk.Label(tab_control, textvariable=self.last_ocr_dir_var, width=52, anchor="w").grid(
            row=21, column=1, columnspan=3, sticky="w"
        )

        tk.Label(tab_control, text="Último Anchor:").grid(row=22, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_control, textvariable=self.last_anchor_summary_var, width=22, anchor="w").grid(
            row=22, column=1, sticky="w", pady=(6, 0)
        )
        tk.Label(tab_control, textvariable=self.last_anchor_dir_var, width=52, anchor="w").grid(
            row=23, column=1, columnspan=3, sticky="w"
        )

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

        tk.Label(tab_cavebot, text="Próxima acción:").grid(row=3, column=0, sticky="w", pady=(12, 0))
        tk.Label(tab_cavebot, textvariable=self.cavebot_next_text, width=40, anchor="w").grid(
            row=3, column=1, sticky="w", pady=(12, 0)
        )

        tk.Label(tab_cavebot, text="Waypoint:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_cavebot, textvariable=self.cavebot_wp_text, width=40, anchor="w").grid(
            row=4, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(tab_cavebot, text="Action:").grid(row=5, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_cavebot, textvariable=self.cavebot_action_text, width=40, anchor="w").grid(
            row=5, column=1, sticky="w", pady=(6, 0)
        )

        def request_advance() -> None:
            try:
                self._config.request_advance()
            except Exception:
                pass

        tk.Button(tab_cavebot, text="Marcar como ejecutado", width=18, command=request_advance).grid(
            row=6, column=0, sticky="w", pady=(10, 0)
        )
        tk.Button(tab_cavebot, text="Siguiente acción", width=14, command=request_advance).grid(
            row=6, column=1, sticky="w", pady=(10, 0)
        )

        # --- TAB: Configuración ---
        tk.Checkbutton(tab_config, text="Habilitar simulación de señales", variable=self.sim_enabled).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        tk.Checkbutton(tab_config, text="Paralyzed", variable=self.sim_paralyzed).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        tk.Checkbutton(tab_config, text="Haste activo", variable=self.sim_haste_active).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        tk.Checkbutton(tab_config, text="Utamo activo", variable=self.sim_utamo_active).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        tk.Checkbutton(tab_config, text="Hungry", variable=self.sim_hungry).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

        tk.Label(tab_config, text="").grid(row=5, column=0)  # separador simple

        tk.Label(tab_config, text="ROIs config (override)").grid(row=6, column=0, sticky="w", pady=(10, 0))
        tk.Entry(tab_config, textvariable=self.rois_config_override, width=34).grid(
            row=6, column=1, sticky="w", pady=(10, 0)
        )

        def browse_rois() -> None:
            try:
                from tkinter import filedialog

                path = filedialog.askopenfilename(
                    title="Selecciona ROIs profile JSON",
                    initialdir=str((Path(__file__).resolve().parent / "configs")),
                    filetypes=[("JSON", "*.json"), ("All files", "*")],
                )
                if path:
                    self.rois_config_override.set(path)
            except Exception:
                pass

        tk.Button(tab_config, text="Browse", width=8, command=browse_rois).grid(
            row=6, column=2, sticky="w", padx=(8, 0), pady=(10, 0)
        )

        tk.Label(
            tab_config,
            text="(Se aplica al iniciar el bot; requiere reinicio)",
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(4, 0))

        tk.Label(tab_config, text="").grid(row=8, column=0)  # separador simple

        tk.Checkbutton(tab_config, text="Modo asistente (sin inputs)", variable=self.asst_enabled).grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        tk.Checkbutton(tab_config, text="Confirmación humana (cavebot)", variable=self.asst_confirm).grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        tk.Checkbutton(tab_config, text="Alertas sonoras", variable=self.asst_sound).grid(
            row=11, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

        tk.Label(tab_config, text="").grid(row=12, column=0)  # separador simple

        tk.Checkbutton(tab_config, text="Guardar replays (ROI+JSON)", variable=self.replay_enabled).grid(
            row=13, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        tk.Label(tab_config, text="Replay interval (ms)").grid(row=14, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(tab_config, from_=100, to=60000, increment=100, textvariable=self.replay_interval_ms, width=8).grid(
            row=14, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(tab_config, text="Replay out_dir").grid(row=15, column=0, sticky="w", pady=(6, 0))
        tk.Entry(tab_config, textvariable=self.replay_out_dir, width=34).grid(
            row=15, column=1, sticky="w", pady=(6, 0)
        )

        def open_replay_dir() -> None:
            try:
                p = str(self.replay_out_dir.get()).strip() or "logs/replay"
                os.makedirs(p, exist_ok=True)
                os.startfile(os.path.abspath(p))
            except Exception:
                pass

        def force_replay_snapshot() -> None:
            try:
                self._config.request_replay_snapshot()
            except Exception:
                pass

        tk.Button(tab_config, text="Abrir carpeta", width=12, command=open_replay_dir).grid(
            row=15, column=2, sticky="w", padx=(8, 0)
        )
        tk.Button(tab_config, text="Snapshot ahora", width=12, command=force_replay_snapshot).grid(
            row=13, column=2, sticky="w", padx=(8, 0)
        )

        tk.Label(tab_config, text="").grid(row=16, column=0)  # separador simple

        tk.Checkbutton(tab_config, text="Exportar telemetría JSONL", variable=self.log_enabled).grid(
            row=17, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        tk.Label(tab_config, text="Log interval (ms)").grid(row=18, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(tab_config, from_=100, to=60000, increment=50, textvariable=self.log_interval_ms, width=8).grid(
            row=18, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(tab_config, text="Log out_file").grid(row=19, column=0, sticky="w", pady=(6, 0))
        tk.Entry(tab_config, textvariable=self.log_out_file, width=34).grid(
            row=19, column=1, sticky="w", pady=(6, 0)
        )

        def open_log_parent() -> None:
            try:
                p = str(self.log_out_file.get()).strip() or "logs/telemetry.jsonl"
                parent = os.path.dirname(p) or "."
                os.makedirs(parent, exist_ok=True)
                os.startfile(os.path.abspath(parent))
            except Exception:
                pass

        def open_log_file() -> None:
            try:
                p = str(self.log_out_file.get()).strip() or "logs/telemetry.jsonl"
                parent = os.path.dirname(p) or "."
                os.makedirs(parent, exist_ok=True)
                # crear si no existe para que startfile funcione
                if not os.path.exists(p):
                    with open(p, "a", encoding="utf-8"):
                        pass
                os.startfile(os.path.abspath(p))
            except Exception:
                pass

        tk.Button(tab_config, text="Abrir carpeta", width=12, command=open_log_parent).grid(
            row=19, column=2, sticky="w", padx=(8, 0)
        )
        tk.Button(tab_config, text="Abrir archivo", width=12, command=open_log_file).grid(
            row=18, column=2, sticky="w", padx=(8, 0)
        )

        # --- Idle alert (UI + core) ---
        tk.Label(tab_config, text="").grid(row=20, column=0)  # separador simple
        tk.Label(tab_config, text="Idle (alerta / anti-stuck, sin inputs)").grid(
            row=21, column=0, columnspan=3, sticky="w", pady=(10, 0)
        )
        tk.Label(tab_config, text="WARN si idle ≥ (s)").grid(row=22, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(tab_config, from_=0, to=3600, increment=5, textvariable=self.idle_alert_s, width=8).grid(
            row=22, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(tab_config, text="FAIL si idle ≥ (s)").grid(row=23, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(tab_config, from_=0, to=7200, increment=10, textvariable=self.ui_idle_fail_s, width=8).grid(
            row=23, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(tab_config, text="Repetir alerta cada (s)").grid(row=24, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(tab_config, from_=1, to=600, increment=1, textvariable=self.idle_repeat_s, width=8).grid(
            row=24, column=1, sticky="w", pady=(6, 0)
        )
        tk.Label(
            tab_config,
            text="(0 desactiva. Se aplica al iniciar el bot; requiere reinicio)",
        ).grid(row=25, column=0, columnspan=3, sticky="w", pady=(4, 0))

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

        def sync_simulation(*_args):
            self._config.update_simulation(
                enabled=bool(self.sim_enabled.get()),
                paralyzed=bool(self.sim_paralyzed.get()),
                haste_active=bool(self.sim_haste_active.get()),
                utamo_active=bool(self.sim_utamo_active.get()),
                hungry=bool(self.sim_hungry.get()),
            )

        def sync_assistant(*_args):
            self._config.update_assistant(
                enabled=bool(self.asst_enabled.get()),
                confirm_actions=bool(self.asst_confirm.get()),
                sound_alerts=bool(self.asst_sound.get()),
            )

        def sync_replay_and_logging(*_args):
            self._config.update_replay(
                enabled=bool(self.replay_enabled.get()),
                interval_ms=int(self.replay_interval_ms.get()),
                out_dir=str(self.replay_out_dir.get()),
            )
            self._config.update_logging(
                enabled=bool(self.log_enabled.get()),
                interval_ms=int(self.log_interval_ms.get()),
                out_file=str(self.log_out_file.get()),
            )

        for v in [self.healing_enabled, self.heal_hp_below_pct, self.heal_mp_below_pct, self.heal_action]:
            v.trace_add("write", sync_healing)
        for v in [self.cavebot_enabled, self.cavebot_route_path]:
            v.trace_add("write", sync_cavebot)
        for v in [self.sim_enabled, self.sim_paralyzed, self.sim_haste_active, self.sim_utamo_active, self.sim_hungry]:
            v.trace_add("write", sync_simulation)
        for v in [self.asst_enabled, self.asst_confirm, self.asst_sound]:
            v.trace_add("write", sync_assistant)
        for v in [
            self.replay_enabled,
            self.replay_interval_ms,
            self.replay_out_dir,
            self.log_enabled,
            self.log_interval_ms,
            self.log_out_file,
        ]:
            v.trace_add("write", sync_replay_and_logging)

        # Sync inicial
        sync_healing()
        sync_cavebot()
        sync_simulation()
        sync_assistant()
        sync_replay_and_logging()

        def poll_telemetry() -> None:
            try:
                tel = self._config.telemetry_snapshot()
                health = self._config.health_snapshot()
                hp_str = "?"
                mp_str = "?"
                cap_str = "?"
                if tel.hp_current is not None and tel.hp_max is not None:
                    if tel.hp_pct is not None:
                        hp_str = f"{tel.hp_current}/{tel.hp_max} ({tel.hp_pct:.1f}%)"
                    else:
                        hp_str = f"{tel.hp_current}/{tel.hp_max}"
                if tel.mp_current is not None and tel.mp_max is not None:
                    if tel.mp_pct is not None:
                        mp_str = f"{tel.mp_current}/{tel.mp_max} ({tel.mp_pct:.1f}%)"
                    else:
                        mp_str = f"{tel.mp_current}/{tel.mp_max}"

                if tel.cap_current is not None:
                    cap_str = f"{tel.cap_current}"

                self.hp_text.set(hp_str)
                self.mp_text.set(mp_str)
                self.cap_text.set(cap_str)

                parts = []
                if tel.low_hp:
                    parts.append("low_hp")
                if tel.low_mp:
                    parts.append("low_mp")
                if tel.low_cap:
                    parts.append("low_cap")
                if getattr(tel, "ring_equipped", None) is True:
                    parts.append("ring")
                if getattr(tel, "amulet_equipped", None) is True:
                    parts.append("amulet")
                if tel.paralyzed:
                    parts.append("paralyzed")
                if tel.haste_active:
                    parts.append("haste")
                if tel.utamo_active:
                    parts.append("utamo")
                if tel.hungry:
                    parts.append("hungry")

                self.signals_text.set(", ".join(parts) if parts else "-")

                self.target_text.set(tel.target or "-")
                self.reco_text.set(tel.recommendation or "-")
                self.cavebot_next_text.set(tel.cavebot_next or "-")
                self.cavebot_wp_text.set(tel.cavebot_waypoint or "-")
                self.cavebot_action_text.set(tel.cavebot_action or "-")

                now = time.time()
                if tel.ts and tel.ts > 0:
                    age = max(0.0, now - float(tel.ts))
                    if age >= 2.0:
                        self.stale_var.set(f"stale {age:.1f}s")
                    else:
                        self.stale_var.set("OK")
                else:
                    self.stale_var.set("-")

                # Health line (watchdog snapshot)
                try:
                    # Thresholds (env-tunable)
                    try:
                        warn_gs_age_s = float(os.getenv("UI_HEALTH_WARN_GS_AGE_S", "3").strip() or "3")
                    except Exception:
                        warn_gs_age_s = 3.0
                    try:
                        fail_gs_age_s = float(os.getenv("UI_HEALTH_FAIL_GS_AGE_S", "8").strip() or "8")
                    except Exception:
                        fail_gs_age_s = 8.0
                    try:
                        warn_frame_age_s = float(os.getenv("UI_HEALTH_WARN_FRAME_AGE_S", "3").strip() or "3")
                    except Exception:
                        warn_frame_age_s = 3.0
                    try:
                        fail_frame_age_s = float(os.getenv("UI_HEALTH_FAIL_FRAME_AGE_S", "8").strip() or "8")
                    except Exception:
                        fail_frame_age_s = 8.0
                    warn_on_drops = os.getenv("UI_HEALTH_WARN_ON_DROPS", "1").strip().lower() not in {"0", "false", "no"}

                    parts_h = []
                    if health.frame_age_s is not None:
                        parts_h.append(f"frame_age {float(health.frame_age_s):.1f}s")
                    if health.gs_age_s is not None:
                        parts_h.append(f"gs_age {float(health.gs_age_s):.1f}s")
                    if health.q_frame is not None or health.q_gs is not None:
                        parts_h.append(f"q f={health.q_frame} gs={health.q_gs}")
                    if health.drop_frame_queue is not None or health.drop_gs_queue is not None:
                        parts_h.append(f"drops f={health.drop_frame_queue} gs={health.drop_gs_queue}")
                    if health.capture_ms_last is not None or health.vision_ms_last is not None or health.decision_ms_last is not None:
                        parts_h.append(
                            f"ms cap={0 if health.capture_ms_last is None else float(health.capture_ms_last):.0f}"
                            f" vis={0 if health.vision_ms_last is None else float(health.vision_ms_last):.0f}"
                            f" dec={0 if health.decision_ms_last is None else float(health.decision_ms_last):.0f}"
                        )
                    try:
                        if (
                            getattr(health, "roi_offset_dx_px", None) is not None
                            or getattr(health, "roi_offset_dy_px", None) is not None
                            or getattr(health, "roi_offset_score", None) is not None
                        ):
                            dx = getattr(health, "roi_offset_dx_px", None)
                            dy = getattr(health, "roi_offset_dy_px", None)
                            sc = getattr(health, "roi_offset_score", None)
                            parts_h.append(
                                "roi_off "
                                f"dx={0 if dx is None else float(dx):.0f} "
                                f"dy={0 if dy is None else float(dy):.0f} "
                                f"score={0 if sc is None else float(sc):.2f}"
                            )
                    except Exception:
                        pass
                    if health.dead_threads:
                        parts_h.append(f"dead {health.dead_threads}")
                    if health.warn:
                        parts_h.append(str(health.warn))
                    self.health_var.set(" | ".join(parts_h) if parts_h else "-")

                    # Semaphore status
                    status = "OK"
                    if health.dead_threads:
                        status = "FAIL"
                    else:
                        try:
                            fa = float(health.frame_age_s) if health.frame_age_s is not None else None
                            ga = float(health.gs_age_s) if health.gs_age_s is not None else None
                        except Exception:
                            fa, ga = None, None

                        if (ga is not None and ga >= fail_gs_age_s) or (fa is not None and fa >= fail_frame_age_s):
                            status = "FAIL"
                        elif (ga is not None and ga >= warn_gs_age_s) or (fa is not None and fa >= warn_frame_age_s):
                            status = "WARN"
                        elif warn_on_drops:
                            try:
                                if int(health.drop_frame_queue or 0) > 0 or int(health.drop_gs_queue or 0) > 0:
                                    status = "WARN"
                            except Exception:
                                pass
                        elif health.warn:
                            status = "WARN"

                    self.health_status_var.set(status)
                    try:
                        if status == "OK":
                            self._health_status_label.config(fg="#1b7f3a")
                        elif status == "WARN":
                            self._health_status_label.config(fg="#b26a00")
                        else:
                            self._health_status_label.config(fg="#b00020")
                    except Exception:
                        pass

                    # Anchor status (ROI auto-alignment)
                    try:
                        try:
                            warn_score = float(os.getenv("UI_ANCHOR_WARN_SCORE", "0.60").strip() or "0.60")
                        except Exception:
                            warn_score = 0.60
                        try:
                            fail_score = float(os.getenv("UI_ANCHOR_FAIL_SCORE", "0.48").strip() or "0.48")
                        except Exception:
                            fail_score = 0.48

                        dx = getattr(health, "roi_offset_dx_px", None)
                        dy = getattr(health, "roi_offset_dy_px", None)
                        sc = getattr(health, "roi_offset_score", None)

                        if dx is None and dy is None and sc is None:
                            self.anchor_status_var.set("OFF")
                            self.anchor_var.set("-")
                            try:
                                self._anchor_status_label.config(fg="#666666")
                            except Exception:
                                pass
                        else:
                            # Show numeric values when available.
                            try:
                                dx_s = "?" if dx is None else f"{float(dx):.0f}"
                            except Exception:
                                dx_s = "?"
                            try:
                                dy_s = "?" if dy is None else f"{float(dy):.0f}"
                            except Exception:
                                dy_s = "?"
                            try:
                                sc_s = "?" if sc is None else f"{float(sc):.2f}"
                            except Exception:
                                sc_s = "?"
                            self.anchor_var.set(f"dx={dx_s} dy={dy_s} score={sc_s}")

                            a_status = "OK"
                            try:
                                sc_f = float(sc) if sc is not None else None
                            except Exception:
                                sc_f = None
                            if sc_f is None:
                                a_status = "ON"
                            elif sc_f < fail_score:
                                a_status = "FAIL"
                            elif sc_f < warn_score:
                                a_status = "WARN"

                            self.anchor_status_var.set(a_status)
                            try:
                                if a_status == "OK":
                                    self._anchor_status_label.config(fg="#1b7f3a")
                                elif a_status == "WARN":
                                    self._anchor_status_label.config(fg="#b26a00")
                                elif a_status == "FAIL":
                                    self._anchor_status_label.config(fg="#b00020")
                                else:
                                    self._anchor_status_label.config(fg="#1f6feb")
                            except Exception:
                                pass
                    except Exception:
                        self.anchor_status_var.set("-")
                        self.anchor_var.set("-")

                    # Idle status (UI-side, derived from telemetry coords)
                    try:
                        try:
                            idle_warn_s = float(
                                (os.getenv("UI_IDLE_WARN_S", "").strip() or os.getenv("ASSIST_IDLE_ALERT_S", "0")).strip()
                                or "0"
                            )
                        except Exception:
                            idle_warn_s = 0.0
                        idle_warn_s = max(0.0, float(idle_warn_s))

                        try:
                            idle_fail_raw = os.getenv("UI_IDLE_FAIL_S", "").strip()
                            idle_fail_s = float(idle_fail_raw) if idle_fail_raw else (max(idle_warn_s * 2.0, idle_warn_s + 30.0) if idle_warn_s > 0 else 0.0)
                        except Exception:
                            idle_fail_s = max(idle_warn_s * 2.0, idle_warn_s + 30.0) if idle_warn_s > 0 else 0.0
                        idle_fail_s = max(0.0, float(idle_fail_s))

                        if idle_warn_s <= 0.0:
                            self.idle_status_var.set("OFF")
                            self.idle_var.set("-")
                            try:
                                self._idle_status_label.config(fg="#666666")
                            except Exception:
                                pass
                        else:
                            gx = getattr(tel, "pos_x", None)
                            gy = getattr(tel, "pos_y", None)
                            gz = getattr(tel, "pos_z", None)

                            if gx is None or gy is None:
                                self.idle_status_var.set("-")
                                self.idle_var.set("no coords")
                                try:
                                    self._idle_status_label.config(fg="#666666")
                                except Exception:
                                    pass
                            else:
                                try:
                                    key = (int(gx), int(gy), int(gz) if gz is not None else None)
                                except Exception:
                                    key = None

                                if key is None:
                                    self.idle_status_var.set("-")
                                    self.idle_var.set("no coords")
                                    try:
                                        self._idle_status_label.config(fg="#666666")
                                    except Exception:
                                        pass
                                else:
                                    if self._idle_last_pos_key is None:
                                        self._idle_last_pos_key = key
                                        self._idle_last_pos_change_ts = now
                                    elif key != self._idle_last_pos_key:
                                        self._idle_last_pos_key = key
                                        self._idle_last_pos_change_ts = now
                                    idle_for = max(0.0, now - float(self._idle_last_pos_change_ts or now))

                                    z_s = "" if key[2] is None else f",{key[2]}"
                                    self.idle_var.set(f"pos={key[0]},{key[1]}{z_s} | idle {idle_for:.0f}s")

                                    i_status = "OK" if idle_for < idle_warn_s else "WARN"
                                    if idle_fail_s > 0.0 and idle_for >= idle_fail_s:
                                        i_status = "FAIL"
                                    self.idle_status_var.set(i_status)
                                    try:
                                        if i_status == "OK":
                                            self._idle_status_label.config(fg="#1b7f3a")
                                        elif i_status == "WARN":
                                            self._idle_status_label.config(fg="#b26a00")
                                        else:
                                            self._idle_status_label.config(fg="#b00020")
                                    except Exception:
                                        pass
                    except Exception:
                        try:
                            self.idle_status_var.set("-")
                            self.idle_var.set("-")
                            try:
                                self._idle_status_label.config(fg="#666666")
                            except Exception:
                                pass
                        except Exception:
                            pass
                except Exception:
                    self.health_var.set("-")
                    try:
                        self.health_status_var.set("-")
                    except Exception:
                        pass
            except Exception:
                pass
            self.root.after(250, poll_telemetry)

        poll_telemetry()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _reset_idle_ui(self) -> None:
        try:
            self._idle_last_pos_key = None
            self._idle_last_pos_change_ts = 0.0
            self.idle_status_var.set("OFF")
            self.idle_var.set("-")
            try:
                self._idle_status_label.config(fg="#666666")
            except Exception:
                pass
        except Exception:
            pass

    def _is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._is_running():
            return

        # Reset UI idle tracking for this run.
        self._reset_idle_ui()

        # Apply ROIs override for this bot run (used by src/main.py:load_roi_config).
        try:
            rois_path = str(self.rois_config_override.get()).strip()
            if rois_path:
                os.environ["ROIS_CONFIG"] = rois_path
            else:
                os.environ.pop("ROIS_CONFIG", None)
        except Exception:
            pass

        # Apply idle-alert settings for this bot run (used by src/main.py decision loop, and UI idle line).
        try:
            idle_warn = float(self.idle_alert_s.get())
        except Exception:
            idle_warn = 0.0
        try:
            idle_rep = float(self.idle_repeat_s.get())
        except Exception:
            idle_rep = 10.0
        try:
            idle_fail = float(self.ui_idle_fail_s.get())
        except Exception:
            idle_fail = 0.0

        try:
            idle_warn = max(0.0, float(idle_warn))
            idle_rep = max(1.0, float(idle_rep))
            idle_fail = max(0.0, float(idle_fail))

            os.environ["ASSIST_IDLE_ALERT_S"] = str(idle_warn)
            os.environ["ASSIST_IDLE_REPEAT_S"] = str(idle_rep)

            # UI-specific thresholds (optional overrides)
            if idle_warn > 0.0:
                os.environ["UI_IDLE_WARN_S"] = str(idle_warn)
            else:
                os.environ.pop("UI_IDLE_WARN_S", None)

            if idle_fail > 0.0:
                os.environ["UI_IDLE_FAIL_S"] = str(idle_fail)
            else:
                os.environ.pop("UI_IDLE_FAIL_S", None)
        except Exception:
            pass

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
            self._reset_idle_ui()
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
        self._reset_idle_ui()

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
