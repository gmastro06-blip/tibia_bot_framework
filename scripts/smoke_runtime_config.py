from __future__ import annotations

import os
import sys
import threading
import time


def main() -> None:
    # When running as `python scripts/...`, Python sets sys.path[0] to `scripts/`.
    # Add repo root so `import src...` works reliably.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # Importing src.main first ensures its sys.path bootstrap runs,
    # so `runtime_config` (in src/) is importable as a top-level module.
    from src.main import run_bot
    from runtime_config import RuntimeConfig

    stop_event = threading.Event()
    runtime_config = RuntimeConfig()

    def _bot_runner() -> None:
        try:
            run_bot(stop_event=stop_event, runtime_config=runtime_config)
        except Exception as exc:
            # Make failures visible for CI / headless runs
            print(f"[smoke_runtime_config] run_bot crashed: {exc!r}")

    t = threading.Thread(target=_bot_runner, daemon=True)
    t.start()

    # Give threads a moment to start.
    time.sleep(2.0)

    print("[smoke_runtime_config] Toggling Healing/Cavebot...")

    runtime_config.update_healing(enabled=True, hp_below_pct=90, mp_below_pct=90, action="exura")
    time.sleep(1.5)

    runtime_config.update_healing(enabled=False)
    time.sleep(1.5)

    runtime_config.update_cavebot(enabled=True, route_path="configs/route.json")
    time.sleep(1.5)

    runtime_config.update_cavebot(enabled=False)
    time.sleep(1.0)

    runtime_config.update_healing(enabled=True, hp_below_pct=70, mp_below_pct=40, action="exura")
    time.sleep(1.5)

    runtime_config.update_healing(enabled=False)
    time.sleep(0.5)

    print("[smoke_runtime_config] Stopping...")
    stop_event.set()
    t.join(timeout=5.0)

    print("[smoke_runtime_config] Done")


if __name__ == "__main__":
    main()
