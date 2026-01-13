from __future__ import annotations

from decision.waypoint_actions import WaypointActionConfig, build_requests_from_waypoint_action


def test_waypoint_actions_parses_known_actions_preview() -> None:
    cfg = WaypointActionConfig(
        quick_loot_hotkey="ctrl+l",
        rope_hotkey="r",
        shovel_hotkey="s",
        weapon_switch="ws",
    )

    reqs = build_requests_from_waypoint_action(
        "loot; rope | shovel ; antitrap ; buy_potions ; custom_label",
        committed=False,
        cfg=cfg,
    )

    assert [r.kind for r in reqs] == ["loot", "tool", "tool", "switch", "trade", "waypoint_action"]
    assert [r.note for r in reqs] == ["preview"] * len(reqs)
    assert reqs[0].value == "ctrl+l"
    assert reqs[1].value == "r"
    assert reqs[2].value == "s"
    assert reqs[3].value == "ws"
    assert reqs[4].value == "buy_potions"
    assert reqs[5].value == "custom_label"


def test_waypoint_actions_sets_committed_note() -> None:
    cfg = WaypointActionConfig(quick_loot_hotkey="L")
    reqs = build_requests_from_waypoint_action("quick_loot", committed=True, cfg=cfg)
    assert len(reqs) == 1
    assert reqs[0].kind == "loot"
    assert reqs[0].note == "committed"


def test_waypoint_actions_supports_note_wait_beep_require() -> None:
    reqs = build_requests_from_waypoint_action(
        "note:Hello World; wait:750; beep:660:120; require:!low_hp,coords_ok",
        committed=False,
    )

    assert [r.kind for r in reqs] == ["note", "wait", "beep", "require"]
    assert [r.note for r in reqs] == ["preview"] * len(reqs)
    assert reqs[0].value == "Hello World"
    assert reqs[1].value == "750"
    assert reqs[2].value == "660:120"
    assert reqs[3].value == "!low_hp,coords_ok"
