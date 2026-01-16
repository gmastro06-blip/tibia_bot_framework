from __future__ import annotations

import types


def _mk_win_window(*, windows: list[dict]):
    """Build a minimal fake win_window module."""

    # windows: [{hwnd:int,title:str,bounds:(l,t,r,b)}]
    by_hwnd = {int(w["hwnd"]): dict(w) for w in windows}

    m = types.SimpleNamespace()

    def set_dpi_awareness() -> None:
        return

    def enum_top_level_windows() -> list[int]:
        return [int(w["hwnd"]) for w in windows]

    def get_window_title(hwnd: int) -> str:
        return str(by_hwnd.get(int(hwnd), {}).get("title", ""))

    def get_window_class(hwnd: int) -> str:
        return "FakeClass"

    def is_minimized(hwnd: int) -> bool:
        return False

    def is_maximized(hwnd: int) -> bool:
        return True

    def get_best_bounds(hwnd: int):
        return by_hwnd.get(int(hwnd), {}).get("bounds")

    m.set_dpi_awareness = set_dpi_awareness
    m.enum_top_level_windows = enum_top_level_windows
    m.get_window_title = get_window_title
    m.get_window_class = get_window_class
    m.is_minimized = is_minimized
    m.is_maximized = is_maximized
    m.get_best_bounds = get_best_bounds

    return m


def test_capture_target_auto_prefers_obs(monkeypatch):
    import input_focus_guard

    fake = _mk_win_window(
        windows=[
            {
                "hwnd": 11,
                "title": "Tibia - Nombre del usuario",
                "bounds": (0, 0, 800, 600),
            },
            {
                "hwnd": 22,
                "title": "OBS Fullscreen Projector (Scene)",
                "bounds": (0, 0, 1920, 1080),
            },
        ]
    )
    monkeypatch.setattr(input_focus_guard, "win_window", fake)

    hwnd, reason = input_focus_guard.choose_capture_target_hwnd(capture_target="auto", extra_title_hints=[])
    assert hwnd == 22
    assert reason == "obs_projector"


def test_capture_target_fallback_to_client(monkeypatch):
    import input_focus_guard

    fake = _mk_win_window(
        windows=[
            {
                "hwnd": 11,
                "title": "Tibia - Nombre del usuario",
                "bounds": (0, 0, 1280, 720),
            }
        ]
    )
    monkeypatch.setattr(input_focus_guard, "win_window", fake)

    # No OBS present => choose client
    hwnd, reason = input_focus_guard.choose_capture_target_hwnd(capture_target="auto", extra_title_hints=[])
    assert hwnd == 11
    assert reason == "client"


def test_choose_largest_window_on_multiple_matches(monkeypatch):
    import input_focus_guard

    fake = _mk_win_window(
        windows=[
            {
                "hwnd": 21,
                "title": "OBS Windowed Projector (Small)",
                "bounds": (0, 0, 800, 600),
            },
            {
                "hwnd": 22,
                "title": "OBS Fullscreen Projector (Big)",
                "bounds": (0, 0, 1920, 1080),
            },
            {
                "hwnd": 11,
                "title": "Tibia - Nombre del usuario",
                "bounds": (0, 0, 1024, 768),
            },
        ]
    )
    monkeypatch.setattr(input_focus_guard, "win_window", fake)

    hwnd, reason = input_focus_guard.choose_capture_target_hwnd(capture_target="auto", extra_title_hints=[])
    assert hwnd == 22
    assert reason == "obs_projector"


def test_capture_target_spanish_projector_title(monkeypatch):
    import input_focus_guard

    fake = _mk_win_window(
        windows=[
            {
                "hwnd": 11,
                "title": "Tibia - Nombre del usuario",
                "bounds": (0, 0, 1024, 768),
            },
            {
                "hwnd": 33,
                "title": "Proyector en ventana (Fuente) - Tibia_Fuente",
                "bounds": (0, 0, 1600, 900),
            },
        ]
    )
    monkeypatch.setattr(input_focus_guard, "win_window", fake)

    hwnd, reason = input_focus_guard.choose_capture_target_hwnd(capture_target="auto", extra_title_hints=[])
    assert hwnd == 33
    assert reason == "obs_projector"
