from __future__ import annotations

import json
import sys
import types

import pytest


pytestmark = pytest.mark.ui


def test_ui_settings_roundtrip_preserves_unknown_keys(monkeypatch, tmp_path) -> None:
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

    settings_path = tmp_path / "ui_settings.json"
    seed = {
        "version": 999,
        "unknown_root": {"hello": "world"},
        "cavebot": {
            "enabled": True,
            "route_path": "configs/route.json",
            "unknown_cb": 123,
            "alerts_matrix": [
                {"condition": "low_hp", "beep": True, "stop": False, "note": ""},
            ],
        },
        "targeting": {
            "profile_name": "p1",
            "rules": {"monsters": ["orc"]},
            "unknown_t": True,
        },
    }
    settings_path.write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")

    monkeypatch.setenv("UI_SETTINGS_FILE", str(settings_path))

    import run_bot_ui

    try:
        ui = run_bot_ui.BotUI()
    except (SystemExit, Exception) as e:
        # En entornos headless, Tk puede importar pero fallar al crear la ventana.
        pytest.skip(f"Tk no disponible/usable en este entorno: {e}")

    try:
        try:
            ui.root.withdraw()
        except Exception:
            pass

        ui._save_ui_settings()

        out = json.loads(settings_path.read_text(encoding="utf-8"))
        assert out.get("unknown_root") == {"hello": "world"}
        assert out.get("cavebot", {}).get("unknown_cb") == 123
        assert out.get("targeting", {}).get("unknown_t") is True

        # Also ensure the known persisted fields are still present.
        assert out.get("targeting", {}).get("profile_name") == "p1"
        assert out.get("cavebot", {}).get("alerts_matrix"), "Expected alerts_matrix to be preserved"

    finally:
        try:
            ui.root.destroy()
        except Exception:
            pass
