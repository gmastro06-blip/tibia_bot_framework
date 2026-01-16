from __future__ import annotations

"""In-core entrypoint for the panel-reference template setup tool.

Historically implemented as `tools/create_panel_refs.py`. This module provides
an equivalent `python -m src.create_panel_refs` entrypoint while delegating to
`tools/` to keep a single source of truth and preserve backwards compatibility.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Iterable


def _load_tools_module():
    repo_root = Path(__file__).resolve().parents[1]
    tool_path = repo_root / "tools" / "create_panel_refs.py"
    spec = importlib.util.spec_from_file_location("_tibia_tools_create_panel_refs", str(tool_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load tool module from {tool_path}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def run(argv: Iterable[str] | None = None) -> int:
    mod = _load_tools_module()

    argv_list = None if argv is None else [str(x) for x in argv]
    main_fn = getattr(mod, "main", None)
    if not callable(main_fn):
        raise AttributeError("tools/create_panel_refs.py has no callable main()")

    try:
        if argv_list is None:
            code = main_fn()
        else:
            old_argv = list(sys.argv)
            try:
                sys.argv = [old_argv[0]] + argv_list
                code = main_fn()
            finally:
                sys.argv = old_argv
        if code is None:
            return 0
        if isinstance(code, bool):
            return 1 if code else 0
        if isinstance(code, int):
            return int(code)
        if isinstance(code, float):
            return int(code)
        if isinstance(code, str):
            try:
                return int(float(code.strip() or "0"))
            except Exception:
                return 0
        return 0
    except SystemExit as e:
        code = getattr(e, "code", 0)
        try:
            return int(code)
        except Exception:
            return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
