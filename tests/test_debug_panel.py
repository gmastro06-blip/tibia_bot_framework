from pathlib import Path

import numpy as np


def test_build_debug_panel_has_crosshair() -> None:
    from telemetry.debug_panel import build_debug_panel

    motion = np.zeros((64, 64), dtype=np.uint8)
    # Simple diagonal trace
    for i in range(10, 54):
        motion[i, i] = 255

    img = build_debug_panel(
        status_line="INJECTION: DISABLED (input_mode=log)",
        coords_line="pos=(100,200,7) provider=minimap conf=0.80 green",
        motion_img=motion,
        metrics={
            "response": 0.88,
            "dx_tiles": 0.25,
            "dy_tiles": -0.50,
            "acc_dx": 1.25,
            "acc_dy": -0.75,
        },
        size=(800, 600),
    )

    assert img.shape == (600, 800, 3)
    assert img.dtype == np.uint8

    # Crosshair center pixel should be white-ish (we draw a filled circle).
    cy = img.shape[0] // 2
    cx = img.shape[1] // 2
    assert int(img[cy, cx].max()) >= 200


def test_debug_panel_exporter_writes_atomic(tmp_path: Path, monkeypatch) -> None:
    # Ensure exporter is enabled via env config.
    monkeypatch.setenv("DEBUG_PANEL_ENABLED", "1")
    monkeypatch.setenv("DEBUG_PANEL_FPS", "100")

    out_file = tmp_path / "debug_panel.png"
    monkeypatch.setenv("DEBUG_PANEL_OUT_FILE", str(out_file))

    from telemetry.debug_panel import DebugPanelExporter, debug_panel_config_from_env

    ex = DebugPanelExporter(debug_panel_config_from_env())

    ex.maybe_export(
        status_line="INJECTION: ARMED (advance_pulse_pending)",
        coords_line="pos=(1,2,3) provider=minimap conf=0.50 amber",
        response=0.9,
        dx_tiles=1.0,
        dy_tiles=0.0,
        acc_dx=2.0,
        acc_dy=-1.0,
        now=123.0,
    )

    assert out_file.exists()
    assert out_file.stat().st_size > 0

    # Second write should still succeed and not leave tmp file around.
    ex.maybe_export(
        status_line="INJECTION: ARMED (advance_pulse_pending)",
        coords_line="pos=(1,2,3) provider=minimap conf=0.50 amber",
        response=0.91,
        dx_tiles=0.0,
        dy_tiles=1.0,
        acc_dx=2.0,
        acc_dy=-1.0,
        now=124.0,
    )

    assert out_file.exists()
    assert out_file.stat().st_size > 0
    assert not (tmp_path / "debug_panel.png.tmp").exists()
