from __future__ import annotations

from decision.behavior_tree import BehaviorTreeRunner
from decision.signals import SignalResult
from runtime_config import HealingConfig


def test_bt_healing_move_waypoint_food() -> None:
    bt = BehaviorTreeRunner()

    sig = SignalResult(
        hp_current=100,
        hp_max=200,
        hp_pct=50.0,
        mp_current=50,
        mp_max=100,
        mp_pct=50.0,
        low_hp=True,
        low_mp=False,
        paralyzed=None,
        haste_active=None,
        utamo_active=None,
        hungry=None,
        healing_trigger=True,
    )

    heal_cfg = HealingConfig(enabled=True, hp_below_pct=70, mp_below_pct=30, action="exura")

    reqs = bt.tick(
        sig=sig,
        healing_cfg=heal_cfg,
        target_cls="",
        target_conf=None,
        cavebot_next="north",
        cavebot_action="loot;rope",
        commit_flag=True,
        eat_food=True,
    )

    # Order matters (Sequence): heal -> move -> waypoint actions -> food
    assert len(reqs) >= 5
    assert reqs[0].kind == "heal" and reqs[0].value == "exura" and reqs[0].note == "preview"
    assert reqs[1].kind == "move" and reqs[1].value == "north" and reqs[1].note == "committed"

    # Waypoint parsed actions
    kinds = [r.kind for r in reqs]
    assert "loot" in kinds
    assert "tool" in kinds

    # Maintenance
    assert any(r.kind == "maintenance" and r.value == "eat_food" for r in reqs)


def test_bt_priority_heal_then_target_then_move(monkeypatch) -> None:
    monkeypatch.setenv("BT_BEEP_ON_TARGET", "0")

    bt = BehaviorTreeRunner()

    sig = SignalResult(
        hp_current=100,
        hp_max=200,
        hp_pct=50.0,
        mp_current=50,
        mp_max=100,
        mp_pct=50.0,
        low_hp=True,
        low_mp=False,
        paralyzed=None,
        haste_active=None,
        utamo_active=None,
        hungry=None,
        healing_trigger=True,
    )

    heal_cfg = HealingConfig(enabled=True, hp_below_pct=70, mp_below_pct=30, action="exura")

    reqs = bt.tick(
        sig=sig,
        healing_cfg=heal_cfg,
        target_cls="orc",
        target_conf=0.9,
        cavebot_next="north",
        cavebot_action="loot;rope",
        commit_flag=True,
        eat_food=True,
    )

    kinds = [r.kind for r in reqs]
    assert kinds[:3] == ["heal", "target", "move"]


def test_bt_disabled_conditions_produce_no_actions() -> None:
    bt = BehaviorTreeRunner()

    sig = SignalResult(
        hp_current=100,
        hp_max=200,
        hp_pct=50.0,
        mp_current=50,
        mp_max=100,
        mp_pct=50.0,
        low_hp=False,
        low_mp=False,
        paralyzed=None,
        haste_active=None,
        utamo_active=None,
        hungry=None,
        healing_trigger=False,
    )

    heal_cfg = HealingConfig(enabled=False, action="exura")

    reqs = bt.tick(
        sig=sig,
        healing_cfg=heal_cfg,
        target_cls="",
        target_conf=None,
        cavebot_next="",
        cavebot_action="",
        commit_flag=False,
        eat_food=False,
    )

    assert reqs == []


def test_bt_beep_on_target_change(monkeypatch) -> None:
    monkeypatch.setenv("BT_BEEP_ON_TARGET", "1")
    # Cooldown is covered by a dedicated test; disable it here.
    monkeypatch.setenv("BT_BEEP_TARGET_COOLDOWN_S", "0")

    bt = BehaviorTreeRunner()

    sig = SignalResult(
        hp_current=None,
        hp_max=None,
        hp_pct=None,
        mp_current=None,
        mp_max=None,
        mp_pct=None,
        low_hp=None,
        low_mp=None,
        paralyzed=None,
        haste_active=None,
        utamo_active=None,
        hungry=None,
        healing_trigger=False,
    )

    heal_cfg = HealingConfig(enabled=False, action="")

    # First target -> should emit a beep request.
    reqs1 = bt.tick(
        sig=sig,
        healing_cfg=heal_cfg,
        target_cls="orc",
        target_conf=0.9,
        cavebot_next="",
        cavebot_action="",
        commit_flag=False,
        eat_food=False,
    )
    assert any(r.kind == "beep" for r in reqs1)

    # Same target again -> no new beep.
    reqs2 = bt.tick(
        sig=sig,
        healing_cfg=heal_cfg,
        target_cls="orc",
        target_conf=0.9,
        cavebot_next="",
        cavebot_action="",
        commit_flag=False,
        eat_food=False,
    )
    assert not any(r.kind == "beep" for r in reqs2)

    # Different target -> beep again.
    reqs3 = bt.tick(
        sig=sig,
        healing_cfg=heal_cfg,
        target_cls="dragon",
        target_conf=0.9,
        cavebot_next="",
        cavebot_action="",
        commit_flag=False,
        eat_food=False,
    )
    assert any(r.kind == "beep" for r in reqs3)


def test_bt_beep_uses_bestiary_priority_when_enabled(monkeypatch, tmp_path) -> None:
    # Enable bestiary and configure a priority list.
    cfg = tmp_path / "bestiary_match.yaml"
    cfg.write_text(
        """
thresholds:
    reliable: 92
    probable: 85
    ambiguous_delta: 5
indexing:
    len_tolerance: 3
    first_letter: true
rules:
    aliases: {}
    ignore: []
    priority: [orc, dragon]
""".lstrip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("BT_BEEP_ON_TARGET", "1")
    monkeypatch.setenv("BT_BEEP_TARGET_USE_BESTIARY", "1")
    monkeypatch.delenv("BT_BEEP_TARGET_CLASSES", raising=False)
    monkeypatch.setenv("BESTIARY_ENABLED", "1")
    monkeypatch.setenv("BESTIARY_CONFIG_PATH", str(cfg))

    bt = BehaviorTreeRunner()

    sig = SignalResult(
        hp_current=None,
        hp_max=None,
        hp_pct=None,
        mp_current=None,
        mp_max=None,
        mp_pct=None,
        low_hp=None,
        low_mp=None,
        paralyzed=None,
        haste_active=None,
        utamo_active=None,
        hungry=None,
        healing_trigger=False,
    )
    heal_cfg = HealingConfig(enabled=False, action="")

    # Not in bestiary priority -> no beep.
    reqs1 = bt.tick(
        sig=sig,
        healing_cfg=heal_cfg,
        target_cls="rat",
        target_conf=0.9,
        cavebot_next="",
        cavebot_action="",
        commit_flag=False,
        eat_food=False,
    )
    assert not any(r.kind == "beep" for r in reqs1)

    # Priority class -> beep.
    reqs2 = bt.tick(
        sig=sig,
        healing_cfg=heal_cfg,
        target_cls="orc",
        target_conf=0.9,
        cavebot_next="",
        cavebot_action="",
        commit_flag=False,
        eat_food=False,
    )
    assert any(r.kind == "beep" for r in reqs2)


def test_bt_beep_tiered_values_from_bestiary(monkeypatch, tmp_path) -> None:
    cfg = tmp_path / "bestiary_match.yaml"
    cfg.write_text(
        """
thresholds:
    reliable: 92
    probable: 85
    ambiguous_delta: 5
indexing:
    len_tolerance: 99
    first_letter: false
rules:
    aliases: {}
    ignore: []
    priority: []
""".lstrip(),
        encoding="utf-8",
    )

    reg = tmp_path / "creatures_registry.json"
    reg.write_text('{"orc": {"hp": 150}, "dragon": {"hp": 5000}}', encoding="utf-8")

    corr = tmp_path / "ocr_corrections.json"
    corr.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("BT_BEEP_ON_TARGET", "1")
    monkeypatch.setenv("BT_BEEP_TARGET_TIERED", "1")
    monkeypatch.setenv("BESTIARY_ENABLED", "1")
    monkeypatch.setenv("BESTIARY_CONFIG_PATH", str(cfg))
    monkeypatch.setenv("BESTIARY_REGISTRY_PATH", str(reg))
    monkeypatch.setenv("BESTIARY_OCR_CORRECTIONS_PATH", str(corr))

    bt = BehaviorTreeRunner()

    sig = SignalResult(
        hp_current=None,
        hp_max=None,
        hp_pct=None,
        mp_current=None,
        mp_max=None,
        mp_pct=None,
        low_hp=None,
        low_mp=None,
        paralyzed=None,
        haste_active=None,
        utamo_active=None,
        hungry=None,
        healing_trigger=False,
    )
    heal_cfg = HealingConfig(enabled=False, action="")

    reqs = bt.tick(
        sig=sig,
        healing_cfg=heal_cfg,
        target_cls="orc",
        target_conf=0.9,
        cavebot_next="",
        cavebot_action="",
        commit_flag=False,
        eat_food=False,
    )

    beeps = [r for r in reqs if r.kind == "beep"]
    assert beeps
    assert any(r.value == "target_reliable" for r in beeps)


def test_bt_beep_target_cooldown_blocks_rapid_changes(monkeypatch) -> None:
    monkeypatch.setenv("BT_BEEP_ON_TARGET", "1")
    monkeypatch.setenv("BT_BEEP_TARGET_COOLDOWN_S", "999")

    bt = BehaviorTreeRunner()

    sig = SignalResult(
        hp_current=None,
        hp_max=None,
        hp_pct=None,
        mp_current=None,
        mp_max=None,
        mp_pct=None,
        low_hp=None,
        low_mp=None,
        paralyzed=None,
        haste_active=None,
        utamo_active=None,
        hungry=None,
        healing_trigger=False,
    )
    heal_cfg = HealingConfig(enabled=False, action="")

    reqs1 = bt.tick(
        sig=sig,
        healing_cfg=heal_cfg,
        target_cls="orc",
        target_conf=0.9,
        cavebot_next="",
        cavebot_action="",
        commit_flag=False,
        eat_food=False,
    )
    assert any(r.kind == "beep" for r in reqs1)

    # Immediate different target should be blocked by cooldown.
    reqs2 = bt.tick(
        sig=sig,
        healing_cfg=heal_cfg,
        target_cls="dragon",
        target_conf=0.9,
        cavebot_next="",
        cavebot_action="",
        commit_flag=False,
        eat_food=False,
    )
    assert not any(r.kind == "beep" for r in reqs2)
