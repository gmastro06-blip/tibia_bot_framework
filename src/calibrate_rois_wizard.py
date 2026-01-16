from __future__ import annotations

"""In-core entrypoint for the ROI calibration wizard.

The project historically shipped the interactive wizard under `tools/`.
Some integrations expect a stable module under `src/`.

This module is intentionally thin:
- It dynamically loads and delegates to `tools/calibrate_rois_wizard.py`.
- It provides a `run(argv)` helper that returns an exit code (test-friendly).

Usage:
  python -m src.calibrate_rois_wizard --help
"""

import importlib.util
import sys
from pathlib import Path
from typing import Iterable


def _load_tools_module():
    repo_root = Path(__file__).resolve().parents[1]
    tool_path = repo_root / "tools" / "calibrate_rois_wizard.py"
    spec = importlib.util.spec_from_file_location("_tibia_tools_calibrate_rois_wizard", str(tool_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load tool module from {tool_path}")
    mod = importlib.util.module_from_spec(spec)
    # Ensure the module is visible during execution. Some libraries (notably
    # `dataclasses` when processing string annotations) rely on
    # `sys.modules[__module__]` being present while the class body executes.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def run(argv: Iterable[str] | None = None) -> int:
    """Run the wizard CLI and return an exit code.

    This wraps the tool's `main()` which may call `sys.exit()`.
    """

    mod = _load_tools_module()

    if argv is None:
        argv_list = None
    else:
        argv_list = [str(x) for x in argv]

    # Delegate: prefer `main(argv)` if present, otherwise patch sys.argv.
    main_fn = getattr(mod, "main", None)
    if not callable(main_fn):
        raise AttributeError("tools/calibrate_rois_wizard.py has no callable main()")

    try:
        if argv_list is None:
            main_fn()
        else:
            # Tool currently uses argparse directly; provide argv if accepted.
            try:
                main_fn(argv_list)
            except TypeError:
                old_argv = list(sys.argv)
                try:
                    sys.argv = [old_argv[0]] + argv_list
                    main_fn()
                finally:
                    sys.argv = old_argv
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
