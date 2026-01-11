"""Deprecated: use tools/route_recorder.py.

This repo has a single route format: JSON list of {x,y,z?,name?,action?} consumed by
`navigation.route.load_route()`.

The previous version of this script wrote a custom text format and depended on
`pyperclip` (not part of the default Poetry deps). To keep the workflow consistent
and stable, this file now forwards to the unified route recorder tool.
"""

from __future__ import annotations

import sys


def main() -> int:
    print("⚠️  cave_recorder.py is deprecated.")
    print("➡️  Use: poetry run python tools/route_recorder.py --out configs/route.json")
    try:
        from tools.route_recorder import main as _new_main

        return int(_new_main())
    except Exception:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())