from __future__ import annotations

import json
from pathlib import Path

from src.targeting_profiles import load_targeting_profile, save_targeting_profile


def test_targeting_profile_roundtrip_preserves_unknown_keys(tmp_path: Path) -> None:
    p = tmp_path / "p1.json"
    p.write_text(
        json.dumps({"unknown": 1, "nested": {"keep": 2, "x": 1}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    save_targeting_profile(p, {"nested": {"x": 3}, "rules": {"monsters": ["orc"]}})

    out = load_targeting_profile(p)
    assert out.get("unknown") == 1
    assert out.get("nested", {}).get("keep") == 2
    assert out.get("nested", {}).get("x") == 3
    assert out.get("rules", {}).get("monsters") == ["orc"]
