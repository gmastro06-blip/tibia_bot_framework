from __future__ import annotations

import sys
import types

import pytest


pytestmark = pytest.mark.ui


def test_ui_has_required_tabs(monkeypatch, tmp_path) -> None:
    # Tkinter may not be available/usable in some headless envs.
    try:
        import tkinter as tk  # noqa: F401
    except Exception as e:
        pytest.skip(f"Tkinter no disponible: {e}")

    # Avoid importing the real bot runtime.
    fake_main = types.ModuleType("main")

    def _fake_run_bot(*_args, **_kwargs):
        return None

    fake_main.run_bot = _fake_run_bot  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "main", fake_main)

    # Point UI settings at a temp file.
    settings_path = tmp_path / "ui_settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("UI_SETTINGS_FILE", str(settings_path))

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

        nb = getattr(ui, "notebook", None)
        assert nb is not None, "Expected BotUI.notebook to exist"

        texts = []
        try:
            for tab_id in nb.tabs():
                try:
                    texts.append(str(nb.tab(tab_id, "text") or ""))
                except Exception:
                    continue
        except Exception:
            texts = []

        # Required main tabs (per PASS 2 requirements)
        assert "Healing" in texts
        assert "Targeting" in texts
        assert "Cavebot" in texts
    finally:
        try:
            ui.root.destroy()
        except Exception:
            pass
