from tools.smoke_live_hpmp import format_capture_status_line


def test_smoke_prints_backend_and_monitor():
    s = format_capture_status_line(
        backend="projector_bitblt",
        window_title="Proyector en ventana (Fuente) - Tibia_Fuente",
        monitor_idx=2,
        mean=12.34,
        black_streak=5,
    )
    assert "backend=projector_bitblt" in s
    assert "mon=2" in s
    assert "mean=12.34" in s
    assert "black_streak=5" in s
