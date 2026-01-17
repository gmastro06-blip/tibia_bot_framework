from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Mide layout (Tkinter) y exporta métricas de widgets a JSON.")
    ap.add_argument("--out", default="logs/layout_metrics.json", help="Archivo JSON de salida")
    ap.add_argument("--w", type=int, default=980, help="Ancho de ventana")
    ap.add_argument("--h", type=int, default=620, help="Alto de ventana")
    ap.add_argument("--no-print", action="store_true", help="No imprimir líneas NAME: x=.. y=.. w=.. h=..")
    ap.add_argument(
        "--all-tabs",
        action="store_true",
        help="Exporta un JSON por cada tab del Notebook principal (reduce 1x1/coords negativas de tabs no seleccionadas).",
    )
    args = ap.parse_args()

    # Evita que el hook automático (UI_MEASURE_LAYOUT) duplique output si está set.
    os.environ.pop("UI_MEASURE_LAYOUT", None)

    # Ensure repo root is importable when executing from tools/.
    try:
        repo_root = Path(__file__).resolve().parents[1]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
    except Exception:
        pass

    from run_bot_ui import BotUI

    ui = BotUI()
    try:
        ui.root.geometry(f"{max(200, int(args.w))}x{max(200, int(args.h))}")
    except Exception:
        pass

    try:
        ui.root.update_idletasks()
        ui.root.update()
    except Exception:
        pass

    out_path = Path(args.out)
    if not out_path.is_absolute():
        try:
            out_path = Path(ui._repo_root) / out_path  # type: ignore[attr-defined]
        except Exception:
            out_path = Path(args.out)

    def _slug(s: str) -> str:
        s = (s or "").strip().lower()
        out = []
        for ch in s:
            if ch.isalnum():
                out.append(ch)
            elif ch in {" ", "-", "_", "/", "\\", ".", ":"}:
                out.append("_")
        slug = "".join(out)
        while "__" in slug:
            slug = slug.replace("__", "_")
        return slug.strip("_") or "tab"

    def _export_to(p: Path, *, print_lines: bool) -> None:
        ui._export_layout_metrics(out_file=str(p), print_lines=bool(print_lines))  # type: ignore[attr-defined]

    def _select_tab(tab_id) -> None:
        try:
            ui.notebook.select(tab_id)  # type: ignore[attr-defined]
        except Exception:
            return
        # Give Tk a couple of passes to lay out widgets.
        try:
            ui.root.update_idletasks()
            ui.root.update()
        except Exception:
            pass
        try:
            time.sleep(0.05)
        except Exception:
            pass
        try:
            ui.root.update_idletasks()
            ui.root.update()
        except Exception:
            pass

    try:
        # Always export base file from the initial tab for backwards-compat.
        _export_to(out_path, print_lines=(not args.no_print))

        if bool(args.all_tabs):
            nb = getattr(ui, "notebook", None)
            if nb is not None and hasattr(nb, "tabs") and hasattr(nb, "tab") and hasattr(nb, "select"):
                base_dir = out_path.parent
                base_stem = out_path.stem
                base_suffix = out_path.suffix or ".json"

                tab_ids = []
                try:
                    tab_ids = list(nb.tabs())
                except Exception:
                    tab_ids = []

                for i, tab_id in enumerate(tab_ids):
                    try:
                        label = str(nb.tab(tab_id, "text") or "")
                    except Exception:
                        label = ""
                    name = f"{i:02d}_{_slug(label)}"
                    p = base_dir / f"{base_stem}.{name}{base_suffix}"

                    _select_tab(tab_id)
                    _export_to(p, print_lines=False)

                # Restore initial tab export to the base file.
                try:
                    if tab_ids:
                        _select_tab(tab_ids[0])
                except Exception:
                    pass
                _export_to(out_path, print_lines=False)
    finally:
        try:
            ui.root.destroy()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
