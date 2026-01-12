from __future__ import annotations

from bestiary.matcher import BestiaryMatchConfig, BestiaryMatcher, BestiaryRules


def test_match_dict_registry_with_corrections() -> None:
    reg = {"orc": {"hp": 150}, "dragon": {"hp": 5000}}
    cfg = BestiaryMatchConfig(reliable=92, probable=85, ambiguous_delta=5, len_tolerance=3, bucket_first_letter=True)
    corr = {"0rc": "orc"}
    m = BestiaryMatcher(reg, cfg=cfg, ocr_corrections=corr)

    r = m.match("0rc")
    assert r.name_key == "orc"
    assert r.tier in {"reliable", "probable"}
    assert r.ambiguous is False

    assert m.canonicalize("0rc") == "orc"
    assert m.payload_for("orc") == {"hp": 150}


def test_match_list_registry_format() -> None:
    reg = [{"name_key": "orc", "hp": 150}, {"name_key": "dragon", "hp": 5000}]
    m = BestiaryMatcher(reg)

    r = m.match("Dragon")
    assert r.name_key == "dragon"


def test_rules_alias_ignore_priority_parse() -> None:
    d = {
        "thresholds": {"reliable": 92, "probable": 85, "ambiguous_delta": 5},
        "indexing": {"len_tolerance": 3, "first_letter": True},
        "rules": {
            "aliases": {"0rc": "orc"},
            "ignore": ["dragon"],
            "priority": ["orc", "dragon"],
        },
    }

    rules = BestiaryRules.from_mapping(d)
    assert rules.aliases.get("0rc") == "orc"
    assert "dragon" in rules.ignore
    assert rules.priority == ("orc", "dragon")

    reg = {"orc": {}, "dragon": {}}
    m = BestiaryMatcher(reg, cfg=BestiaryMatchConfig.from_mapping(d), rules=rules)
    # alias
    assert m.canonicalize("0rc") == "orc"
    # ignore
    assert m.match("dragon").name_key is None


def test_ambiguous_blocks_canonicalize(monkeypatch) -> None:
    reg: dict[str, dict[str, object]] = {"orc": {}, "orc2": {}}
    cfg = BestiaryMatchConfig(reliable=92, probable=85, ambiguous_delta=5, len_tolerance=99, bucket_first_letter=False)
    m = BestiaryMatcher(reg, cfg=cfg)

    # Force a close top-2 where delta < ambiguous_delta.
    def fake_wratio(_q: str, cand: str) -> int:
        if cand.strip().lower() == "orc":
            return 90
        if cand.strip().lower() == "orc2":
            return 88
        return 0

    monkeypatch.setattr("bestiary.matcher.fuzz.WRatio", fake_wratio)

    r = m.match("orc")
    assert r.name_key == "orc"
    assert r.ambiguous is True
    assert r.tier == "probable"

    # Canonicalize is intentionally conservative.
    assert m.canonicalize("orc") is None
