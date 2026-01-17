from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.targeting_profiles import load_targeting_profile, profile_path, save_targeting_profile


def test_profile_path_validates_name(tmp_path) -> None:
    # Empty
    with pytest.raises(ValueError):
        profile_path("", repo_root=tmp_path)

    # Invalid characters
    for bad in ["a/b", "a\\b", "a:b", "a*b"]:
        with pytest.raises(ValueError):
            profile_path(bad, repo_root=tmp_path)

    # OK
    p = profile_path("p1", repo_root=tmp_path)
    assert p == tmp_path / "configs" / "targeting_profiles" / "p1.json"


def test_targeting_profile_roundtrip_preserves_unknown_keys(tmp_path: Path) -> None:
    p = profile_path("demo", repo_root=tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    seed = {
        "version": 1,
        "unknown_root": {"hello": "world"},
        "rules": {"monsters": ["orc"], "priority": ["orc"]},
        "nested": {"keep": {"x": 1}},
    }
    p.write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")

    # Save an update that doesn't mention unknown_root/nested.keep; those must remain.
    save_targeting_profile(
        p,
        {
            "rules": {"monsters": ["dragon"], "priority": ["dragon"]},
            "nested": {"known": True},
        },
    )

    out = load_targeting_profile(p)
    assert out.get("unknown_root") == {"hello": "world"}
    assert out.get("nested", {}).get("keep") == {"x": 1}

    # New keys should be applied.
    assert out.get("rules", {}).get("monsters") == ["dragon"]
    assert out.get("nested", {}).get("known") is True
