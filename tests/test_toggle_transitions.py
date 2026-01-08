from __future__ import annotations

from runtime_config import CavebotConfig, HealingConfig
from src.main import _toggle_transition_lines


def test_toggle_transition_lines_initial_state_prints_both() -> None:
    healing = HealingConfig(enabled=False)
    cavebot = CavebotConfig(enabled=False, route_path="configs/route.json")

    lines, last_h, last_c = _toggle_transition_lines(healing, cavebot, None, None)
    assert lines == ["🩹 Healing OFF", "🧭 Cavebot OFF"]
    assert last_h is False
    assert last_c is False


def test_toggle_transition_lines_enabling_cavebot_prints_route() -> None:
    healing = HealingConfig(enabled=False)
    cavebot = CavebotConfig(enabled=True, route_path="configs/route.json")

    lines, last_h, last_c = _toggle_transition_lines(healing, cavebot, False, False)
    assert lines == ["🧭 Cavebot ON", "🧭 Ruta: configs/route.json"]
    assert last_h is False
    assert last_c is True


def test_toggle_transition_lines_no_change_prints_nothing() -> None:
    healing = HealingConfig(enabled=True)
    cavebot = CavebotConfig(enabled=False, route_path="configs/route.json")

    lines, last_h, last_c = _toggle_transition_lines(healing, cavebot, True, False)
    assert lines == []
    assert last_h is True
    assert last_c is False
