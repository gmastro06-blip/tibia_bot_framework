from __future__ import annotations

import sys
import types

import pytest


pytestmark = pytest.mark.ui


def test_routes_tab_actions_do_not_break_parse(monkeypatch, tmp_path) -> None:
    # Tkinter puede no estar disponible en algunos entornos headless; en ese caso skip.
    try:
        import tkinter as tk  # noqa: F401
    except Exception as e:
        pytest.skip(f"Tkinter no disponible: {e}")

    # Evitar que el UI importe el main real (captura/roboflow/etc.).
    fake_main = types.ModuleType("main")

    def _fake_run_bot(*_args, **_kwargs):
        return None

    fake_main.run_bot = _fake_run_bot  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "main", fake_main)

    import run_bot_ui

    try:
        ui = run_bot_ui.BotUI()
    except (SystemExit, Exception) as e:
        pytest.skip(f"Tk no disponible/usable en este entorno: {e}")

    try:
        try:
            ui.root.withdraw()
        except Exception:
            pass

        route_dir = tmp_path / "route"
        route_dir.mkdir(parents=True, exist_ok=True)

        ui.route_steps = []
        ui.route_path_var.set(str(route_dir))

        # Add some classic waypoint actions (action lines).
        ui._route_template_action("rope")
        ui._route_template_action("ladder")
        ui._route_template_action("loot")
        ui._route_template_action("use:shovel")

        ui._route_save()

        wp_path = route_dir / "waypoints.in"
        assert wp_path.exists(), "Expected waypoints.in to be written"

        parsed = ui._parse_waypoints(wp_path.read_text(encoding="utf-8"))
        assert not parsed.errors, f"Expected waypoints.in to parse, got: {parsed.errors}"

    finally:
        try:
            ui.root.destroy()
        except Exception:
            pass
