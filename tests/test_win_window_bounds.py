from __future__ import annotations

import win_window


def test_get_best_bounds_prefers_extended_frame(monkeypatch) -> None:
    monkeypatch.setattr(win_window, "get_extended_frame_bounds", lambda hwnd: (1, 2, 3, 4))
    monkeypatch.setattr(win_window, "get_client_rect_screen", lambda hwnd: (10, 20, 30, 40))
    monkeypatch.setattr(win_window, "get_window_rect", lambda hwnd: (100, 200, 300, 400))

    assert win_window.get_best_bounds(999) == (1, 2, 3, 4)


def test_get_best_bounds_falls_back_to_client_rect(monkeypatch) -> None:
    monkeypatch.setattr(win_window, "get_extended_frame_bounds", lambda hwnd: None)
    monkeypatch.setattr(win_window, "get_client_rect_screen", lambda hwnd: (10, 20, 30, 40))
    monkeypatch.setattr(win_window, "get_window_rect", lambda hwnd: (100, 200, 300, 400))

    assert win_window.get_best_bounds(999) == (10, 20, 30, 40)


def test_get_best_bounds_falls_back_to_window_rect(monkeypatch) -> None:
    monkeypatch.setattr(win_window, "get_extended_frame_bounds", lambda hwnd: None)
    monkeypatch.setattr(win_window, "get_client_rect_screen", lambda hwnd: None)
    monkeypatch.setattr(win_window, "get_window_rect", lambda hwnd: (100, 200, 300, 400))

    assert win_window.get_best_bounds(999) == (100, 200, 300, 400)
