from __future__ import annotations

from src.route_editor.macro_recorder import MacroRecorder


def test_records_node_on_pos_change() -> None:
    rec = MacroRecorder()
    rec.start(route_dir="routes/foo", ts=100.0)

    rec.on_tick(100.0, (1, 2, 3), [])
    rec.on_tick(100.1, (1, 2, 3), [])
    rec.on_tick(100.2, (2, 2, 3), [])

    nodes = [e for e in rec.events if e.get("type") == "node"]
    assert len(nodes) == 2
    assert nodes[0]["x"] == 1 and nodes[0]["y"] == 2 and nodes[0]["z"] == 3
    assert nodes[1]["x"] == 2 and nodes[1]["y"] == 2 and nodes[1]["z"] == 3


def test_actions_committed_only_default() -> None:
    rec = MacroRecorder()
    rec.start(route_dir="routes/foo", ts=10.0)

    actions = [
        {"kind": "heal", "value": "F1", "note": "preview", "committed": False},
        {"kind": "move", "value": "north", "note": "committed", "committed": True},
    ]

    rec.on_tick(10.0, (1, 2, 3), actions)

    act_events = [e for e in rec.events if e.get("type") == "action"]
    assert len(act_events) == 1
    assert act_events[0]["kind"] == "move"
    assert act_events[0]["committed"] is True


def test_action_dedup_signature() -> None:
    rec = MacroRecorder()
    rec.start(route_dir="routes/foo", ts=1.0)

    actions = [{"kind": "move", "value": "north", "committed": True}]
    rec.on_tick(1.0, (1, 2, 3), actions)
    rec.on_tick(1.1, (1, 2, 3), actions)

    act_events = [e for e in rec.events if e.get("type") == "action"]
    assert len(act_events) == 1


def test_action_records_without_position_as_nulls() -> None:
    rec = MacroRecorder()
    rec.start(route_dir="routes/foo", ts=1.0)

    actions = [{"kind": "heal", "value": "F2", "committed": True}]
    rec.on_tick(1.0, None, actions)

    act_events = [e for e in rec.events if e.get("type") == "action"]
    assert len(act_events) == 1
    assert act_events[0]["x"] is None
    assert act_events[0]["y"] is None
    assert act_events[0]["z"] is None
