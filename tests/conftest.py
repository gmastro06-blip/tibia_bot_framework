from __future__ import annotations

import os
import sys


# Ensure tests can import both `src.*` and the repo's "flat" modules.
# Many modules live under `src/` but are imported as top-level (e.g. `import runtime_config`).
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)
