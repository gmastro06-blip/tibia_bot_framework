from __future__ import annotations

"""In-core entrypoint for the English ROI wizard.

The interactive ROI wizard originally lived under `tools/roi_wizard_english.py`.
This module provides a stable import/run surface under `src/`.

It dynamically loads and delegates to the tools implementation to avoid
forking logic and to preserve backwards compatibility.

Usage:
  python -m src.roi_wizard_english --help
"""

import importlib.util
import sys
from pathlib import Path
from typing import Iterable


def _load_tools_module():
    repo_root = Path(__file__).resolve().parents[1]
    tool_path = repo_root / "tools" / "roi_wizard_english.py"
    spec = importlib.util.spec_from_file_location("_tibia_tools_roi_wizard_english", str(tool_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load tool module from {tool_path}")

    mod = importlib.util.module_from_spec(spec)
    # Required for dataclasses/string-annotations and other import-time lookups.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def run(argv: Iterable[str] | None = None) -> int:
    """Run the wizard CLI and return an exit code."""

    mod = _load_tools_module()

    argv_list = None if argv is None else [str(x) for x in argv]
    main_fn = getattr(mod, "main", None)
    if not callable(main_fn):
        raise AttributeError("tools/roi_wizard_english.py has no callable main()")

    try:
        if argv_list is None:
            code = main_fn()
        else:
            # Tool uses argparse internally; provide argv by patching sys.argv.
            old_argv = list(sys.argv)
            try:
                sys.argv = [old_argv[0]] + argv_list
                code = main_fn()
            finally:
                sys.argv = old_argv
        try:
            return int(code or 0)
        except Exception:
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
