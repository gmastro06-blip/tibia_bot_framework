from __future__ import annotations

import sys
import types

import pytest


def test_ui_configuration_tab_calls_update_simulation(monkeypatch) -> None:
    # Tkinter puede no estar disponible en algunos entornos headless; en ese caso skip.
    try:
        import tkinter  # noqa: F401
    except Exception as e:
        pytest.skip(f"Tkinter no disponible: {e}")

    # Evitar que el UI importe el main real (captura/roboflow/etc.).
    fake_main = types.ModuleType("main")

    def _fake_run_bot(*args, **kwargs):
        return None

    fake_main.run_bot = _fake_run_bot  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "main", fake_main)

    # Espiar llamadas a update_simulation.
    import runtime_config as rc

    calls: list[dict[str, object]] = []
    assistant_calls: list[dict[str, object]] = []
    replay_calls: list[dict[str, object]] = []
    logging_calls: list[dict[str, object]] = []
    OriginalRuntimeConfig = rc.RuntimeConfig

    class SpyRuntimeConfig(OriginalRuntimeConfig):
        def update_simulation(self, **kwargs):  # type: ignore[override]
            calls.append(dict(kwargs))
            return super().update_simulation(**kwargs)

        def update_assistant(self, **kwargs):  # type: ignore[override]
            assistant_calls.append(dict(kwargs))
            return super().update_assistant(**kwargs)

        def update_replay(self, **kwargs):  # type: ignore[override]
            replay_calls.append(dict(kwargs))
            return super().update_replay(**kwargs)

        def update_logging(self, **kwargs):  # type: ignore[override]
            logging_calls.append(dict(kwargs))
            return super().update_logging(**kwargs)

    monkeypatch.setattr(rc, "RuntimeConfig", SpyRuntimeConfig)

    import run_bot_ui

    ui = run_bot_ui.BotUI()
    try:
        # Minimizar UI real (no abrir ventana visible)
        try:
            ui.root.withdraw()
        except Exception:
            pass

        # Estado inicial ya llama sync_simulation()
        assert calls, "Se esperaba al menos una llamada inicial a update_simulation()"

        # Cambiar toggles debería propagar a RuntimeConfig via trace_add.
        ui.sim_enabled.set(True)
        ui.sim_paralyzed.set(True)
        ui.sim_haste_active.set(False)
        ui.sim_utamo_active.set(True)
        ui.sim_hungry.set(True)

        sim = ui._config.simulation_snapshot()
        assert sim.enabled is True
        assert sim.paralyzed is True
        assert sim.haste_active is False
        assert sim.utamo_active is True
        assert sim.hungry is True

        # Deshabilitar simulación debe actualizar enabled.
        ui.sim_enabled.set(False)
        sim2 = ui._config.simulation_snapshot()
        assert sim2.enabled is False

        # Asegurar que hubo múltiples propagaciones.
        assert len(calls) >= 2
        assert any(c.get("paralyzed") is True for c in calls)
        assert any(c.get("enabled") is False for c in calls)

        # Toggles del modo asistente también deben propagarse.
        ui.asst_enabled.set(True)
        ui.asst_confirm.set(True)
        ui.asst_sound.set(False)

        a = ui._config.assistant_snapshot()
        assert a.enabled is True
        assert a.confirm_actions is True
        assert a.sound_alerts is False
        assert assistant_calls, "Se esperaba al menos una llamada a update_assistant()"
        assert any(c.get("sound_alerts") is False for c in assistant_calls)

        # Replay + logging: el estado inicial ya debería sincronizar.
        assert replay_calls, "Se esperaba al menos una llamada inicial a update_replay()"
        assert logging_calls, "Se esperaba al menos una llamada inicial a update_logging()"
        assert any("out_dir" in c for c in replay_calls)
        assert any("out_file" in c for c in logging_calls)

        ui.replay_enabled.set(True)
        ui.replay_interval_ms.set(1500)
        ui.replay_out_dir.set("logs/replay_test")
        ui.log_enabled.set(True)
        ui.log_interval_ms.set(500)
        ui.log_out_file.set("logs/telemetry_test.jsonl")

        rep = ui._config.replay_snapshot()
        assert rep.enabled is True
        assert rep.interval_ms == 1500
        assert rep.out_dir == "logs/replay_test"

        log = ui._config.logging_snapshot()
        assert log.enabled is True
        assert log.interval_ms == 500
        assert log.out_file == "logs/telemetry_test.jsonl"

        assert any(c.get("enabled") is True for c in replay_calls)
        assert any(c.get("interval_ms") == 1500 for c in replay_calls)
        assert any(c.get("out_dir") == "logs/replay_test" for c in replay_calls)
        assert any(c.get("enabled") is True for c in logging_calls)
        assert any(c.get("interval_ms") == 500 for c in logging_calls)
        assert any(c.get("out_file") == "logs/telemetry_test.jsonl" for c in logging_calls)

    finally:
        try:
            ui.root.destroy()
        except Exception:
            pass
