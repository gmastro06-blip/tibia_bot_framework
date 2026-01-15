from __future__ import annotations

import input_focus_guard as fg


def test_find_client_hwnd_anywhere_prefers_allowed_title(monkeypatch) -> None:
    monkeypatch.setattr(fg.win_window, "set_dpi_awareness", lambda: None)
    monkeypatch.setattr(fg.win_window, "enum_top_level_windows", lambda: [101, 102, 103])

    titles = {
        101: "Discord",
        102: "Tibia - CharName",
        103: "Some Other App",
    }
    classes = {
        101: "Chrome_WidgetWin_1",
        102: "SDL_app",
        103: "Other",
    }

    monkeypatch.setattr(fg.win_window, "get_window_title", lambda hwnd: titles.get(int(hwnd), ""))
    monkeypatch.setattr(fg.win_window, "get_window_class", lambda hwnd: classes.get(int(hwnd), ""))

    assert fg.find_client_hwnd_anywhere() == 102


def test_find_client_hwnd_anywhere_denied_titles_win(monkeypatch) -> None:
    monkeypatch.setattr(fg.win_window, "set_dpi_awareness", lambda: None)
    monkeypatch.setattr(fg.win_window, "enum_top_level_windows", lambda: [201, 202])

    titles = {
        201: "Tibia - Discord Overlay",
        202: "Tibia - Real Client",
    }
    classes = {
        201: "SDL_app",
        202: "SDL_app",
    }

    monkeypatch.setattr(fg.win_window, "get_window_title", lambda hwnd: titles.get(int(hwnd), ""))
    monkeypatch.setattr(fg.win_window, "get_window_class", lambda hwnd: classes.get(int(hwnd), ""))

    assert (
        fg.find_client_hwnd_anywhere(
            allowed_window_titles=["Tibia"],
            denied_titles_contains=["Discord"],
        )
        == 202
    )


def test_is_allowed_to_inject_blocks_when_minimized(monkeypatch) -> None:
    monkeypatch.setattr(fg.win_window, "is_minimized", lambda hwnd: True)
    monkeypatch.setattr(fg.win_window, "get_foreground_hwnd", lambda: 123)

    ok, reason = fg.is_allowed_to_inject(123)
    assert ok is False
    assert reason == "client_minimized"


def test_is_allowed_to_inject_blocks_when_not_foreground(monkeypatch) -> None:
    monkeypatch.setattr(fg.win_window, "is_minimized", lambda hwnd: False)
    monkeypatch.setattr(fg.win_window, "get_foreground_hwnd", lambda: 999)

    ok, reason = fg.is_allowed_to_inject(123)
    assert ok is False
    assert reason == "not_foreground"


def test_is_allowed_to_inject_allows_when_foreground(monkeypatch) -> None:
    monkeypatch.setattr(fg.win_window, "is_minimized", lambda hwnd: False)
    monkeypatch.setattr(fg.win_window, "get_foreground_hwnd", lambda: 123)

    ok, reason = fg.is_allowed_to_inject(123)
    assert ok is True
    assert reason == "ok"


def test_refresh_client_hwnd_keeps_prev_when_title_matches(monkeypatch) -> None:
    monkeypatch.setattr(fg.win_window, "get_window_title", lambda hwnd: "Tibia - Foo")
    monkeypatch.setattr(fg, "find_client_hwnd_anywhere", lambda **_kw: 777)

    assert fg.refresh_client_hwnd(555, allowed_window_titles=["Tibia"]) == 555
