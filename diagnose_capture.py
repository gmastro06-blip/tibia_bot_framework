from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2

# Allow running as a standalone script from repo root.
repo_root = Path(__file__).resolve().parent
src_dir = repo_root / "src"
sys.path.insert(0, str(src_dir))

import win_window
from capture.dxgi_capture import DXGICapture
from input_focus_guard import format_capture_target_overlay_line, update_capture_target_state, update_client_state


def _score_like_guard(title: str, class_name: str) -> int:
    t = (title or "").strip()
    c = (class_name or "").strip()
    if not t and not c:
        return 0

    t_low = t.lower()
    c_low = c.lower()

    # Mirror the heuristics in input_focus_guard (simplified output).
    denied = [
        "chrome",
        "discord",
        "visual studio code",
        "steam",
        "terminal",
        "explorer",
    ]
    for bad in denied:
        if bad in t_low:
            return -999

    score = 0
    if t_low.startswith("tibia -"):
        score += 12
    elif "tibia -" in t_low:
        score += 8
    if "tibia" in t_low:
        score += 5

    hints = ["sdl", "qt", "unity", "unreal", "glfw", "directx"]
    for h in hints:
        if h in c_low:
            score += 2

    return int(score)


def main() -> None:
    win_window.set_dpi_awareness()

    print("== Window candidates (top 15) ==")
    rows = []
    for hwnd in win_window.enum_top_level_windows():
        title = win_window.get_window_title(hwnd)
        cls = win_window.get_window_class(hwnd)
        sc = _score_like_guard(title, cls)
        if sc <= 0:
            continue
        rows.append((sc, hwnd, title, cls))

    rows.sort(key=lambda r: (-r[0], r[1]))
    for sc, hwnd, title, cls in rows[:15]:
        print(f"score={sc:>3} hwnd={hwnd} class={cls} title={title}")

    print("\n== update_client_state() snapshot ==")
    st = update_client_state()
    print(f"hwnd={st.hwnd} title={st.title}")
    print(f"foreground={int(st.is_foreground)} minimized={int(st.is_minimized)} maximized={int(st.is_maximized)}")
    print(f"bounds={st.bounds}")

    print("\n== update_capture_target_state() snapshot ==")
    try:
        ct = update_capture_target_state()
        print(format_capture_target_overlay_line())
        print(
            f"target_class={getattr(ct, 'target_class_name', '')} "
            f"minimized={int(bool(getattr(ct, 'target_is_minimized', False)))} "
            f"maximized={int(bool(getattr(ct, 'target_is_maximized', False)))}"
        )
    except Exception as e:
        print(f"capture_target_state error: {e}")

    print("\n== DXGICapture.capture() test ==")
    cap = DXGICapture(force_monitor=None)
    try:
        cap._verbose = True  # noqa: SLF001 - diagnostic only
    except Exception:
        pass

    frame = None
    for i in range(50):
        frame = cap.capture()
        if frame is not None:
            break
        time.sleep(0.05)

    print(f"capture_state={getattr(cap, 'capture_state', '')}")
    print(f"capture_bounds={getattr(cap, 'capture_bounds', None)}")
    print(f"capture_monitor_index={getattr(cap, 'capture_monitor_index', None)}")

    if frame is None:
        raise SystemExit("No frame captured (is Tibia minimized? wrong window found?)")

    out = repo_root / "logs" / "diagnose_capture.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame)
    print(f"Saved: {out} shape={frame.shape}")


if __name__ == "__main__":
    main()
