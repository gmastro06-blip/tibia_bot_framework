from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def _merge_keep_unknown(dst: dict[str, Any], src: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge src into dst, preserving unknown keys in dst."""

    for k, v in src.items():
        if isinstance(v, Mapping) and isinstance(dst.get(k), dict):
            dst[k] = _merge_keep_unknown(dict(dst[k]), v)  # type: ignore[arg-type]
        else:
            dst[k] = v
    return dst


def targeting_profiles_dir(*, repo_root: Path | None = None) -> Path:
    """Default profiles directory: <repo_root>/configs/targeting_profiles.

    If repo_root is None, resolves relative to current working directory.
    """

    base = Path(repo_root) if repo_root is not None else Path.cwd()
    return base / "configs" / "targeting_profiles"


def profile_path(name: str, *, repo_root: Path | None = None) -> Path:
    safe = (name or "").strip()
    if not safe:
        raise ValueError("profile name is empty")
    if any(ch in safe for ch in "\\/:*"):
        raise ValueError("invalid profile name")
    return targeting_profiles_dir(repo_root=repo_root) / f"{safe}.json"


def load_targeting_profile(path: Path) -> dict[str, Any]:
    """Load a targeting profile JSON file.

    Returns an empty dict if the file doesn't exist or is invalid.
    """

    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return dict(data) if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_targeting_profile(path: Path, profile: Mapping[str, Any]) -> None:
    """Save targeting profile as JSON preserving unknown keys already on disk."""

    existing: dict[str, Any] = {}
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = dict(raw)
    except Exception:
        existing = {}

    merged = _merge_keep_unknown(existing, profile)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
