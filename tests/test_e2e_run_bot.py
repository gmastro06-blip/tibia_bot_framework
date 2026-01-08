from __future__ import annotations

import threading
import time

import numpy as np

from runtime_config import RuntimeConfig


def test_run_bot_end_to_end_smoke(monkeypatch, capsys) -> None:
    """E2E (mocked) regression test.

    Runs the full threaded pipeline via `run_bot()` but replaces real capture
    + vision with deterministic fakes so it doesn't depend on any real monitor,
    OCR, Roboflow, etc.

    Verifies:
    - Threads start
    - GameState flows through to decision thread
    - RuntimeConfig changes are observed live (logs)
    - Bot stops cleanly via stop_event
    """

    from src import main as mainmod

    class FakeCapture:
        def __init__(self, force_monitor: int = 2):
            self.force_monitor = force_monitor

        def capture(self):
            # 1920x1080 BGR-like frame
            frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            frame[:, :, 1] = 100  # add some non-black content
            return frame

    class FakeGameState:
        def __init__(self, hp_cur: int, hp_max: int, mp_cur: int, mp_max: int):
            self.hp_current = hp_cur
            self.hp_max = hp_max
            self.mp_current = mp_cur
            self.mp_max = mp_max

    class FakeBuilder:
        def __init__(self):
            self._ticks = 0

        def update_from_frame(self, frame, rois, resolution):
            self._ticks += 1
            # Drop HP over time so healing trigger is predictable.
            hp_cur = max(1, 100 - (self._ticks * 5))
            return FakeGameState(hp_cur=hp_cur, hp_max=100, mp_cur=50, mp_max=100)

    monkeypatch.setattr(mainmod, "DXGICapture", FakeCapture)
    monkeypatch.setattr(mainmod, "GameStateBuilder", FakeBuilder)

    stop_event = threading.Event()
    runtime_config = RuntimeConfig()

    t = threading.Thread(
        target=mainmod.run_bot,
        kwargs={"stop_event": stop_event, "runtime_config": runtime_config},
        daemon=True,
    )
    t.start()

    # Let threads spin up and process a couple frames.
    time.sleep(0.6)

    # Enable healing high threshold to trigger quickly.
    runtime_config.update_healing(enabled=True, hp_below_pct=98, mp_below_pct=98, action="exura")

    # Give decision thread time to observe config + trigger.
    time.sleep(1.0)

    stop_event.set()
    t.join(timeout=3.0)
    assert not t.is_alive(), "run_bot thread should stop after stop_event is set"

    out = capsys.readouterr().out

    assert "📸 Thread de captura iniciado" in out
    assert "👁️  Thread de visión iniciado" in out
    assert "🧠 Thread de decisión iniciado" in out

    # Should print game state at least once.
    assert "🎮 Estado: HP" in out

    # Should observe runtime config changes live.
    assert "🩹 Healing ON" in out

    # Trigger should fire at least once given high threshold and dropping HP.
    assert "🩹 Healing TRIGGER" in out
