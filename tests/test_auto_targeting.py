from __future__ import annotations

import json

from decision.auto_targeting import AutoTargetConfig, AutoTargetingController


class FakeGameState:
    def __init__(self, battlelist_entries: list[dict]) -> None:
        self.battlelist_entries = battlelist_entries


def _kinds(actions) -> list[str]:
    return [a.kind for a in actions]


def test_autotarget_enables_follow() -> None:
    ctrl = AutoTargetingController()
    cfg = AutoTargetConfig(
        enabled=True,
        follow_on_enable=True,
        follow_distance_tiles=2,
        retarget_if_lost_ms=500,
        whitelist=[],
        blacklist=[],
        min_confidence=0.0,
    )

    gs = FakeGameState(
        [
            {"name_display": "Orc", "conf": 0.9},
        ]
    )

    actions = ctrl.tick(gs, now=1000.0, cfg=cfg)
    kinds = _kinds(actions)

    assert "target" in kinds
    assert "follow_target" in kinds

    follow = [a for a in actions if a.kind == "follow_target"][0]
    payload = json.loads(follow.value)
    assert payload["target"].lower() == "orc"
    assert int(payload["dist"]) == 2


def test_target_lock_no_flip_when_visible() -> None:
    ctrl = AutoTargetingController()
    cfg = AutoTargetConfig(enabled=True, min_confidence=0.0)

    gs1 = FakeGameState(
        [
            {"name_display": "Orc", "conf": 0.9},
            {"name_display": "Troll", "conf": 0.9},
        ]
    )
    a1 = ctrl.tick(gs1, now=1000.0, cfg=cfg)
    assert any(a.kind == "target" and a.value.lower() == "orc" for a in a1)

    # Order flips, but current target is still visible -> should not retarget.
    gs2 = FakeGameState(
        [
            {"name_display": "Troll", "conf": 0.9},
            {"name_display": "Orc", "conf": 0.9},
        ]
    )
    a2 = ctrl.tick(gs2, now=1000.1, cfg=cfg)
    assert not any(a.kind == "target" for a in a2)


def test_retarget_when_lost() -> None:
    ctrl = AutoTargetingController()
    cfg = AutoTargetConfig(enabled=True, retarget_if_lost_ms=200, min_confidence=0.0)

    gs1 = FakeGameState([{"name_display": "Orc", "conf": 0.9}])
    _ = ctrl.tick(gs1, now=1000.0, cfg=cfg)

    # Target disappears for longer than threshold, new target appears.
    gs2 = FakeGameState([{"name_display": "Troll", "conf": 0.9}])
    a2 = ctrl.tick(gs2, now=1001.0, cfg=cfg)

    assert any(a.kind == "target" and a.value.lower() == "troll" for a in a2)


def test_disable_stops_follow_and_clears() -> None:
    ctrl = AutoTargetingController()

    cfg_on = AutoTargetConfig(enabled=True, follow_on_enable=True, min_confidence=0.0)
    gs = FakeGameState([{"name_display": "Orc", "conf": 0.9}])
    _ = ctrl.tick(gs, now=1000.0, cfg=cfg_on)

    cfg_off = AutoTargetConfig(enabled=False)
    actions = ctrl.tick(gs, now=1000.1, cfg=cfg_off)

    kinds = _kinds(actions)
    assert "stop_follow" in kinds
    assert "clear_target" in kinds
