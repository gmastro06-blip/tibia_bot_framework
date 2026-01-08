from __future__ import annotations

import threading

from runtime_config import RuntimeConfig


def test_runtime_config_snapshot_is_copy() -> None:
    cfg = RuntimeConfig()

    cfg.update_healing(enabled=True, hp_below_pct=80, mp_below_pct=25, action="exura")
    cfg.update_cavebot(enabled=True, route_path="configs/route.json")

    healing1, cavebot1 = cfg.snapshot()

    # Mutate returned objects; should not affect internal state.
    healing1.enabled = False
    healing1.hp_below_pct = 1
    cavebot1.enabled = False
    cavebot1.route_path = "X"

    healing2, cavebot2 = cfg.snapshot()
    assert healing2.enabled is True
    assert healing2.hp_below_pct == 80
    assert healing2.mp_below_pct == 25
    assert healing2.action == "exura"

    assert cavebot2.enabled is True
    assert cavebot2.route_path == "configs/route.json"


def test_runtime_config_thread_safety_smoke() -> None:
    cfg = RuntimeConfig()

    def writer() -> None:
        for i in range(200):
            cfg.update_healing(enabled=(i % 2 == 0), hp_below_pct=60 + (i % 30))
            cfg.update_cavebot(enabled=(i % 3 == 0))

    t = threading.Thread(target=writer)
    t.start()

    # Reader loop should never throw and should always return coherent types.
    for _ in range(200):
        healing, cavebot = cfg.snapshot()
        assert isinstance(healing.enabled, bool)
        assert isinstance(healing.hp_below_pct, int)
        assert isinstance(cavebot.enabled, bool)
        assert isinstance(cavebot.route_path, str)

    t.join(timeout=2.0)
    assert not t.is_alive()
