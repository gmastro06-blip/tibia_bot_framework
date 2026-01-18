from __future__ import annotations

import pytest


def test_dxgi_reacquire_picks_client_over_obs(monkeypatch: pytest.MonkeyPatch) -> None:
    from capture import dxgi_capture

    # Avoid touching real Win32 enumeration.
    monkeypatch.setattr(dxgi_capture.DXGICapture, "find_window", lambda self: 0)

    # Minimal Win32 stubs used by reacquire_target().
    monkeypatch.setattr(dxgi_capture.win32gui, "IsWindow", lambda hwnd: True)
    monkeypatch.setattr(dxgi_capture.win32gui, "GetWindowRect", lambda hwnd: (10, 20, 110, 220))

    # Ensure default policy prefers client titles.
    monkeypatch.setenv("CAPTURE_TARGET", "client")
    monkeypatch.setenv("CAPTURE_TITLE_INCLUDE", "Tibia")
    monkeypatch.setenv("CAPTURE_TITLE_EXCLUDE", "Proyector|Projector|OBS")
    monkeypatch.delenv("CAPTURE_PROCESS_NAME", raising=False)

    cap = dxgi_capture.DXGICapture(force_monitor=None)

    # Provide a controlled window list: OBS projector and real Tibia client.
    def _fake_list_visible_windows():
        return [
            (101, "Proyector en ventana (Fuente) - Tibia_Fuente", 111),
            (202, "Tibia - PlayerName", 222),
        ]

    monkeypatch.setattr(cap, "_list_visible_windows", _fake_list_visible_windows)

    def _fake_proc(pid: int) -> str:
        return "obs64.exe" if int(pid) == 111 else "tibia.exe"

    monkeypatch.setattr(cap, "_get_process_name_by_pid", _fake_proc)

    ok = cap.reacquire_target()
    assert ok is True
    assert cap.client_hwnd == 202
    assert "tibia" in cap.client_title.lower()
    assert cap.capture_state == "reacquired"
    assert cap.target_found is True
    assert cap.capture_bounds == (10, 20, 110, 220)
