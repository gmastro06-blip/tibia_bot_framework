from __future__ import annotations

import types


def _mk_win_window(*, hwnd: int, minimized: bool):
    m = types.SimpleNamespace()

    def set_dpi_awareness() -> None:
        return

    def enum_top_level_windows() -> list[int]:
        return [int(hwnd)]

    def get_window_title(_hwnd: int) -> str:
        return "Proyector en ventana (Fuente) - Tibia_Fuente"

    def get_window_class(_hwnd: int) -> str:
        return "FakeClass"

    def is_minimized(_hwnd: int) -> bool:
        return bool(minimized)

    def is_maximized(_hwnd: int) -> bool:
        return False

    def get_best_bounds(_hwnd: int):
        return (0, 0, 800, 600)

    m.set_dpi_awareness = set_dpi_awareness
    m.enum_top_level_windows = enum_top_level_windows
    m.get_window_title = get_window_title
    m.get_window_class = get_window_class
    m.is_minimized = is_minimized
    m.is_maximized = is_maximized
    m.get_best_bounds = get_best_bounds

    return m


def test_capture_force_minimized_env_overrides_win_state(monkeypatch):
    import input_focus_guard

    fake = _mk_win_window(hwnd=123, minimized=False)
    monkeypatch.setattr(input_focus_guard, "win_window", fake)

    # Force target selection to the fake projector window.
    monkeypatch.setenv("CAPTURE_TARGET", "obs_projector")
    monkeypatch.setenv("CAPTURE_FORCE_MINIMIZED", "1")

    st = input_focus_guard.update_capture_target_state()
    assert st.target_hwnd == 123
    assert st.target_is_minimized is True
