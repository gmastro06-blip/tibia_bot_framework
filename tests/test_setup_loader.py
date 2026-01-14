import json
from pathlib import Path

from route_editor.setup_loader import load_setup, save_setup


def test_setup_roundtrip_tmp(tmp_path: Path):
    payload = {
        "general": {"loot_type": "auto"},
        "hunt_config": {"cap_leave": 10, "mana_name": "mana potion", "take_mana": 5, "mana_leave": 2},
        "items": {"mana potion": {"hotkey": "f1", "use": "self"}},
        "custom": {"foo": 123},
    }
    p = tmp_path / "setup.json"
    p.write_text(json.dumps(payload), encoding="utf-8")

    cfg = load_setup(p)
    cfg.set_hunt_field("cap_leave", 20)
    cfg.set_item("mana potion", "f2", "self")
    save_setup(cfg, p)

    reloaded = json.loads(p.read_text(encoding="utf-8"))
    assert reloaded["hunt_config"]["cap_leave"] == 20
    assert reloaded["items"]["mana potion"]["hotkey"] == "f2"
    # Preserve unknown
    assert reloaded["custom"]["foo"] == 123
