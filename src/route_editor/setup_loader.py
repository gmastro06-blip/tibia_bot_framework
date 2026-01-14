from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from route_editor.models import SetupConfig


def load_setup(path: str | Path) -> SetupConfig:
    p = Path(path)
    data: Dict[str, Any] = {}
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        data = {}
    return SetupConfig(raw=data)


def save_setup(cfg: SetupConfig, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Keep ordering stable with indent; allow non-ASCII if present.
    p.write_text(json.dumps(cfg.raw, ensure_ascii=False, indent=2), encoding="utf-8")
