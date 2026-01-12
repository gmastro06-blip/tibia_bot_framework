from __future__ import annotations

import json
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
        self._last_coords_sanity_dir: str | None = None

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

        # Soak session tracking (UI-only, helps opening the latest artifacts).
        self._last_soak_run_id: str | None = None
        self.soak_run_id_var = None

        self.root = tk.Tk()
        self.root.title("Tibia Bot Framework")
        self.root.resizable(False, False)

        # Soak run id (requires a root window)
        self.soak_run_id_var = tk.StringVar(master=self.root, value="-")

        # Estado general
        self.status_var = tk.StringVar(value="Detenido")
        self.stale_var = tk.StringVar(value="-")
        self.health_var = tk.StringVar(value="-")
        self.health_status_var = tk.StringVar(value="-")
        self.anchor_var = tk.StringVar(value="-")
        self.anchor_status_var = tk.StringVar(value="OFF")
        self.idle_var = tk.StringVar(value="-")
        self.idle_status_var = tk.StringVar(value="OFF")
        self.stuck_var = tk.StringVar(value="-")
        self.stuck_status_var = tk.StringVar(value="OFF")

        # UI-side idle tracking (derived from telemetry coords)
        self._idle_last_pos_key: tuple[int, int, int | None] | None = None
        self._idle_last_pos_change_ts: float = 0.0

        # UI operator panel (event stream)
        self._event_lines: list[str] = []
        self._event_max_lines = 30
        self._last_event_target: str = ""
        self._last_event_reco: str = ""
        self._last_event_action_req: str = ""
        self._last_event_action_committed: bool | None = None
        self._last_event_wp: str = ""
        self._last_event_wp_action: str = ""
        self._last_event_health_status: str = ""
        self._last_event_idle_status: str = ""
        self._last_event_note: str = ""
        self._last_event_coords_status: str = ""
        self._last_event_stuck_reason: str = ""
        self._last_event_step_idx: int | None = None
        self._last_event_step_total: int | None = None

        # Route checklist state (UI-only)
        self._route_items: list[dict] = []
        self._route_loaded_from: str = ""
        self._route_current_index: int | None = None

        # Últimos resultados de sanity-check (persisten en UI)
        self.last_roi_summary_var = tk.StringVar(value="-")
        self.last_roi_dir_var = tk.StringVar(value="-")
        self.last_ocr_summary_var = tk.StringVar(value="-")
        self.last_ocr_dir_var = tk.StringVar(value="-")
        self.last_coords_summary_var = tk.StringVar(value="-")
        self.last_coords_dir_var = tk.StringVar(value="-")
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

        # Overlay exporter (env-based; applied at bot start)
        self.overlay_enabled = tk.BooleanVar(
            value=os.getenv("OVERLAY_ENABLED", "").strip().lower() in {"1", "true", "yes"}
        )
        try:
            _overlay_interval_default = float(os.getenv("OVERLAY_INTERVAL_S", "1.0").strip() or "1.0")
        except Exception:
            _overlay_interval_default = 1.0
        self.overlay_interval_s = tk.DoubleVar(value=max(0.1, float(_overlay_interval_default)))
        self.overlay_out_dir = tk.StringVar(
            value=os.getenv("OVERLAY_OUT_DIR", "logs/debug_overlay").strip() or "logs/debug_overlay"
        )
        self.overlay_tile_grid = tk.BooleanVar(
            value=os.getenv("OVERLAY_TILE_GRID", "1").strip().lower() not in {"0", "false", "no"}
        )
        try:
            _tile_raw = os.getenv("OVERLAY_TILE_PX", "").strip() or os.getenv("TIBIA_TILE_PX", "32").strip() or "32"
            _tile_default = int(float(_tile_raw))
        except Exception:
            _tile_default = 32
        self.overlay_tile_px = tk.IntVar(value=max(4, min(128, int(_tile_default))))
        self.overlay_rois = tk.StringVar(
            value=os.getenv(
                "OVERLAY_ROIS",
                "coords_ocr,minimap_content,hp_top_ocr,mp_top_ocr,hp_low_bar,mp_low_bar,states_icons,equipment_slots,battlelist_rows",
            ).strip()
        )
        self.overlay_preset = tk.StringVar(value="Custom")
        self.telemetry_preset = tk.StringVar(value="Custom")

        # ROI config override (applied at bot start via env var)
        self.rois_config_override = tk.StringVar(value=os.getenv("ROIS_CONFIG", "").strip())

        # UI settings persistence (best-effort): load last overlay settings.
        try:
            self._load_ui_settings()
        except Exception:
            pass

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

        tk.Label(tab_control, text="Stuck:").grid(row=13, column=0, sticky="w", pady=(6, 0))
        self._stuck_status_label = tk.Label(tab_control, textvariable=self.stuck_status_var, width=8, anchor="w")
        self._stuck_status_label.grid(row=13, column=1, sticky="w", pady=(6, 0))
        self._stuck_detail_label = tk.Label(tab_control, textvariable=self.stuck_var, width=52, anchor="w")
        self._stuck_detail_label.grid(row=13, column=2, sticky="w", pady=(6, 0))

        # Panel operador: últimos eventos
        tk.Label(tab_control, text="Eventos (últimos):").grid(row=14, column=0, sticky="w", pady=(6, 0))
        self._events_text = tk.Text(tab_control, height=8, width=80, wrap="none")
        try:
            self._events_text.configure(state="disabled")
        except Exception:
            pass
        self._events_text.grid(row=15, column=0, columnspan=5, sticky="w", pady=(4, 0))

        # Herramientas de precisión (no bloquean; generan artefactos en logs/)
        tk.Label(tab_control, text="").grid(row=16, column=0)  # separador simple
        tk.Label(tab_control, text="Herramientas:").grid(row=16, column=0, sticky="w", pady=(6, 0))

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

                    if isinstance(data, dict) and "coords_sanity" in data:
                        cs = data.get("coords_sanity") or {}
                        ok = cs.get("ok")
                        samples = cs.get("samples")
                        ok_rate = cs.get("ok_rate")
                        max_jump = cs.get("max_jump")
                        warn_jumps = cs.get("warn_jumps")
                        fail_jumps = cs.get("fail_jumps")
                        lines = [
                            f"Coords: ok={ok}/{samples} ({0 if ok_rate is None else float(ok_rate):.0%})",
                            f"Jumps: max={max_jump} warn={warn_jumps} fail={fail_jumps}",
                        ]
                        last = cs.get("last_coords")
                        if last is not None:
                            lines.append(f"Last: {last}")
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

        def run_coords_sanity_ui() -> None:
            rois_path = str(self.rois_config_override.get()).strip()
            out_dir = str(self._repo_root / "logs" / "coords_sanity_ui")
            cmd = [
                sys.executable,
                str(self._repo_root / "tools" / "coords_sanity_check.py"),
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
                    self._last_coords_sanity_dir = tool_out_dir
                    try:
                        self.last_coords_dir_var.set(str(tool_out_dir))
                        self.last_coords_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
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
                    self._last_coords_sanity_dir = str(newest)
                    try:
                        self.last_coords_dir_var.set(str(newest))
                        self.last_coords_summary_var.set(summary_short or ("OK" if ok else "FAIL"))
                    except Exception:
                        pass
                except Exception:
                    pass

            _run_tool_async(cmd, title="Coords Sanity Check", open_dir=out_dir, on_complete=_on_complete)

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
                elif kind == "coords":
                    last_dir = self._last_coords_sanity_dir
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
            row=17, column=1, sticky="w", pady=(6, 0)
        )
        tk.Button(tab_control, text="OCR test", width=12, command=run_ocr_sanity_ui).grid(
            row=17, column=2, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Button(tab_control, text="Coords OCR test", width=14, command=run_coords_sanity_ui).grid(
            row=17, column=0, sticky="w", pady=(6, 0)
        )

        tk.Button(tab_control, text="Anchor test", width=12, command=run_anchor_sanity_ui).grid(
            row=17, column=3, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Button(tab_control, text="Anchor setup", width=12, command=run_anchor_setup_ui).grid(
            row=17, column=4, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Button(tab_control, text="ROI overlay", width=12, command=lambda: _open_last_artifact("overlay", "roi")).grid(
            row=18, column=1, sticky="w", pady=(6, 0)
        )
        tk.Button(tab_control, text="ROI report", width=12, command=lambda: _open_last_artifact("report", "roi")).grid(
            row=18, column=2, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Button(tab_control, text="Anchor overlay", width=12, command=lambda: _open_last_artifact("overlay", "anchor")).grid(
            row=18, column=3, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Button(tab_control, text="OCR overlay", width=12, command=lambda: _open_last_artifact("overlay", "ocr")).grid(
            row=19, column=1, sticky="w", pady=(6, 0)
        )
        tk.Button(tab_control, text="OCR report", width=12, command=lambda: _open_last_artifact("report", "ocr")).grid(
            row=19, column=2, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Button(tab_control, text="Coords OCR overlay", width=14, command=lambda: _open_last_artifact("overlay", "coords")).grid(
            row=18, column=0, sticky="w", pady=(6, 0)
        )
        tk.Button(tab_control, text="Coords OCR report", width=14, command=lambda: _open_last_artifact("report", "coords")).grid(
            row=19, column=0, sticky="w", pady=(6, 0)
        )

        tk.Button(tab_control, text="Anchor report", width=12, command=lambda: _open_last_artifact("report", "anchor")).grid(
            row=19, column=3, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        # Últimos resultados
        tk.Label(tab_control, text="").grid(row=20, column=0)
        tk.Label(tab_control, text="Último ROI:").grid(row=21, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_control, textvariable=self.last_roi_summary_var, width=22, anchor="w").grid(
            row=21, column=1, sticky="w", pady=(6, 0)
        )
        tk.Label(tab_control, textvariable=self.last_roi_dir_var, width=52, anchor="w").grid(
            row=22, column=1, columnspan=3, sticky="w"
        )

        tk.Label(tab_control, text="Último OCR:").grid(row=23, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_control, textvariable=self.last_ocr_summary_var, width=22, anchor="w").grid(
            row=23, column=1, sticky="w", pady=(6, 0)
        )
        tk.Label(tab_control, textvariable=self.last_ocr_dir_var, width=52, anchor="w").grid(
            row=24, column=1, columnspan=3, sticky="w"
        )

        tk.Label(tab_control, text="Último Coords:").grid(row=25, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_control, textvariable=self.last_coords_summary_var, width=22, anchor="w").grid(
            row=25, column=1, sticky="w", pady=(6, 0)
        )
        tk.Label(tab_control, textvariable=self.last_coords_dir_var, width=52, anchor="w").grid(
            row=26, column=1, columnspan=3, sticky="w"
        )

        tk.Label(tab_control, text="Último Anchor:").grid(row=27, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_control, textvariable=self.last_anchor_summary_var, width=22, anchor="w").grid(
            row=27, column=1, sticky="w", pady=(6, 0)
        )
        tk.Label(tab_control, textvariable=self.last_anchor_dir_var, width=52, anchor="w").grid(
            row=28, column=1, columnspan=3, sticky="w"
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

        # Checklist de ruta (UI-only)
        tk.Label(tab_cavebot, text="").grid(row=7, column=0)
        tk.Label(tab_cavebot, text="Checklist de ruta:").grid(row=8, column=0, sticky="w", pady=(10, 0))

        self._route_status_var = tk.StringVar(value="(ruta no cargada)")
        tk.Label(tab_cavebot, textvariable=self._route_status_var, width=60, anchor="w").grid(
            row=8, column=1, columnspan=2, sticky="w", pady=(10, 0)
        )

        self._route_listbox = tk.Listbox(tab_cavebot, height=10, width=60)
        self._route_listbox.grid(row=9, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self._route_next_var = tk.StringVar(value="-")
        tk.Label(tab_cavebot, text="Próximos:").grid(row=10, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_cavebot, textvariable=self._route_next_var, width=60, anchor="w").grid(
            row=10, column=1, sticky="w", pady=(6, 0)
        )

        def _route_format_item(idx: int, wp: object) -> str:
            try:
                name = getattr(wp, "name", None) or ""
                x = getattr(wp, "x", None)
                y = getattr(wp, "y", None)
                z = getattr(wp, "z", None)
                act = getattr(wp, "action", None) or ""
                coord = ""
                if x is not None and y is not None:
                    coord = f"({x},{y}{'' if z is None else ','+str(z)})"
                label = name or coord or "(wp)"
                if act:
                    return f"{idx:03d}  {label}  action={act}"
                return f"{idx:03d}  {label}"
            except Exception:
                return f"{idx:03d}  (wp)"

        def _load_route_for_ui() -> None:
            path = str(self.cavebot_route_path.get()).strip() or "configs/route.json"
            if path == self._route_loaded_from and self._route_items:
                return
            self._route_loaded_from = path
            self._route_items = []
            self._route_current_index = None
            try:
                from navigation.route import load_route

                route = load_route(path)
                self._route_items = [{"wp": wp} for wp in route]
            except Exception:
                self._route_items = []

            try:
                self._route_listbox.delete(0, "end")
                for i, item in enumerate(self._route_items):
                    self._route_listbox.insert("end", _route_format_item(i, item.get("wp")))
            except Exception:
                pass

            try:
                if self._route_items:
                    self._route_status_var.set(f"Cargada: {path} ({len(self._route_items)} waypoints)")
                else:
                    self._route_status_var.set(f"No pude cargar ruta: {path}")
            except Exception:
                pass

        def reload_route_ui() -> None:
            self._route_loaded_from = ""
            _load_route_for_ui()

        tk.Button(tab_cavebot, text="Recargar ruta", width=14, command=reload_route_ui).grid(
            row=9, column=2, sticky="w", padx=(8, 0)
        )

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

        # Mantener la ruta cargada para checklist cuando cambie el path.
        try:
            self.cavebot_route_path.trace_add("write", lambda *_args: reload_route_ui())
        except Exception:
            pass
        _load_route_for_ui()

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

        tk.Label(
            tab_config,
            text=(
                "Tip estable (sin coords visibles): usa COORDS_PROVIDER=disabled + CAVEBOT_MODE=steps "
                "(ver ./scripts/profile_no_coords_steps.ps1). Minimap es experimental; ver README."
            ),
            wraplength=520,
            justify="left",
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))

        tk.Label(tab_config, text="").grid(row=9, column=0)  # separador simple

        tk.Checkbutton(tab_config, text="Modo asistente (sin inputs)", variable=self.asst_enabled).grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        tk.Checkbutton(tab_config, text="Confirmación humana (cavebot)", variable=self.asst_confirm).grid(
            row=11, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        tk.Checkbutton(tab_config, text="Alertas sonoras", variable=self.asst_sound).grid(
            row=12, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

        tk.Label(tab_config, text="").grid(row=13, column=0)  # separador simple

        tk.Checkbutton(tab_config, text="Guardar replays (ROI+JSON)", variable=self.replay_enabled).grid(
            row=14, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        tk.Label(tab_config, text="Replay interval (ms)").grid(row=15, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(tab_config, from_=100, to=60000, increment=100, textvariable=self.replay_interval_ms, width=8).grid(
            row=15, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(tab_config, text="Replay out_dir").grid(row=16, column=0, sticky="w", pady=(6, 0))
        tk.Entry(tab_config, textvariable=self.replay_out_dir, width=34).grid(
            row=16, column=1, sticky="w", pady=(6, 0)
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
            row=16, column=2, sticky="w", padx=(8, 0)
        )
        tk.Button(tab_config, text="Snapshot ahora", width=12, command=force_replay_snapshot).grid(
            row=14, column=2, sticky="w", padx=(8, 0)
        )

        # Presets for replay/jsonl
        tk.Label(tab_config, text="Preset (replay/log)").grid(row=17, column=0, sticky="w")
        tel_presets = ["Custom", "Off", "Debug", "Soak", "Soak Full"]
        ttk.Combobox(
            tab_config,
            textvariable=self.telemetry_preset,
            values=tel_presets,
            width=16,
            state="readonly",
        ).grid(row=17, column=1, sticky="w")
        tk.Button(
            tab_config,
            text="Aplicar",
            width=12,
            command=lambda: self._apply_telemetry_preset(str(self.telemetry_preset.get())),
        ).grid(row=17, column=2, sticky="w", padx=(8, 0))

        tk.Checkbutton(tab_config, text="Exportar telemetría JSONL", variable=self.log_enabled).grid(
            row=18, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        tk.Label(tab_config, text="Log interval (ms)").grid(row=19, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(tab_config, from_=100, to=60000, increment=50, textvariable=self.log_interval_ms, width=8).grid(
            row=19, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(tab_config, text="Log out_file").grid(row=20, column=0, sticky="w", pady=(6, 0))
        tk.Entry(tab_config, textvariable=self.log_out_file, width=34).grid(
            row=20, column=1, sticky="w", pady=(6, 0)
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
            row=20, column=2, sticky="w", padx=(8, 0)
        )
        tk.Button(tab_config, text="Abrir archivo", width=12, command=open_log_file).grid(
            row=19, column=2, sticky="w", padx=(8, 0)
        )

        # --- Idle alert (UI + core) ---
        tk.Label(tab_config, text="").grid(row=21, column=0)  # separador simple
        tk.Label(tab_config, text="Idle (alerta / anti-stuck, sin inputs)").grid(
            row=22, column=0, columnspan=3, sticky="w", pady=(10, 0)
        )
        tk.Label(tab_config, text="WARN si idle ≥ (s)").grid(row=23, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(tab_config, from_=0, to=3600, increment=5, textvariable=self.idle_alert_s, width=8).grid(
            row=23, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(tab_config, text="FAIL si idle ≥ (s)").grid(row=24, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(tab_config, from_=0, to=7200, increment=10, textvariable=self.ui_idle_fail_s, width=8).grid(
            row=24, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(tab_config, text="Repetir alerta cada (s)").grid(row=25, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(tab_config, from_=1, to=600, increment=1, textvariable=self.idle_repeat_s, width=8).grid(
            row=25, column=1, sticky="w", pady=(6, 0)
        )
        tk.Label(
            tab_config,
            text="(0 desactiva. Se aplica al iniciar el bot; requiere reinicio)",
        ).grid(row=26, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # --- Overlay exporter (env-based) ---
        tk.Label(tab_config, text="").grid(row=27, column=0)  # separador simple
        tk.Label(tab_config, text="Overlay (frames anotados)").grid(
            row=28, column=0, columnspan=3, sticky="w", pady=(10, 0)
        )

        tk.Label(tab_config, text="Preset").grid(row=29, column=0, sticky="w", pady=(6, 0))
        overlay_presets = ["Custom", "Minimal", "Debug HUD", "Full HUD"]
        ttk.Combobox(
            tab_config,
            textvariable=self.overlay_preset,
            values=overlay_presets,
            width=16,
            state="readonly",
        ).grid(row=29, column=1, sticky="w", pady=(6, 0))

        tk.Button(
            tab_config,
            text="Aplicar",
            width=12,
            command=lambda: self._apply_overlay_preset(str(self.overlay_preset.get())),
        ).grid(row=29, column=2, sticky="w", padx=(8, 0), pady=(6, 0))

        tk.Checkbutton(tab_config, text="Habilitar overlay", variable=self.overlay_enabled).grid(
            row=30, column=0, sticky="w", pady=(6, 0)
        )
        tk.Button(tab_config, text="Cargar UI", width=12, command=self._load_ui_settings).grid(
            row=30, column=1, sticky="w", pady=(6, 0)
        )
        tk.Button(tab_config, text="Guardar UI", width=12, command=self._save_ui_settings).grid(
            row=30, column=2, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Label(tab_config, text="Overlay out_dir").grid(row=31, column=0, sticky="w", pady=(6, 0))
        tk.Entry(tab_config, textvariable=self.overlay_out_dir, width=34).grid(
            row=31, column=1, sticky="w", pady=(6, 0)
        )

        def open_overlay_dir() -> None:
            try:
                p = str(self.overlay_out_dir.get()).strip() or "logs/debug_overlay"
                os.makedirs(p, exist_ok=True)
                os.startfile(os.path.abspath(p))
            except Exception:
                pass

        tk.Button(tab_config, text="Abrir carpeta", width=12, command=open_overlay_dir).grid(
            row=31, column=2, sticky="w", padx=(8, 0)
        )

        tk.Label(tab_config, text="Interval (s)").grid(row=32, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(
            tab_config,
            from_=0.1,
            to=60.0,
            increment=0.1,
            textvariable=self.overlay_interval_s,
            width=8,
        ).grid(row=32, column=1, sticky="w", pady=(6, 0))

        tk.Checkbutton(tab_config, text="Tile grid", variable=self.overlay_tile_grid).grid(
            row=33, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )
        tk.Label(tab_config, text="Tile px").grid(row=34, column=0, sticky="w", pady=(6, 0))
        tk.Spinbox(tab_config, from_=4, to=128, increment=1, textvariable=self.overlay_tile_px, width=8).grid(
            row=34, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(tab_config, text="OVERLAY_ROIS (CSV)").grid(row=35, column=0, sticky="w", pady=(6, 0))
        tk.Entry(tab_config, textvariable=self.overlay_rois, width=34).grid(
            row=35, column=1, sticky="w", pady=(6, 0)
        )

        tk.Label(
            tab_config,
            text="(Se aplica al iniciar el bot; requiere reinicio)",
        ).grid(row=36, column=0, columnspan=3, sticky="w", pady=(4, 0))

        def open_ui_settings_file() -> None:
            try:
                p = self._ui_settings_path()
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
                if not p.exists():
                    try:
                        self._save_ui_settings()
                    except Exception:
                        pass
                os.startfile(os.path.abspath(str(p)))
            except Exception:
                pass

        tk.Button(tab_config, text="Reset defaults", width=18, command=self._reset_ui_defaults).grid(
            row=37, column=0, sticky="w", pady=(6, 0)
        )
        tk.Button(tab_config, text="Abrir ui_settings.json", width=18, command=open_ui_settings_file).grid(
            row=37, column=1, sticky="w", pady=(6, 0)
        )

        def open_latest_soak() -> None:
            try:
                # Prefer last run id (same UI session), otherwise find newest on disk.
                rid = self._last_soak_run_id
                replay_base = "logs/replay_soak"
                overlay_base = "logs/debug_overlay_soak"
                replay_dir = None
                overlay_dir = None
                if rid:
                    replay_dir = Path(replay_base) / rid
                    overlay_dir = Path(overlay_base) / rid
                if replay_dir is None or not replay_dir.exists():
                    replay_dir = self._find_latest_soak_dir(replay_base)
                if overlay_dir is None or not overlay_dir.exists():
                    overlay_dir = self._find_latest_soak_dir(overlay_base)

                # Open whatever exists.
                if replay_dir is not None and replay_dir.exists():
                    os.startfile(os.path.abspath(str(replay_dir)))
                if overlay_dir is not None and overlay_dir.exists():
                    os.startfile(os.path.abspath(str(overlay_dir)))
            except Exception:
                pass

        def open_latest_soak_jsonl() -> None:
            try:
                p = None
                rid = self._last_soak_run_id
                if rid:
                    cand = Path("logs") / f"telemetry_soak_{rid}.jsonl"
                    if cand.exists():
                        p = cand
                if p is None:
                    p = self._find_latest_soak_jsonl()
                if p is None:
                    return
                os.startfile(os.path.abspath(str(p)))
            except Exception:
                pass

        tk.Button(tab_config, text="Abrir último soak", width=18, command=open_latest_soak).grid(
            row=38, column=0, sticky="w", pady=(6, 0)
        )
        tk.Button(tab_config, text="Abrir JSONL soak", width=18, command=open_latest_soak_jsonl).grid(
            row=38, column=1, sticky="w", pady=(6, 0)
        )

        def open_latest_soak_all() -> None:
            try:
                open_latest_soak()
                open_latest_soak_jsonl()
            except Exception:
                pass

        tk.Button(tab_config, text="Abrir TODO soak", width=18, command=open_latest_soak_all).grid(
            row=38, column=2, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        tk.Label(tab_config, text="Soak run_id:").grid(row=39, column=0, sticky="w", pady=(6, 0))
        tk.Label(tab_config, textvariable=self.soak_run_id_var, width=22, anchor="w").grid(
            row=39, column=1, sticky="w", pady=(6, 0)
        )

        def _resolve_latest_soak_inputs() -> tuple[Path | None, Path | None, Path | None, str | None]:
            """Best-effort resolution of latest soak artifacts.

            Prefers the current UI session run_id, otherwise the newest on disk.
            """

            rid = self._last_soak_run_id

            replay_dir = None
            overlay_dir = None
            jsonl_path = None

            try:
                if rid:
                    cand = Path("logs") / "replay_soak" / str(rid)
                    if cand.exists():
                        replay_dir = cand
            except Exception:
                replay_dir = None

            try:
                if rid:
                    cand = Path("logs") / "debug_overlay_soak" / str(rid)
                    if cand.exists():
                        overlay_dir = cand
            except Exception:
                overlay_dir = None

            try:
                if rid:
                    cand = Path("logs") / f"telemetry_soak_{rid}.jsonl"
                    if cand.exists():
                        jsonl_path = cand
            except Exception:
                jsonl_path = None

            if replay_dir is None:
                replay_dir = self._find_latest_soak_dir("logs/replay_soak")
            if overlay_dir is None:
                overlay_dir = self._find_latest_soak_dir("logs/debug_overlay_soak")
            if jsonl_path is None:
                jsonl_path = self._find_latest_soak_jsonl()

            # Fallbacks to non-soak locations.
            if replay_dir is None:
                replay_dir = Path("logs") / "replay"
            if overlay_dir is None:
                overlay_dir = Path("logs") / "debug_overlay"
            if jsonl_path is None:
                jsonl_path = Path("logs") / "telemetry.jsonl"

            return replay_dir, overlay_dir, jsonl_path, rid

        def _timeline_default_out(rid: str | None) -> Path:
            try:
                if rid:
                    return Path("logs") / f"soak_timeline_{rid}.html"
            except Exception:
                pass
            return Path("logs") / "soak_timeline.html"

        def open_soak_timeline_html() -> None:
            try:
                replay_dir, overlay_dir, jsonl_path, rid = _resolve_latest_soak_inputs()
                out_html = _timeline_default_out(rid)
                if not out_html.exists():
                    return
                os.startfile(os.path.abspath(str(out_html)))
            except Exception:
                pass

        def generate_soak_timeline_html() -> None:
            """Generate timeline HTML for the latest soak and open it."""
            try:
                replay_dir, overlay_dir, jsonl_path, rid = _resolve_latest_soak_inputs()
                out_html = _timeline_default_out(rid)

                # Ensure output dir exists.
                try:
                    out_html.parent.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass

                cmd = [
                    sys.executable,
                    str(self._repo_root / "tools" / "soak_timeline_report.py"),
                    "--jsonl",
                    str(jsonl_path),
                    "--replay-dir",
                    str(replay_dir),
                    "--overlay-dir",
                    str(overlay_dir),
                    "--out",
                    str(out_html),
                ]

                p = subprocess.run(
                    cmd,
                    cwd=str(self._repo_root),
                    capture_output=True,
                    text=True,
                )

                if p.returncode != 0:
                    msg = (p.stderr or p.stdout or "(sin output)").strip()
                    if len(msg) > 1500:
                        msg = msg[:1500] + "..."
                    try:
                        self._messagebox.showerror(
                            "Timeline HTML",
                            f"Falló generación (code={p.returncode}).\n\n{msg}",
                        )
                    except Exception:
                        pass
                    return

                # Open result.
                try:
                    os.startfile(os.path.abspath(str(out_html)))
                except Exception:
                    pass
            except Exception as e:
                try:
                    self._messagebox.showerror("Timeline HTML", f"Error: {e}")
                except Exception:
                    pass

        tk.Button(tab_config, text="Soak timeline (HTML)", width=18, command=generate_soak_timeline_html).grid(
            row=40, column=0, sticky="w", pady=(6, 0)
        )
        tk.Button(tab_config, text="Abrir timeline", width=18, command=open_soak_timeline_html).grid(
            row=40, column=1, sticky="w", pady=(6, 0)
        )

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
                # Coords confidence (estructurado desde el core)
                coords_status = str(getattr(tel, "coords_status", "") or "")
                coords_jump = getattr(tel, "coords_jump", None)
                if coords_status == "NO_COORDS" or getattr(tel, "pos_x", None) is None or getattr(tel, "pos_y", None) is None:
                    parts.append("no_coords")
                elif coords_status == "UNSTABLE":
                    parts.append("coords_unstable")
                elif coords_status == "BAD_JUMP":
                    parts.append("coords_bad")
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
                            idle_fail_s = (
                                float(idle_fail_raw)
                                if idle_fail_raw
                                else (
                                    max(idle_warn_s * 2.0, idle_warn_s + 30.0) if idle_warn_s > 0 else 0.0
                                )
                            )
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

                            # If coords are not trusted/available, don't compute idle (it would be garbage).
                            if coords_status in {"NO_COORDS", "BAD_JUMP", "UNSTABLE", "DISABLED"}:
                                self.idle_status_var.set("-")
                                jump_s = "" if coords_jump is None else f" (jump={coords_jump})"
                                self.idle_var.set(f"coords {coords_status or 'unknown'}{jump_s}")
                                try:
                                    self._idle_status_label.config(fg="#666666")
                                except Exception:
                                    pass

                            elif gx is None or gy is None:
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

                    # Stuck status (diagnóstico del bot en tel.note)
                    try:
                        note = str(getattr(tel, "note", "") or "").strip()
                        s_reason = str(getattr(tel, "stuck_reason", "") or "").strip()
                        s_idle = getattr(tel, "stuck_idle_s", None)
                        s_block = getattr(tel, "stuck_blockers", None)
                        s_extra = str(getattr(tel, "stuck_extra", "") or "").strip()

                        if not s_reason and not note:
                            self.stuck_status_var.set("OFF")
                            self.stuck_var.set("-")
                            try:
                                self._stuck_status_label.config(fg="#666666")
                            except Exception:
                                pass
                        elif s_reason:
                            status = "FAIL" if s_reason == "STALE_GS" else "WARN"
                            self.stuck_status_var.set(status)
                            if note.startswith("⛔ Stuck:"):
                                self.stuck_var.set(note)
                            else:
                                try:
                                    idle_s = "?" if s_idle is None else f"{float(s_idle):.0f}s"
                                except Exception:
                                    idle_s = "?"
                                try:
                                    blk_s = "?" if s_block is None else str(int(s_block))
                                except Exception:
                                    blk_s = "?"
                                msg = f"⛔ Stuck: {s_reason} | idle {idle_s} | blockers {blk_s}"
                                if s_extra:
                                    msg = f"{msg} | {s_extra}"
                                self.stuck_var.set(msg)
                            try:
                                if status == "FAIL":
                                    self._stuck_status_label.config(fg="#b00020")
                                else:
                                    self._stuck_status_label.config(fg="#b26a00")
                            except Exception:
                                pass
                        else:
                            # Any other note (informational)
                            self.stuck_status_var.set("ON")
                            self.stuck_var.set(note)
                            try:
                                self._stuck_status_label.config(fg="#1f6feb")
                            except Exception:
                                pass
                    except Exception:
                        self.stuck_status_var.set("-")
                        self.stuck_var.set("-")

                    # Panel de eventos + checklist: detectar cambios relevantes.
                    try:
                        def _ts() -> str:
                            try:
                                return time.strftime("%H:%M:%S")
                            except Exception:
                                return "--:--:--"

                        def _push(line: str) -> None:
                            try:
                                self._event_lines.append(line)
                                if len(self._event_lines) > int(self._event_max_lines):
                                    self._event_lines = self._event_lines[-int(self._event_max_lines) :]
                                # render
                                try:
                                    self._events_text.configure(state="normal")
                                except Exception:
                                    pass
                                try:
                                    self._events_text.delete("1.0", "end")
                                    self._events_text.insert("end", "\n".join(self._event_lines))
                                    self._events_text.see("end")
                                except Exception:
                                    pass
                                try:
                                    self._events_text.configure(state="disabled")
                                except Exception:
                                    pass
                            except Exception:
                                pass

                        tgt = str(getattr(tel, "target", "") or "")
                        reco = str(getattr(tel, "recommendation", "") or "")
                        action_req = str(getattr(tel, "action_request", "") or "")
                        action_committed = getattr(tel, "action_committed", None)
                        wp = str(getattr(tel, "cavebot_waypoint", "") or "")
                        wp_action = str(getattr(tel, "cavebot_action", "") or "")

                        # Coords confidence transitions (structured telemetry)
                        coords_status = str(getattr(tel, "coords_status", "") or "")
                        coords_jump = getattr(tel, "coords_jump", None)

                        def _coords_trouble(s: str) -> bool:
                            return str(s or "") in {"NO_COORDS", "UNSTABLE", "BAD_JUMP", "DISABLED"}

                        if coords_status != self._last_event_coords_status:
                            if _coords_trouble(coords_status) or _coords_trouble(self._last_event_coords_status):
                                jump_s = "" if coords_jump is None else f" (jump={coords_jump})"
                                _push(f"{_ts()} coords: {coords_status or 'OK'}{jump_s}")
                            self._last_event_coords_status = coords_status

                        # Stuck reason transitions (structured telemetry)
                        s_reason = str(getattr(tel, "stuck_reason", "") or "").strip()
                        s_idle = getattr(tel, "stuck_idle_s", None)
                        s_block = getattr(tel, "stuck_blockers", None)
                        s_extra = str(getattr(tel, "stuck_extra", "") or "").strip()
                        if s_reason != self._last_event_stuck_reason:
                            if s_reason:
                                try:
                                    idle_s = "?" if s_idle is None else f"{float(s_idle):.0f}s"
                                except Exception:
                                    idle_s = "?"
                                try:
                                    blk_s = "?" if s_block is None else str(int(s_block))
                                except Exception:
                                    blk_s = "?"
                                extra_s = f" | {s_extra}" if s_extra else ""
                                _push(f"{_ts()} stuck: {s_reason} | idle {idle_s} | blockers {blk_s}{extra_s}")
                            elif self._last_event_stuck_reason:
                                _push(f"{_ts()} stuck: cleared")
                            self._last_event_stuck_reason = s_reason

                        # StepNavigator index transitions (structured telemetry)
                        try:
                            step_total = int(getattr(tel, "cavebot_step_total", 0) or 0)
                            step_idx = int(getattr(tel, "cavebot_step_idx", 0) or 0)
                        except Exception:
                            step_total = 0
                            step_idx = 0

                        if step_total > 0:
                            prev_total = int(self._last_event_step_total or 0)
                            prev_idx = self._last_event_step_idx
                            if prev_total <= 0:
                                # Step mode just became active.
                                _push(f"{_ts()} step: {step_idx + 1}/{step_total} ({wp})")
                            elif prev_idx is not None and step_idx != int(prev_idx):
                                _push(f"{_ts()} step: {step_idx + 1}/{step_total} ({wp})")
                            self._last_event_step_total = step_total
                            self._last_event_step_idx = step_idx
                        else:
                            # Reset when not in step mode.
                            self._last_event_step_total = 0
                            self._last_event_step_idx = None

                        if tgt != self._last_event_target:
                            _push(f"{_ts()} target: {tgt}")
                            self._last_event_target = tgt

                        if reco != self._last_event_reco:
                            _push(f"{_ts()} reco: {reco}")
                            self._last_event_reco = reco

                        if (
                            action_req != self._last_event_action_req
                            or self._last_event_action_committed is None
                            or (action_committed is not None and bool(action_committed) != bool(self._last_event_action_committed))
                        ):
                            star = "*" if bool(action_committed) else ""
                            _push(f"{_ts()} action{star}: {action_req}")
                            self._last_event_action_req = action_req
                            self._last_event_action_committed = bool(action_committed) if action_committed is not None else None

                        if wp != self._last_event_wp or wp_action != self._last_event_wp_action:
                            if wp or wp_action:
                                _push(f"{_ts()} cavebot: {wp} action={wp_action}")
                            self._last_event_wp = wp
                            self._last_event_wp_action = wp_action

                        # Health / Idle status transitions
                        hs = str(self.health_status_var.get() or "")
                        if hs and hs != self._last_event_health_status and hs in {"WARN", "FAIL"}:
                            _push(f"{_ts()} health: {hs}")
                        self._last_event_health_status = hs

                        is_ = str(self.idle_status_var.get() or "")
                        if is_ and is_ != self._last_event_idle_status and is_ in {"WARN", "FAIL"}:
                            _push(f"{_ts()} idle: {is_} ({self.idle_var.get()})")
                        self._last_event_idle_status = is_

                        # Note / stuck diagnostics
                        try:
                            note = str(getattr(tel, "note", "") or "").strip()
                        except Exception:
                            note = ""
                        if note and note != self._last_event_note:
                            _push(f"{_ts()} note: {note}")
                            self._last_event_note = note

                        # Checklist: resaltar waypoint actual si podemos mapearlo.
                        try:
                            _load_route_for_ui()
                            if self._route_items:
                                cur_idx = None
                                # Prefer StepNavigator structured index when available.
                                try:
                                    step_total = int(getattr(tel, "cavebot_step_total", 0) or 0)
                                    step_idx = int(getattr(tel, "cavebot_step_idx", 0) or 0)
                                except Exception:
                                    step_total = 0
                                    step_idx = 0

                                if step_total > 0:
                                    cur_idx = max(0, step_idx)
                                    # Bound by loaded route length.
                                    try:
                                        if cur_idx >= len(self._route_items):
                                            cur_idx = max(0, len(self._route_items) - 1)
                                    except Exception:
                                        pass

                                if cur_idx is None and wp:
                                    # match by name or by "(x,y" string
                                    for i, item in enumerate(self._route_items):
                                        wpi = item.get("wp")
                                        name_i = str(getattr(wpi, "name", "") or "")
                                        x_i = getattr(wpi, "x", None)
                                        y_i = getattr(wpi, "y", None)
                                        z_i = getattr(wpi, "z", None)
                                        if name_i and name_i == wp:
                                            cur_idx = i
                                            break
                                        if x_i is not None and y_i is not None:
                                            cand2 = f"({x_i},{y_i})"
                                            cand3 = f"({x_i},{y_i},{z_i})" if z_i is not None else ""
                                            if wp == cand2 or (cand3 and wp == cand3):
                                                cur_idx = i
                                                break
                                if cur_idx is not None and cur_idx != self._route_current_index:
                                    self._route_current_index = cur_idx
                                    try:
                                        self._route_listbox.selection_clear(0, "end")
                                        self._route_listbox.selection_set(cur_idx)
                                        self._route_listbox.see(cur_idx)
                                    except Exception:
                                        pass

                                # Próximos N
                                try:
                                    n = 3
                                    if self._route_current_index is not None:
                                        start = min(len(self._route_items) - 1, max(0, int(self._route_current_index)))
                                        nxt = []
                                        for j in range(start, min(len(self._route_items), start + n)):
                                            wpp = self._route_items[j].get("wp")
                                            label = getattr(wpp, "name", None) or f"({getattr(wpp, 'x', '?')},{getattr(wpp, 'y', '?')})"
                                            nxt.append(str(label))
                                        self._route_next_var.set(" → ".join(nxt) if nxt else "-")
                                    else:
                                        self._route_next_var.set("-")
                                except Exception:
                                    pass
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

    def _ui_settings_path(self) -> Path:
        """Return the JSON path used to persist UI settings.

        Controlled by env var UI_SETTINGS_FILE; defaults to configs/ui_settings.json.
        Relative paths are resolved from repo root.
        """

        raw = (os.getenv("UI_SETTINGS_FILE", "") or "").strip()
        if not raw:
            return self._repo_root / "configs" / "ui_settings.json"
        p = Path(raw)
        if not p.is_absolute():
            p = self._repo_root / p
        return p

    def _load_ui_settings(self) -> None:
        p = self._ui_settings_path()
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return

        ov = data.get("overlay")
        if isinstance(ov, dict):
            try:
                if "enabled" in ov:
                    self.overlay_enabled.set(bool(ov.get("enabled")))
                if "out_dir" in ov:
                    self.overlay_out_dir.set(str(ov.get("out_dir") or "logs/debug_overlay"))
                if "interval_s" in ov:
                    self.overlay_interval_s.set(max(0.1, float(ov.get("interval_s") or 1.0)))
                if "tile_grid" in ov:
                    self.overlay_tile_grid.set(bool(ov.get("tile_grid")))
                if "tile_px" in ov:
                    self.overlay_tile_px.set(max(4, min(128, int(float(ov.get("tile_px") or 32)))))
                if "rois" in ov:
                    self.overlay_rois.set(str(ov.get("rois") or ""))
            except Exception:
                pass

        # RuntimeConfig-backed settings
        try:
            h = data.get("healing")
            if isinstance(h, dict):
                if "enabled" in h:
                    self.healing_enabled.set(bool(h.get("enabled")))
                if "hp_below_pct" in h:
                    self.heal_hp_below_pct.set(int(float(h.get("hp_below_pct") or 0)))
                if "mp_below_pct" in h:
                    self.heal_mp_below_pct.set(int(float(h.get("mp_below_pct") or 0)))
                if "action" in h:
                    self.heal_action.set(str(h.get("action") or ""))
        except Exception:
            pass

        try:
            cb = data.get("cavebot")
            if isinstance(cb, dict):
                if "enabled" in cb:
                    self.cavebot_enabled.set(bool(cb.get("enabled")))
                if "route_path" in cb:
                    self.cavebot_route_path.set(str(cb.get("route_path") or "configs/route.json"))
        except Exception:
            pass

        try:
            s = data.get("simulation")
            if isinstance(s, dict):
                if "enabled" in s:
                    self.sim_enabled.set(bool(s.get("enabled")))
                if "paralyzed" in s:
                    self.sim_paralyzed.set(bool(s.get("paralyzed")))
                if "haste_active" in s:
                    self.sim_haste_active.set(bool(s.get("haste_active")))
                if "utamo_active" in s:
                    self.sim_utamo_active.set(bool(s.get("utamo_active")))
                if "hungry" in s:
                    self.sim_hungry.set(bool(s.get("hungry")))
        except Exception:
            pass

        try:
            a = data.get("assistant")
            if isinstance(a, dict):
                if "enabled" in a:
                    self.asst_enabled.set(bool(a.get("enabled")))
                if "confirm_actions" in a:
                    self.asst_confirm.set(bool(a.get("confirm_actions")))
                if "sound_alerts" in a:
                    self.asst_sound.set(bool(a.get("sound_alerts")))
        except Exception:
            pass

        try:
            r = data.get("replay")
            if isinstance(r, dict):
                if "enabled" in r:
                    self.replay_enabled.set(bool(r.get("enabled")))
                if "interval_ms" in r:
                    self.replay_interval_ms.set(int(float(r.get("interval_ms") or 0)))
                if "out_dir" in r:
                    self.replay_out_dir.set(str(r.get("out_dir") or self.replay_out_dir.get()))
        except Exception:
            pass

        try:
            lg = data.get("logging")
            if isinstance(lg, dict):
                if "enabled" in lg:
                    self.log_enabled.set(bool(lg.get("enabled")))
                if "interval_ms" in lg:
                    self.log_interval_ms.set(int(float(lg.get("interval_ms") or 0)))
                if "out_file" in lg:
                    self.log_out_file.set(str(lg.get("out_file") or self.log_out_file.get()))
        except Exception:
            pass

        # Env-backed per-run settings
        try:
            idle = data.get("idle")
            if isinstance(idle, dict):
                if "alert_s" in idle:
                    self.idle_alert_s.set(max(0.0, float(idle.get("alert_s") or 0.0)))
                if "repeat_s" in idle:
                    self.idle_repeat_s.set(max(1.0, float(idle.get("repeat_s") or 10.0)))
                if "ui_fail_s" in idle:
                    self.ui_idle_fail_s.set(max(0.0, float(idle.get("ui_fail_s") or 0.0)))
        except Exception:
            pass

        try:
            ro = data.get("rois")
            if isinstance(ro, dict):
                if "config_override" in ro:
                    self.rois_config_override.set(str(ro.get("config_override") or ""))
        except Exception:
            pass

        try:
            preset = data.get("overlay_preset")
            if isinstance(preset, str) and preset:
                self.overlay_preset.set(preset)
        except Exception:
            pass

        try:
            tp = data.get("telemetry_preset")
            if isinstance(tp, str) and tp:
                self.telemetry_preset.set(tp)
        except Exception:
            pass

    def _save_ui_settings(self) -> None:
        p = self._ui_settings_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        try:
            payload = {
                "version": 1,
                "overlay_preset": str(self.overlay_preset.get()),
                "telemetry_preset": str(self.telemetry_preset.get()),
                "overlay": {
                    "enabled": bool(self.overlay_enabled.get()),
                    "out_dir": str(self.overlay_out_dir.get()).strip() or "logs/debug_overlay",
                    "interval_s": float(self.overlay_interval_s.get()),
                    "tile_grid": bool(self.overlay_tile_grid.get()),
                    "tile_px": int(self.overlay_tile_px.get()),
                    "rois": str(self.overlay_rois.get()).strip(),
                },
                "healing": {
                    "enabled": bool(self.healing_enabled.get()),
                    "hp_below_pct": int(self.heal_hp_below_pct.get()),
                    "mp_below_pct": int(self.heal_mp_below_pct.get()),
                    "action": str(self.heal_action.get()),
                },
                "cavebot": {
                    "enabled": bool(self.cavebot_enabled.get()),
                    "route_path": str(self.cavebot_route_path.get()),
                },
                "simulation": {
                    "enabled": bool(self.sim_enabled.get()),
                    "paralyzed": bool(self.sim_paralyzed.get()),
                    "haste_active": bool(self.sim_haste_active.get()),
                    "utamo_active": bool(self.sim_utamo_active.get()),
                    "hungry": bool(self.sim_hungry.get()),
                },
                "assistant": {
                    "enabled": bool(self.asst_enabled.get()),
                    "confirm_actions": bool(self.asst_confirm.get()),
                    "sound_alerts": bool(self.asst_sound.get()),
                },
                "replay": {
                    "enabled": bool(self.replay_enabled.get()),
                    "interval_ms": int(self.replay_interval_ms.get()),
                    "out_dir": str(self.replay_out_dir.get()),
                },
                "logging": {
                    "enabled": bool(self.log_enabled.get()),
                    "interval_ms": int(self.log_interval_ms.get()),
                    "out_file": str(self.log_out_file.get()),
                },
                "idle": {
                    "alert_s": float(self.idle_alert_s.get()),
                    "repeat_s": float(self.idle_repeat_s.get()),
                    "ui_fail_s": float(self.ui_idle_fail_s.get()),
                },
                "rois": {
                    "config_override": str(self.rois_config_override.get()).strip(),
                },
            }
        except Exception:
            return

        try:
            p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _apply_overlay_preset(self, name: str) -> None:
        n = (name or "").strip().lower()
        try:
            if n in {"custom", ""}:
                return

            if n in {"minimal"}:
                self.overlay_enabled.set(True)
                self.overlay_interval_s.set(1.0)
                self.overlay_out_dir.set("logs/debug_overlay")
                self.overlay_tile_grid.set(False)
                self.overlay_tile_px.set(32)
                self.overlay_rois.set("")
                return

            if n in {"debug hud", "debug"}:
                self.overlay_enabled.set(True)
                self.overlay_interval_s.set(1.0)
                self.overlay_out_dir.set("logs/debug_overlay")
                self.overlay_tile_grid.set(True)
                self.overlay_tile_px.set(32)
                self.overlay_rois.set(
                    "coords_ocr,minimap_content,hp_top_ocr,mp_top_ocr,hp_low_bar,mp_low_bar,states_icons,equipment_slots,battlelist_rows"
                )
                return

            if n in {"full hud", "full"}:
                self.overlay_enabled.set(True)
                self.overlay_interval_s.set(0.5)
                self.overlay_out_dir.set("logs/debug_overlay")
                self.overlay_tile_grid.set(True)
                self.overlay_tile_px.set(32)
                self.overlay_rois.set(
                    "coords_ocr,minimap_content,hpmp_top_strip,hp_top_ocr,mp_top_ocr,hpmp_low_panel,hp_low_bar,mp_low_bar,states_icons,equipment_slots,skills_panel,right_hud_panel,battlelist_rows,chat_panel"
                )
                return
        except Exception:
            pass

    def _apply_telemetry_preset(self, name: str) -> None:
        n = (name or "").strip().lower()
        try:
            if n in {"custom", ""}:
                return

            if n in {"off", "disabled"}:
                self.replay_enabled.set(False)
                self.log_enabled.set(False)
                return

            if n in {"debug"}:
                self.replay_enabled.set(True)
                self.replay_interval_ms.set(2000)
                self.replay_out_dir.set("logs/replay")
                self.log_enabled.set(True)
                self.log_interval_ms.set(250)
                self.log_out_file.set("logs/telemetry.jsonl")
                return

            if n in {"soak", "soak run"}:
                self.replay_enabled.set(True)
                self.replay_interval_ms.set(1500)
                self.replay_out_dir.set("logs/replay")
                self.log_enabled.set(True)
                self.log_interval_ms.set(250)
                self.log_out_file.set("logs/telemetry.jsonl")
                return

            if n in {"soak full", "soak_full", "soakfull"}:
                # Full soak = replay+jsonl + overlay configured.
                self.replay_enabled.set(True)
                self.replay_interval_ms.set(1500)
                self.replay_out_dir.set("logs/replay_soak")
                self.log_enabled.set(True)
                self.log_interval_ms.set(250)
                self.log_out_file.set("logs/telemetry.jsonl")
                try:
                    self.overlay_preset.set("Full HUD")
                    self._apply_overlay_preset("Full HUD")
                    # Keep soak overlay separate to simplify debugging.
                    self.overlay_out_dir.set("logs/debug_overlay_soak")
                    self.overlay_interval_s.set(0.5)
                except Exception:
                    pass
                return
        except Exception:
            pass

    def _reset_ui_defaults(self) -> None:
        # Best-effort reset; keep it conservative.
        try:
            # Healing
            self.healing_enabled.set(False)
            self.heal_hp_below_pct.set(70)
            self.heal_mp_below_pct.set(30)
            self.heal_action.set("")
        except Exception:
            pass

    def _find_latest_soak_dir(self, base_dir: str) -> Path | None:
        """Return the newest YYYYMMDD_HHMMSS subdir under base_dir, if any."""
        try:
            base = Path(base_dir)
            if not base.exists() or not base.is_dir():
                return None
            dirs = [p for p in base.iterdir() if p.is_dir()]
            if not dirs:
                return None
            # Prefer lexicographic order: timestamp format sorts correctly.
            dirs.sort(key=lambda p: p.name)
            return dirs[-1]
        except Exception:
            return None

    def _find_latest_soak_jsonl(self) -> Path | None:
        try:
            logs_dir = Path("logs")
            if not logs_dir.exists() or not logs_dir.is_dir():
                return None
            files = [p for p in logs_dir.glob("telemetry_soak_*.jsonl") if p.is_file()]
            if not files:
                return None
            files.sort(key=lambda p: p.name)
            return files[-1]
        except Exception:
            return None

        try:
            # Cavebot
            self.cavebot_enabled.set(False)
            self.cavebot_route_path.set("configs/route.json")
        except Exception:
            pass

        try:
            # Simulation
            self.sim_enabled.set(True)
            self.sim_paralyzed.set(False)
            self.sim_haste_active.set(False)
            self.sim_utamo_active.set(False)
            self.sim_hungry.set(False)
        except Exception:
            pass

        try:
            # Assistant
            self.asst_enabled.set(True)
            self.asst_confirm.set(True)
            self.asst_sound.set(True)
        except Exception:
            pass

        try:
            # Replay/Logging
            self.telemetry_preset.set("Custom")
            self.replay_enabled.set(False)
            self.replay_interval_ms.set(2000)
            self.replay_out_dir.set(self._config.replay_snapshot().out_dir)
            self.log_enabled.set(False)
            self.log_interval_ms.set(250)
            self.log_out_file.set(self._config.logging_snapshot().out_file)
        except Exception:
            pass

        try:
            # Idle
            self.idle_alert_s.set(0.0)
            self.idle_repeat_s.set(10.0)
            self.ui_idle_fail_s.set(0.0)
        except Exception:
            pass

        try:
            # ROIs override
            self.rois_config_override.set("")
        except Exception:
            pass

        try:
            # Overlay
            self.overlay_preset.set("Custom")
            self.overlay_enabled.set(False)
            self.overlay_interval_s.set(1.0)
            self.overlay_out_dir.set("logs/debug_overlay")
            self.overlay_tile_grid.set(True)
            self.overlay_tile_px.set(32)
            self.overlay_rois.set(
                "coords_ocr,minimap_content,hp_top_ocr,mp_top_ocr,hp_low_bar,mp_low_bar,states_icons,equipment_slots,battlelist_rows"
            )
        except Exception:
            pass

    def start(self) -> None:
        if self._is_running():
            return

        # If running a soak preset, isolate outputs per session.
        try:
            tel_preset = str(getattr(self, "telemetry_preset", None).get()).strip().lower()  # type: ignore[union-attr]
        except Exception:
            tel_preset = ""
        if tel_preset in {"soak", "soak run", "soak full", "soak_full", "soakfull"}:
            try:
                run_id = time.strftime("%Y%m%d_%H%M%S")
                try:
                    self._last_soak_run_id = str(run_id)
                except Exception:
                    self._last_soak_run_id = None
                try:
                    if self.soak_run_id_var is not None:
                        self.soak_run_id_var.set(str(run_id))
                except Exception:
                    pass

                # Replay dir
                try:
                    base = str(self.replay_out_dir.get()).strip() or "logs/replay_soak"
                    self.replay_out_dir.set(str(Path(base) / run_id))
                except Exception:
                    pass

                # Overlay dir
                try:
                    base = str(self.overlay_out_dir.get()).strip() or "logs/debug_overlay_soak"
                    self.overlay_out_dir.set(str(Path(base) / run_id))
                except Exception:
                    pass

                # JSONL file (unique per run)
                try:
                    # Keep logs under logs/ by default.
                    self.log_out_file.set(str(Path("logs") / f"telemetry_soak_{run_id}.jsonl"))
                except Exception:
                    pass
            except Exception:
                pass
        else:
            # Not a soak run.
            try:
                self._last_soak_run_id = None
            except Exception:
                pass
            try:
                if self.soak_run_id_var is not None:
                    self.soak_run_id_var.set("-")
            except Exception:
                pass

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

        # Apply overlay exporter settings for this bot run (used by src/main.py overlay exporter).
        try:
            ov_enabled = bool(self.overlay_enabled.get())
        except Exception:
            ov_enabled = False
        try:
            ov_out_dir = str(self.overlay_out_dir.get()).strip() or "logs/debug_overlay"
        except Exception:
            ov_out_dir = "logs/debug_overlay"
        try:
            ov_interval_s = float(self.overlay_interval_s.get())
        except Exception:
            ov_interval_s = 1.0
        try:
            ov_tile_grid = bool(self.overlay_tile_grid.get())
        except Exception:
            ov_tile_grid = False
        try:
            ov_tile_px = int(float(self.overlay_tile_px.get()))
        except Exception:
            ov_tile_px = 32
        try:
            ov_rois = str(self.overlay_rois.get()).strip()
        except Exception:
            ov_rois = ""

        try:
            if ov_enabled:
                os.environ["OVERLAY_ENABLED"] = "1"
                os.environ["OVERLAY_OUT_DIR"] = str(ov_out_dir)
                os.environ["OVERLAY_INTERVAL_S"] = str(max(0.1, float(ov_interval_s)))
                if ov_tile_grid:
                    os.environ["OVERLAY_TILE_PX"] = str(max(4, int(ov_tile_px)))
                else:
                    os.environ.pop("OVERLAY_TILE_PX", None)

                if ov_rois:
                    os.environ["OVERLAY_ROIS"] = ov_rois
                else:
                    os.environ.pop("OVERLAY_ROIS", None)
            else:
                os.environ.pop("OVERLAY_ENABLED", None)
                os.environ.pop("OVERLAY_OUT_DIR", None)
                os.environ.pop("OVERLAY_INTERVAL_S", None)
                os.environ.pop("OVERLAY_TILE_PX", None)
                os.environ.pop("OVERLAY_ROIS", None)
        except Exception:
            pass

        # Persist UI settings (best-effort) so the next UI open restores them.
        try:
            self._save_ui_settings()
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
