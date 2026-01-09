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


def test_runtime_config_simulation_snapshot_is_copy() -> None:
    cfg = RuntimeConfig()
    cfg.update_simulation(enabled=True, paralyzed=True, haste_active=True, utamo_active=False, hungry=True)

    sim1 = cfg.simulation_snapshot()
    sim1.enabled = False
    sim1.paralyzed = False

    sim2 = cfg.simulation_snapshot()
    assert sim2.enabled is True
    assert sim2.paralyzed is True
    assert sim2.haste_active is True
    assert sim2.utamo_active is False
    assert sim2.hungry is True


def test_runtime_config_telemetry_snapshot_is_copy() -> None:
    cfg = RuntimeConfig()
    cfg.update_telemetry(hp_current=50, hp_max=100, hp_pct=50.0, low_hp=True, note="ok")

    t1 = cfg.telemetry_snapshot()
    t1.hp_current = 1
    t1.note = "mutated"

    t2 = cfg.telemetry_snapshot()
    assert t2.hp_current == 50
    assert t2.hp_max == 100
    assert t2.hp_pct == 50.0
    assert t2.low_hp is True
    assert t2.note == "ok"


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
